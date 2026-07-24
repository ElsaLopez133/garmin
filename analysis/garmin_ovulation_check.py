"""
Is ovulation detectable from RHR / HRV alone?

For each cycle we take the estimated ovulation day (= the last day of Garmin's
predicted fertile window, i.e. the Follicular->Luteal boundary; the anchor phases
in cycle_phase are period-relative windows, not the true boundary), align every
day relative to it (day 0 = ovulation),
and average RHR and HRV across all cycles at each relative day. If the curves
visibly *step* at day 0, the transition is detectable; the script also quantifies
the pre- vs post-ovulation difference with a paired test across cycles.

Run:
  python garmin_ovulation_check.py [path/to/csv]
  (no arg -> prompts for a day count, like the other scripts)
"""

import os
import sys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# How many days each side of ovulation to analyse
WINDOW = 12
# Pre / post windows used for the quantitative step test (days relative to ovulation)
PRE = (-7, -2)
POST = (2, 7)
METRICS = [("resting_hr", "Resting HR (bpm)"), ("hrv_rmssd", "HRV (rMSSD)")]


def load_csv():
    if len(sys.argv) > 1:
        file_name = sys.argv[1]
        label = os.path.splitext(os.path.basename(file_name))[0]
        out_dir = os.path.join("figs", "cycle_checks")
    else:
        days = int(input("How many days of data? (e.g. 730): "))
        file_name = f"./data/garmin_data_{days}days/garmin_data_{days}days.csv"
        label = f"garmin_data_{days}days"
        out_dir = f"./figs/garmin_data_{days}days"
    if not os.path.exists(file_name):
        sys.exit(f"❌ File not found: {file_name}")
    df = pd.read_csv(file_name)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    os.makedirs(out_dir, exist_ok=True)
    return df, os.path.join(out_dir, f"ovulation_check_{label}.jpg"), label


def find_cycles(df):
    """Return list of (cycle_start, ovulation_date, next_start) for cycles with a fertile window.

    Ovulation is anchored to the last day of Garmin's predicted fertile window
    (garmin_predicted_fertile), since the period-anchored cycle_phase labels do not
    mark the true Follicular->Luteal boundary.
    """
    is_period = df["is_period"] if "is_period" in df.columns else df["cycle_phase"] == "Period"
    starts = list(df.loc[is_period & ~is_period.shift(1, fill_value=False), "date"])
    cycles = []
    for i, start in enumerate(starts):
        next_start = starts[i + 1] if i + 1 < len(starts) else df["date"].max() + pd.Timedelta(days=1)
        block = df[(df["date"] >= start) & (df["date"] < next_start)]
        fertile = block[block["garmin_predicted_fertile"] == True]  # noqa: E712 (bool column)
        if fertile.empty:
            continue  # no ovulation anchor in this cycle
        ovulation = fertile["date"].max()  # ovulation ~ last day of the fertile window
        cycles.append((start, ovulation, next_start))
    return cycles


def build_aligned(df, cycles):
    """Long frame: one row per (cycle, relative_day) with metric values."""
    rows = []
    for cid, (start, ov, next_start) in enumerate(cycles):
        block = df[(df["date"] >= start) & (df["date"] < next_start)].copy()
        block["rel_day"] = (block["date"] - ov).dt.days
        block = block[block["rel_day"].between(-WINDOW, WINDOW)]
        for _, r in block.iterrows():
            rows.append({"cycle": cid, "rel_day": int(r["rel_day"]),
                         **{m: r.get(m) for m, _ in METRICS}})
    return pd.DataFrame(rows)


def quantify(aligned, metric):
    """Paired pre-vs-post step test across cycles."""
    pre_mask = aligned["rel_day"].between(*PRE)
    post_mask = aligned["rel_day"].between(*POST)
    pre = aligned[pre_mask].groupby("cycle")[metric].mean()
    post = aligned[post_mask].groupby("cycle")[metric].mean()
    paired = pd.concat([pre, post], axis=1, keys=["pre", "post"]).dropna()
    if len(paired) < 2:
        return None
    diff = paired["post"] - paired["pre"]
    result = {
        "n_cycles": len(paired),
        "pre_mean": paired["pre"].mean(),
        "post_mean": paired["post"].mean(),
        "delta": diff.mean(),
        "cohens_d": diff.mean() / diff.std(ddof=1) if diff.std(ddof=1) else np.nan,
    }
    try:
        from scipy.stats import ttest_rel
        result["p_value"] = ttest_rel(paired["post"], paired["pre"]).pvalue
    except Exception:
        result["p_value"] = None
    return result


def main():
    df, out_path, label = load_csv()
    for col in ("cycle_phase", "garmin_predicted_fertile"):
        if col not in df.columns:
            sys.exit(f"❌ No '{col}' column.")

    cycles = find_cycles(df)
    print(f"\n========== Ovulation detectability: {label} ==========")
    print(f"Usable cycles (with a fertile window): {len(cycles)}")
    if len(cycles) < 2:
        sys.exit("Not enough cycles to analyse.")

    aligned = build_aligned(df, cycles)

    fig, axes = plt.subplots(len(METRICS), 1, figsize=(11, 8), sharex=True)
    for ax, (metric, ylabel) in zip(axes, METRICS):
        grp = aligned.groupby("rel_day")[metric]
        mean = grp.mean()
        sem = grp.sem()
        ax.axvspan(-WINDOW, 0, color="yellow", alpha=0.15, label="Follicular side")
        ax.axvspan(0, WINDOW, color="pink", alpha=0.25, label="Luteal side")
        ax.axvline(0, color="green", linestyle="--", linewidth=1.5, label="Estimated ovulation")
        ax.plot(mean.index, mean.values, color="black", marker="o", markersize=3)
        ax.fill_between(mean.index, mean - sem, mean + sem, color="gray", alpha=0.3)
        ax.set_ylabel(ylabel)
        ax.grid(alpha=0.3)

        res = quantify(aligned, metric)
        if res:
            p = f"{res['p_value']:.3g}" if res["p_value"] is not None else "n/a"
            txt = (f"pre {res['pre_mean']:.1f} -> post {res['post_mean']:.1f}  "
                   f"(Δ{res['delta']:+.2f}, d={res['cohens_d']:.2f}, p={p}, n={res['n_cycles']})")
            ax.set_title(txt, fontsize=10)
            print(f"\n{metric}:")
            print(f"  pre-ovulation  mean ({PRE[0]}..{PRE[1]} d): {res['pre_mean']:.2f}")
            print(f"  post-ovulation mean ({POST[0]}..{POST[1]} d): {res['post_mean']:.2f}")
            print(f"  step Δ = {res['delta']:+.2f}   Cohen's d = {res['cohens_d']:.2f}   p = {p}   ({res['n_cycles']} cycles)")

    axes[-1].set_xlabel("Days relative to estimated ovulation (0 = Follicular→Luteal boundary)")
    axes[0].legend(loc="upper left", fontsize=8)
    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    print(f"\n💾 Saved plot to {out_path}")
    print("\nInterpretation: a clear step at day 0 (RHR up, HRV down) with |d|>0.5 and small p")
    print("means the Follicular→Luteal transition is detectable from these signals alone.")


if __name__ == "__main__":
    main()
