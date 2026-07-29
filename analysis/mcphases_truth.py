"""Ground-truth + biometric prep for validating cycle models on the mcPHASES dataset.

mcPHASES (PhysioNet, https://physionet.org/content/mcphases/) pairs Fitbit biometrics
with Mira hormone assays (LH, estrogen/E3G, PdG) and daily self-report for 42
participants over ~90-day intervals. It provides a *hormonal* ovulation ground truth --
the LH surge -- so the HSMM boundary that on Garmin data we could only check against
Garmin's own calendar rule can be scored against real ovulation.

Two products:

- :func:`build_daily_table` -- one row per ``(id, study_interval, day_in_study)`` with the
  Fitbit signals mapped to our Garmin schema, plus phase / flow / hormones.
- :func:`extract_ovulation_truth` -- one row per complete cycle with the LH-peak ovulation
  day (gated by a surge test) and the phase-label luteal onset, to score a model against.

Fitbit -> Garmin signal map (see README):

===========================  ===============================================
our Garmin signal            mcPHASES source (daily aggregation)
===========================  ===============================================
``resting_hr``               ``resting_heart_rate.value``
``hrv_rmssd``                nightly mean of ``heart_rate_variability_details.rmssd``
``avg_sleep_respiration``    ``respiratory_rate_summary.full_sleep_breathing_rate``
``skin_temp`` (bonus)        ``computed_temperature.nightly_temperature``
===========================  ===============================================

Garmin's ``avg_sleep_stress`` has no clean Fitbit equivalent (Fitbit's stress score is a
different, inverted construct) and is deliberately omitted.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

KEY = ["id", "study_interval", "day_in_study"]
# Garmin signals reproducible from Fitbit (avg_sleep_stress intentionally absent).
SIGNALS = ["resting_hr", "hrv_rmssd", "avg_sleep_respiration"]


def _daily_mean(path: Path, col: str, new: str) -> pd.DataFrame:
    """Collapse an intraday / duplicated table to one mean value per day-key.

    The Fitbit vitals here (resting HR, HRV rmssd, breathing rate) are strictly
    positive; the export encodes missing readings as ``0`` (resting HR marks them
    with ``error == 0`` too). Those sentinels are dropped before averaging -- left in,
    a single ``0`` row halved a day's mean (~10% of resting-HR rows are zeros), which
    corrupted the daily signal and inverted the follicular/luteal RHR contrast.
    """
    d = pd.read_csv(path, usecols=KEY + [col])
    d[col] = pd.to_numeric(d[col], errors="coerce")
    d = d[d[col] > 0]
    return d.groupby(KEY, as_index=False)[col].mean().rename(columns={col: new})


def build_daily_table(mcphases_dir, cache_path=None, include_temp: bool = True) -> pd.DataFrame:
    """Merge the hormone/self-report truth with the mapped Fitbit signals.

    Returns one row per ``(id, study_interval, day_in_study)``. Biometric tables are
    deduplicated to one value per day (mean) before the left-join onto the hormone
    days, so the row count equals the hormone table's.
    """
    mcphases_dir = Path(mcphases_dir)
    h = pd.read_csv(mcphases_dir / "hormones_and_selfreport.csv")
    for c in ("lh", "pdg", "estrogen"):
        h[c] = pd.to_numeric(h[c], errors="coerce")
    m = h[KEY + ["phase", "flow_volume", "lh", "pdg", "estrogen"]].copy()

    m = m.merge(_daily_mean(mcphases_dir / "resting_heart_rate.csv", "value", "resting_hr"),
                on=KEY, how="left")
    m = m.merge(_daily_mean(mcphases_dir / "heart_rate_variability_details.csv", "rmssd", "hrv_rmssd"),
                on=KEY, how="left")
    m = m.merge(_daily_mean(mcphases_dir / "respiratory_rate_summary.csv",
                            "full_sleep_breathing_rate", "avg_sleep_respiration"),
                on=KEY, how="left")

    temp_path = mcphases_dir / "computed_temperature.csv"
    if include_temp and temp_path.exists():
        t = pd.read_csv(temp_path, usecols=["id", "study_interval",
                                            "sleep_start_day_in_study", "nightly_temperature"])
        t = (t.groupby(["id", "study_interval", "sleep_start_day_in_study"], as_index=False)
               ["nightly_temperature"].mean()
               .rename(columns={"sleep_start_day_in_study": "day_in_study",
                                "nightly_temperature": "skin_temp"}))
        m = m.merge(t, on=KEY, how="left")

    m = m.sort_values(KEY).reset_index(drop=True)
    if cache_path is not None:
        cache_path = Path(cache_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        m.to_csv(cache_path, index=False)
    return m


def add_cycle_structure(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``is_period, cycle_id, cycle_day, cycle_len, period_end`` per participant-interval.

    Cycles are segmented at the onset of the ``Menstrual`` phase label (the study's own,
    hormone-informed menses). The leading partial cycle before a participant's first
    logged menses is dropped (``cycle_id`` starts at 1).
    """
    out = []
    for _, g in df.groupby(["id", "study_interval"], sort=False):
        g = g.sort_values("day_in_study").reset_index(drop=True)
        ip = (g["phase"] == "Menstrual").to_numpy()
        onset = ip & ~np.r_[False, ip[:-1]]
        g["is_period"] = ip
        g["cycle_id"] = onset.cumsum()

        first = g.groupby("cycle_id")["day_in_study"].min()
        g["cycle_day"] = g["day_in_study"] - g["cycle_id"].map(first)
        cyc_len = (first.shift(-1) - first)
        g["cycle_len"] = g["cycle_id"].map(cyc_len).astype("Float64")

        pe = g.loc[g["is_period"]].groupby("cycle_id")["cycle_day"].max() + 1
        g["period_end"] = g["cycle_id"].map(pe)

        out.append(g[g["cycle_id"] > 0])
    return pd.concat(out, ignore_index=True)


def extract_ovulation_truth(df: pd.DataFrame, lh_abs: float = 20.0,
                            lh_prom: float = 2.5) -> pd.DataFrame:
    """One row per complete cycle with the hormonal ovulation day.

    Ovulation is taken as the **LH-peak day** among the cycle's non-menstrual days,
    accepted (``confident=True``) only when the peak clears an absolute threshold
    (``lh_abs`` mIU/mL) *and* stands ``lh_prom``x above the cycle's median LH -- a
    surge test that rejects flat/low-coverage cycles. ``luteal_onset`` is the first
    ``Luteal``-labelled cycle day (a "first luteal day" anchor comparable to a model's
    boundary); it typically trails the LH peak by ~2-3 days (the biometric lag).
    """
    rows = []
    for (pid, iv, cid), gc in df.groupby(["id", "study_interval", "cycle_id"], sort=False):
        cl = gc["cycle_len"].iloc[0]
        if pd.isna(cl):                      # incomplete final cycle
            continue
        gc = gc.sort_values("cycle_day")
        rec = {"id": pid, "study_interval": iv, "cycle_id": cid,
               "cycle_len": int(cl), "confident": False}
        lh = gc.loc[~gc["is_period"]].set_index("cycle_day")["lh"].dropna()
        if not lh.empty:
            peak_day, peak = int(lh.idxmax()), float(lh.max())
            base = float(gc["lh"].median())
            prom = peak / base if base and base > 0 else np.inf
            lut = gc.loc[gc["phase"] == "Luteal", "cycle_day"]
            rec.update(ov_day=peak_day, lh_peak=round(peak, 1), lh_prominence=round(prom, 1),
                       confident=bool(peak >= lh_abs and prom >= lh_prom),
                       luteal_onset=int(lut.min()) if not lut.empty else np.nan,
                       luteal_len=int(cl) - peak_day)
        rows.append(rec)
    return pd.DataFrame(rows)
