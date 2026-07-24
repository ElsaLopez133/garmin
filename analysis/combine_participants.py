"""
Combine downloaded participant CSVs into one dataset + a per-participant QC table.

Reads every CSV in a folder (default data/participants/, as produced by
collector/fetch_uploads.py), stacks them, prints a triage table, and writes:
  data/combined/all_participants.csv      - everything stacked
  data/combined/ovulation_eligible.csv    - only participants usable for ovulation work
  data/combined/participant_summary.csv   - the QC table

Run:
    python analysis/combine_participants.py            # uses data/participants/
    python analysis/combine_participants.py somedir/
"""

import glob
import os
import sys

import pandas as pd

IN_DIR = sys.argv[1] if len(sys.argv) > 1 else "data/participants"
OUT_DIR = "data/combined"

# Contraception answers that suppress ovulation -> exclude from ovulation analysis.
HORMONAL = {"Hormonal pill", "Hormonal IUD", "Implant / injection / ring"}


def cycle_stats(df):
    """Return (n_cycles, avg_cycle_length, pct_phase_logged)."""
    if "cycle_phase" not in df.columns:
        return 0, None, 0.0
    is_period = df["is_period"] if "is_period" in df.columns else df["cycle_phase"] == "Period"
    pct_logged = ((df["cycle_phase"] != "Not logged") | is_period).mean() * 100
    starts = list(df.loc[is_period & ~is_period.shift(1, fill_value=False), "date"])
    if len(starts) >= 2:
        lengths = [(starts[i + 1] - starts[i]).days for i in range(len(starts) - 1)]
        avg = round(sum(lengths) / len(lengths), 1)
    else:
        avg = None
    return len(starts), avg, round(pct_logged, 1)


def first(df, col, default="?"):
    return df[col].iloc[0] if col in df.columns and len(df) else default


def main():
    files = sorted(glob.glob(os.path.join(IN_DIR, "*.csv")))
    if not files:
        sys.exit(f"No CSVs found in {IN_DIR}/ — run collector/fetch_uploads.py first.")

    frames, rows = [], []
    for fp in files:
        df = pd.read_csv(fp)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True) if "date" in df.columns else df

        pid = first(df, "participant_id", os.path.splitext(os.path.basename(fp))[0])
        if "participant_id" not in df.columns:
            df["participant_id"] = pid  # ensure every row is labelled
        frames.append(df)

        n_cyc, avg_len, pct_logged = cycle_stats(df)
        contraception = first(df, "contraception")
        rhr_cov = round(df["resting_hr"].notna().mean() * 100, 1) if "resting_hr" in df.columns else 0.0
        hormonal = contraception in HORMONAL
        usable = (not hormonal) and n_cyc >= 2 and rhr_cov > 50

        rows.append({
            "participant_id": pid,
            "days": len(df),
            "cycles": n_cyc,
            "avg_cycle_len": avg_len,
            "phase_logged_%": pct_logged,
            "rhr_coverage_%": rhr_cov,
            "contraception": contraception,
            "ovulation_usable": "yes" if usable else "no",
        })

    combined = pd.concat(frames, ignore_index=True)
    qc = pd.DataFrame(rows)

    print(f"\nCombined {len(files)} participant file(s), {len(combined)} total rows.\n")
    print(qc.to_string(index=False))

    n_usable = (qc["ovulation_usable"] == "yes").sum()
    n_hormonal = qc["contraception"].isin(HORMONAL).sum()
    print(f"\nOvulation-usable participants: {n_usable}/{len(qc)} "
          f"(excluded {n_hormonal} on hormonal contraception, plus any with too few cycles / sparse RHR)")

    os.makedirs(OUT_DIR, exist_ok=True)
    combined.to_csv(os.path.join(OUT_DIR, "all_participants.csv"), index=False)
    qc.to_csv(os.path.join(OUT_DIR, "participant_summary.csv"), index=False)

    eligible_ids = set(qc.loc[qc["ovulation_usable"] == "yes", "participant_id"])
    combined[combined["participant_id"].isin(eligible_ids)].to_csv(
        os.path.join(OUT_DIR, "ovulation_eligible.csv"), index=False)

    print(f"\n✅ Wrote:")
    print(f"   {OUT_DIR}/all_participants.csv")
    print(f"   {OUT_DIR}/ovulation_eligible.csv")
    print(f"   {OUT_DIR}/participant_summary.csv")


if __name__ == "__main__":
    main()
