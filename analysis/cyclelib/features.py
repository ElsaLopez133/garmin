"""Feature standardization shared by the HSMM notebooks.

Robust (median / MAD) standardization removes cross-signal scale so the pooled
emission parameters describe a shared follicular<->luteal *shape*. Two variants:

- :func:`robust_standardize` -- one median/MAD over the whole record (single-person
  Garmin data).
- :func:`standardize_per_group` -- median/MAD within each participant-interval
  (multi-person mcPHASES data), the design intended for the future Garmin collector.
"""
from __future__ import annotations

import numpy as np

MAD_SCALE = 1.4826   # MAD -> std-equivalent for a normal


def robust_standardize(df, signals, out_prefix="z_"):
    """Add ``z_<signal>`` columns standardized by a single global median/MAD.

    Signals are interpolated (both directions) before the location/scale estimate,
    matching the ``02_model_hsmm`` observation matrix. Mutates and returns ``df``.
    """
    X = df[signals].interpolate(limit_direction="both").to_numpy(float)
    med = np.median(X, 0)
    mad = np.median(np.abs(X - med), 0) * MAD_SCALE
    mad[mad == 0] = 1.0
    Z = (X - med) / mad
    for j, s in enumerate(signals):
        df[f"{out_prefix}{s}"] = Z[:, j]
    return df


def standardize_per_group(df, signals, group_cols, out_prefix="z_"):
    """Add ``z_<signal>`` columns standardized within each group.

    ``group_cols`` selects the per-person unit (e.g. ``["id", "study_interval"]``).
    Each block is interpolated, then centred on its median and scaled by its MAD
    (zero-MAD signals fall back to 1). Returns a copy.
    """
    df = df.copy()
    for s in signals:
        df[f"{out_prefix}{s}"] = np.nan
    for _, idx in df.groupby(group_cols, sort=False).groups.items():
        block = df.loc[idx, signals].interpolate(limit_direction="both")
        med = block.median()
        mad = ((block - med).abs().median() * MAD_SCALE).replace(0, 1.0)
        for s in signals:
            df.loc[idx, f"{out_prefix}{s}"] = (block[s] - med[s]) / mad[s]
    return df
