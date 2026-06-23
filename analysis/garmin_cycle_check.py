"""
Quick verification of retrieved menstrual-cycle data.

Two ways to run it:
  1. Point it at any CSV (e.g. a participant upload):
        python garmin_cycle_check.py path/to/garmin_data_xxxx.csv
  2. No argument -> prompts for a day count and reads
        data/garmin_data_{N}days/garmin_data_{N}days.csv

It prints a text sanity-check (phase counts, detected cycle starts and their
lengths, gaps) and saves a plot of the cycle phases with RHR + HRV overlaid, so
you can eyeball whether the phases line up with the biometrics.
"""

import os
import sys

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import pandas as pd

# Same colors as garmin_plot.py
PHASE_COLORS = {
    "Menstrual": "red",
    "Follicular": "yellow",
    "Fertile": "green",
    "Luteal": "pink",
}


def load_csv():
    """Return (dataframe, output_path_for_figure, label)."""
    if len(sys.argv) > 1:
        file_name = sys.argv[1]
        if not os.path.exists(file_name):
            sys.exit(f"❌ File not found: {file_name}")
        label = os.path.splitext(os.path.basename(file_name))[0]
        out_dir = os.path.join("figs", "cycle_checks")
    else:
        while True:
            try:
                days = int(input("How many days of data do you want to check? (e.g. 90, 180): "))
                if days <= 0:
                    raise ValueError
                break
            except ValueError:
                print("⚠️ Enter a positive number (the data must already exist).")
        file_name = f"./data/garmin_data_{days}days/garmin_data_{days}days.csv"
        if not os.path.exists(file_name):
            sys.exit(f"❌ No data found at {file_name} — download it first.")
        label = f"garmin_data_{days}days"
        out_dir = f"./figs/garmin_data_{days}days"

    df = pd.read_csv(file_name)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    os.makedirs(out_dir, exist_ok=True)
    return df, os.path.join(out_dir, f"cycle_check_{label}.jpg"), label


def text_summary(df, label):
    print(f"\n========== Cycle check: {label} ==========")
    print(f"Date range : {df['date'].min().date()} -> {df['date'].max().date()}  ({len(df)} days)")

    if "cycle_phase" not in df.columns:
        print("⚠️ No 'cycle_phase' column found — cannot verify cycle data.")
        return

    counts = df["cycle_phase"].value_counts(dropna=False)
    print("\nPhase day counts:")
    for phase, n in counts.items():
        print(f"  {str(phase):12s} {n:4d}  ({n / len(df) * 100:4.1f}%)")

    logged = df[~df["cycle_phase"].isin(["Not logged"]) & df["cycle_phase"].notna()]
    if logged.empty:
        print("\n⚠️ No logged cycle phases at all — cycle data was NOT retrieved.")
        return

    # Detect cycle starts = first day of each Menstrual run
    is_menstrual = df["cycle_phase"] == "Menstrual"
    starts = df.loc[is_menstrual & ~is_menstrual.shift(1, fill_value=False), "date"]
    starts = list(starts)
    print(f"\nDetected {len(starts)} cycle start(s) (first day of menstruation):")
    for i, s in enumerate(starts):
        if i + 1 < len(starts):
            length = (starts[i + 1] - s).days
            print(f"  {s.date()}  ->  cycle length {length} days")
        else:
            print(f"  {s.date()}  (most recent / open cycle)")

    if len(starts) >= 2:
        lengths = [(starts[i + 1] - starts[i]).days for i in range(len(starts) - 1)]
        avg = sum(lengths) / len(lengths)
        print(f"\nAverage cycle length: {avg:.1f} days (range {min(lengths)}–{max(lengths)})")
        odd = [l for l in lengths if l < 21 or l > 40]
        if odd:
            print(f"⚠️ Suspicious cycle length(s): {odd} — worth checking that source data.")

    # Per-phase mean biometrics — a quick physiological sanity check
    cols = [c for c in ["resting_hr", "hrv_rmssd", "avgStressLevel", "total_sleep_hr"] if c in df.columns]
    if cols:
        print("\nMean biometrics per phase (expect RHR slightly higher in Luteal):")
        means = logged.groupby("cycle_phase")[cols].mean().round(2)
        print(means.to_string())
    print("=" * (len(label) + 28))


def plot(df, out_path, label):
    has_phase = "cycle_phase" in df.columns
    fig, ax1 = plt.subplots(figsize=(14, 6))
    ax1.set_title(f"Menstrual cycle phases vs RHR / HRV — {label}")

    # Phase background bands
    if has_phase:
        for _, row in df.iterrows():
            color = PHASE_COLORS.get(row["cycle_phase"])
            if color:
                ax1.axvspan(row["date"], row["date"] + pd.Timedelta(days=1), color=color, alpha=0.35)

    # RHR on left axis
    if "resting_hr" in df.columns:
        ax1.plot(df["date"], df["resting_hr"], color="darkred", marker="o",
                 markersize=3, linewidth=1.5, label="Resting HR")
        ax1.set_ylabel("Resting HR (bpm)", color="darkred")
    ax1.grid(alpha=0.3)

    # HRV on right axis
    if "hrv_rmssd" in df.columns:
        ax2 = ax1.twinx()
        ax2.plot(df["date"], df["hrv_rmssd"], color="black", marker="x",
                 markersize=3, linewidth=1, alpha=0.8, label="HRV rMSSD")
        ax2.set_ylabel("HRV (rMSSD)", color="black")

    # Legend: phases + lines
    handles = [mpatches.Patch(color=c, alpha=0.35, label=p) for p, c in PHASE_COLORS.items()]
    lines, labels = ax1.get_legend_handles_labels()
    handles += lines
    ax1.legend(handles=handles, loc="upper left", ncol=3, fontsize=8)

    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    print(f"\n💾 Saved plot to {out_path}")


def main():
    df, out_path, label = load_csv()
    text_summary(df, label)
    plot(df, out_path, label)


if __name__ == "__main__":
    main()
