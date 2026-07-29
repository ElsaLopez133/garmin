"""Preprocessing: flag confounded days (illness / alcohol / travel / hard days).

Acute, non-cycle events shift the biometrics (RHR up, HRV down, respiration and
stress up, body battery drained) for a day or two and then recover. Left in, a
single such day can dominate a per-cycle change-point fit. This step adds a
``is_confounded`` flag plus a continuous ``confound_score`` so those days can be
down-weighted -- or set to NaN and left to imputation -- before modelling.

Design choices that matter more than the detector:

- **Detect on the residual, not the raw value.** The cycle itself is the largest
  legitimate source of variation (the luteal RHR rise is exactly what downstream
  code wants to keep). Every test below runs on the deviation from a *local
  rolling median*, which tracks the slow cycle drift, so a normal luteal rise is
  not flagged -- only abrupt departures from the personal trend are.
- **Flag, don't delete.** Rows are kept and labelled; ``apply_confound_mask`` can
  optionally NaN the signal values so the existing median imputation absorbs them
  without breaking calendar-day alignment.
- **Fit robustly.** Medians, MAD, and (when available) a Minimum Covariance
  Determinant estimator are used throughout, because the training data contains
  the very outliers being removed -- a plain mean/std would be inflated by them.

Two complementary detectors are combined:

1. **Hampel filter** -- per-signal rolling-median +/- ``n_sigmas`` * MAD spike
   test. Catches single-signal excursions.
2. **Robust Mahalanobis distance** -- one distance per day from the multivariate
   centre of the detrended residuals, so days where *several* signals move
   together (the illness signature) are caught even when no single signal is
   extreme enough on its own.

A day is flagged only when several signals agree: it trips the univariate test on
at least ``min_signals`` signals, *or* it is a multivariate outlier **and** at
least ``min_signals`` signals are moderately off (so one lone extreme signal never
flags a day on its own).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Signals that respond to acute confounders and are well covered in the data.
# Only those actually present (and non-empty) in the frame are used.
DEFAULT_SIGNALS = [
    "resting_hr",
    "hrv_rmssd",
    "avg_sleep_respiration",
    "avg_sleep_stress",
    "avgStressLevel",
]

_MAD_TO_SIGMA = 1.4826  # scales MAD to an SD estimate for a normal distribution


def _rolling_median(series: pd.Series, window: int) -> pd.Series:
    """Centred rolling median that tracks the slow (cycle) trend."""
    return series.rolling(window, center=True, min_periods=max(3, window // 2)).median()


def _mad(values: np.ndarray) -> float:
    """Median absolute deviation (0 -> tiny epsilon to avoid divide-by-zero)."""
    med = np.nanmedian(values)
    mad = np.nanmedian(np.abs(values - med))
    return float(mad) if mad > 0 else 1e-9


def hampel_residual(series: pd.Series, window: int, n_sigmas: float):
    """Return ``(flags, z)`` for a signal.

    ``z`` is the robust standardized residual from the local rolling median;
    ``flags`` marks days where ``|z| > n_sigmas``. NaN inputs never flag.
    """
    resid = series - _rolling_median(series, window)
    scale = _MAD_TO_SIGMA * _mad(resid.to_numpy())
    z = resid / scale
    flags = z.abs() > n_sigmas
    return flags.fillna(False), z


def _robust_covariance(resid: np.ndarray):
    """Return ``(location, precision)`` of the residual matrix.

    Uses sklearn's Minimum Covariance Determinant when available; otherwise a
    pure-numpy fallback: median centre + covariance of the residuals winsorized
    at +/-3 robust-SD, so a handful of outliers cannot inflate the covariance.
    """
    try:  # preferred: MCD (runs in the project venv, which has sklearn)
        from sklearn.covariance import MinCovDet

        mcd = MinCovDet().fit(resid)
        return mcd.location_, np.linalg.pinv(mcd.covariance_)
    except Exception:
        loc = np.median(resid, axis=0)
        centred = resid - loc
        scale = _MAD_TO_SIGMA * np.median(np.abs(centred), axis=0)
        scale[scale == 0] = 1e-9
        clipped = np.clip(centred / scale, -3, 3) * scale  # winsorize, keep units
        cov = np.cov(clipped, rowvar=False)
        cov = np.atleast_2d(cov)
        return loc, np.linalg.pinv(cov)


def _mahalanobis(resid: np.ndarray):
    """Robust Mahalanobis distance of each row of the residual matrix."""
    loc, precision = _robust_covariance(resid)
    centred = resid - loc
    d2 = np.einsum("ij,jk,ik->i", centred, precision, centred)
    return np.sqrt(np.clip(d2, 0, None))


def flag_confounders(
    df: pd.DataFrame,
    signals=None,
    date_col: str = "date",
    window: int = 7,
    n_sigmas: float = 3.0,
    min_signals: int = 3,
    moderate_sigmas: float = 1.5,
    maha_k: float = 3.5,
) -> pd.DataFrame:
    """Return a copy of ``df`` with confounded-day columns added.

    Adds:

    - ``confound_score`` -- robust multivariate Mahalanobis distance (continuous;
      useful for confidence gating downstream).
    - ``n_signal_flags`` -- how many signals tripped the univariate Hampel test.
    - ``is_confounded``  -- boolean: multivariate trip *or* ``>= min_signals``
      univariate trips.

    Parameters
    ----------
    window : int
        Rolling-median window (days) that defines the local baseline.
    n_sigmas : float
        Hampel threshold in robust SDs.
    min_signals : int
        How many signals must agree before a day is flagged -- both for the strong
        (Hampel) count and for the moderate-deviation breadth that gates the
        multivariate test. A day where only one signal is off is never flagged.
    moderate_sigmas : float
        Threshold (robust SDs) for counting a signal as "moderately off" when
        gating the Mahalanobis flag by breadth.
    maha_k : float
        Multivariate threshold: flag when the Mahalanobis distance exceeds
        ``median + maha_k * MAD`` of the distances (a Hampel test on the distances
        themselves -- avoids a chi-squared table and needs no scipy).
    """
    present = [
        c for c in (signals or DEFAULT_SIGNALS)
        if c in df.columns and df[c].notna().any()
    ]
    if not present:
        raise ValueError(
            "None of the requested signal columns are present/populated: "
            f"{signals or DEFAULT_SIGNALS}"
        )

    out = df.copy()
    order = out.index

    work = out[[date_col, *present]].copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="raise")
    work = work.sort_values(date_col)

    # 1. Per-signal Hampel residuals + strong/moderate deviation counts.
    resid_cols = {}
    strong_count = pd.Series(0, index=work.index)
    moderate_count = pd.Series(0, index=work.index)
    for col in present:
        flags, z = hampel_residual(work[col], window, n_sigmas)
        resid_cols[col] = z.fillna(0.0)  # 0 == on baseline (missing -> no deviation)
        strong_count = strong_count + flags.astype(int)
        moderate_count = moderate_count + (z.abs() > moderate_sigmas).fillna(False).astype(int)

    # 2. Multivariate distance on the standardized residuals, gated by breadth so a
    #    single extreme signal cannot trip it -- only genuinely coordinated days
    #    (>= min_signals signals each at least moderately off) qualify.
    resid = np.column_stack([resid_cols[c].to_numpy() for c in present])
    distances = _mahalanobis(resid)
    dist = pd.Series(distances, index=work.index)
    maha_thresh = dist.median() + maha_k * _MAD_TO_SIGMA * _mad(dist.to_numpy())
    maha_flag = (dist > maha_thresh) & (moderate_count >= min_signals)

    is_confounded = maha_flag | (strong_count >= min_signals)

    out["confound_score"] = dist.reindex(order)
    out["n_signal_flags"] = strong_count.reindex(order).astype(int)
    out["is_confounded"] = is_confounded.reindex(order).fillna(False).astype(bool)
    return out


def apply_confound_mask(df, signals=None, flag_col: str = "is_confounded"):
    """Return a copy with signal values on flagged days set to NaN.

    Keeps every row (preserving calendar-day alignment); downstream median
    imputation / past-only rolling means then absorb the gaps.
    """
    cols = [
        c for c in (signals or DEFAULT_SIGNALS)
        if c in df.columns and df[c].notna().any()
    ]
    out = df.copy()
    mask = out[flag_col].astype(bool)
    out.loc[mask, cols] = np.nan
    return out


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", help="input CSV with date + biometric columns")
    parser.add_argument("-o", "--output", help="output CSV (default: stdout summary only)")
    parser.add_argument("-w", "--window", type=int, default=7)
    parser.add_argument("-n", "--n-sigmas", type=float, default=3.0)
    parser.add_argument("--min-signals", type=int, default=3)
    parser.add_argument("--moderate-sigmas", type=float, default=1.5)
    parser.add_argument("--maha-k", type=float, default=3.5)
    parser.add_argument("--mask", action="store_true",
                        help="NaN the signals on flagged days in the output CSV")
    args = parser.parse_args()

    df = pd.read_csv(args.csv)
    out = flag_confounders(
        df, window=args.window, n_sigmas=args.n_sigmas,
        min_signals=args.min_signals, moderate_sigmas=args.moderate_sigmas,
        maha_k=args.maha_k,
    )
    n = int(out["is_confounded"].sum())
    print(f"Flagged {n} / {len(out)} days ({n / len(out) * 100:.1f}%) as confounded.")
    flagged = out.loc[out["is_confounded"], ["date", "confound_score", "n_signal_flags"]]
    with pd.option_context("display.max_rows", None):
        print(flagged.to_string(index=False))

    if args.output:
        result = apply_confound_mask(out) if args.mask else out
        result.to_csv(args.output, index=False)
        print(f"\nWrote {args.output}" + (" (signals masked on flagged days)" if args.mask else ""))


if __name__ == "__main__":
    _main()
