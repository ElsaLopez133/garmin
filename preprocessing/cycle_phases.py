"""Derive reproductive phase anchors from raw menstrual-calendar booleans.

The collector writes only raw facts:

- ``is_period``: logged bleeding days.
- ``garmin_predicted_fertile``: Garmin's fertile-window prediction.

This preprocessing step creates ``cycle_phase`` for modelling. Period is not a
phase label; it remains the separate ``is_period`` event. Because menses is part
of the follicular phase, period days are labelled ``Early follicular``.
"""
from __future__ import annotations

from datetime import timedelta

import pandas as pd

DEFAULT_ANCHOR_DAYS = 5
PHASE_NOT_LOGGED = "Not logged"
PHASE_EARLY_FOLLICULAR = "Early follicular"
PHASE_LATE_LUTEAL = "Late luteal"


def _boolean_series(values: pd.Series) -> pd.Series:
    """Return a strict boolean Series for CSV-loaded bool-like values."""
    if values.dtype == bool:
        return values
    if values.dtype == object:
        return values.astype(str).str.strip().str.lower().isin({"true", "1", "yes"})
    return values.fillna(False).astype(bool)


def derive_cycle_phases(
    df: pd.DataFrame,
    anchor_days: int = DEFAULT_ANCHOR_DAYS,
    date_col: str = "date",
    period_col: str = "is_period",
    phase_col: str = "cycle_phase",
) -> pd.DataFrame:
    """Return a copy of ``df`` with reproductive phase anchors.

    ``cycle_phase`` is rebuilt from scratch:

    - ``Early follicular``: first ``anchor_days`` days from each period start,
      including period days.
    - ``Late luteal``: last ``anchor_days`` days before each next period start.
    - ``Not logged``: the unlabeled middle.

    The first cycle cannot receive a late-luteal anchor before its first observed
    period. Short-cycle overlaps are resolved by preserving early-follicular
    labels first and assigning late-luteal labels only to still-unlabeled days.
    """
    if anchor_days <= 0:
        raise ValueError("anchor_days must be positive")
    missing = [col for col in (date_col, period_col) if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required column(s): {', '.join(missing)}")

    out = df.copy()
    order = out.index

    work = out[[date_col, period_col]].copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="raise").dt.date
    work[period_col] = _boolean_series(work[period_col])
    work = work.sort_values(date_col)

    dates = work[date_col].tolist()
    is_period = work[period_col].tolist()
    date_pos = {day: i for i, day in enumerate(dates)}

    phase = [PHASE_NOT_LOGGED] * len(work)
    is_follicular_anchor = [False] * len(work)
    is_luteal_anchor = [False] * len(work)

    period_starts = [
        i for i, period in enumerate(is_period)
        if period and (i == 0 or not is_period[i - 1])
    ]

    for start_idx in period_starts:
        start = dates[start_idx]
        for offset in range(anchor_days):
            idx = date_pos.get(start + timedelta(days=offset))
            if idx is None:
                continue
            phase[idx] = PHASE_EARLY_FOLLICULAR
            is_follicular_anchor[idx] = True

    for start_idx in period_starts[1:]:
        start = dates[start_idx]
        for offset in range(1, anchor_days + 1):
            idx = date_pos.get(start - timedelta(days=offset))
            if idx is None or phase[idx] != PHASE_NOT_LOGGED:
                continue
            phase[idx] = PHASE_LATE_LUTEAL
            is_luteal_anchor[idx] = True

    work[phase_col] = phase
    work["is_follicular_anchor"] = is_follicular_anchor
    work["is_luteal_anchor"] = is_luteal_anchor

    for col in (period_col, phase_col, "is_follicular_anchor", "is_luteal_anchor"):
        out[col] = work[col].reindex(order)
    for col in (period_col, "is_follicular_anchor", "is_luteal_anchor"):
        out[col] = out[col].astype(bool)

    return out


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", help="input CSV with date and is_period columns")
    parser.add_argument("-o", "--output", help="output CSV (default: overwrite input)")
    parser.add_argument(
        "-a", "--anchor-days",
        type=int,
        default=DEFAULT_ANCHOR_DAYS,
        help=f"anchor window length (default {DEFAULT_ANCHOR_DAYS})",
    )
    args = parser.parse_args()

    df = pd.read_csv(args.csv)
    out = derive_cycle_phases(df, anchor_days=args.anchor_days)
    out.to_csv(args.output or args.csv, index=False)

    print(out["cycle_phase"].value_counts(dropna=False).to_string())
    print("\nis_period")
    print(out["is_period"].value_counts(dropna=False).to_string())


if __name__ == "__main__":
    _main()
