"""Emission models for the one-boundary HSMM.

Two interchangeable ways to turn a day's standardized signals into the
``(logp_foll, logp_lut)`` scores that :func:`cyclelib.hsmm.boundary_search`
consumes:

- **Generative** (:func:`cyclelib.hsmm.fit_gaussian`): one multivariate Gaussian
  per state.
- **Discriminative** (:func:`fit_discriminative` + :func:`discriminative_logscores`):
  a logistic regression ``P(luteal | x)`` whose log-posterior becomes the emission.

Both are trained from the same follicular / luteal **anchor rows**
(:func:`anchor_rows`), so any difference between them is the emission model, not the
features.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

EPS = 1e-4


@dataclass(frozen=True)
class AnchorConfig:
    """Which days seed the follicular / luteal emissions.

    Follicular anchor: non-period days in ``[period_end, min(period_end + foll_width,
    cycle_len - foll_guard))`` -- the post-menstrual nadir, with ``foll_guard``
    keeping it off early-luteal on short cycles. Luteal anchor: the last
    ``lut_width`` non-period days before the next menses.
    """
    foll_width: int = 6
    foll_guard: int = 16
    lut_width: int = 6


def anchor_rows(cycles, signals, anchor=AnchorConfig()):
    """Stack follicular / luteal anchor-day feature rows across cycles.

    ``cycles`` is an iterable of per-cycle DataFrames (each already carrying the
    standardized ``z_<signal>`` columns, ``cycle_day``, ``is_period``, ``cycle_len``
    and ``period_end``). Returns ``(F, L)`` arrays of shape ``(n, D)``.
    """
    zcols = [f"z_{s}" for s in signals]
    D = len(signals)
    F, L = [], []
    for gc in cycles:
        cl = int(gc["cycle_len"].iloc[0])
        pe = int(gc["period_end"].iloc[0])
        cd = gc["cycle_day"].to_numpy()
        ip = gc["is_period"].to_numpy()
        Z = gc[zcols].to_numpy()
        hi = min(pe + anchor.foll_width, cl - anchor.foll_guard)
        F.append(Z[(~ip) & (cd >= pe) & (cd < hi)])
        L.append(Z[(~ip) & (cd >= cl - anchor.lut_width) & (cd < cl)])
    stack = lambda a: np.vstack(a) if a else np.empty((0, D))
    return stack(F), stack(L)


def clean_anchors(F, L):
    """Drop rows with any NaN feature from both anchor stacks."""
    F = np.asarray(F, float)
    L = np.asarray(L, float)
    return F[~np.isnan(F).any(1)], L[~np.isnan(L).any(1)]


def fit_discriminative(F, L):
    """Logistic ``P(luteal | z)`` from follicular / luteal anchors.

    Returns a fitted sklearn pipeline, or ``None`` if either side has fewer than
    ``D + 2`` clean rows. Imported lazily so :mod:`cyclelib.hsmm` stays free of a
    hard sklearn dependency.
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline

    F, L = clean_anchors(F, L)
    D = F.shape[1]
    if len(F) < D + 2 or len(L) < D + 2:
        return None
    X = np.vstack([F, L])
    y = np.r_[np.zeros(len(F)), np.ones(len(L))]
    return make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)).fit(X, y)


def discriminative_logscores(Xz, clf):
    """Per-day ``(logp_foll, logp_lut)`` from a ``P(luteal | x)`` classifier.

    ``p = P(luteal | x)`` is clipped to ``[EPS, 1 - EPS]``; the follicular score is
    ``log(1 - p)`` and the luteal score ``log(p)`` -- a drop-in for
    :func:`cyclelib.hsmm.gaussian_logscores`.
    """
    p = np.clip(clf.predict_proba(np.asarray(Xz, float))[:, 1], EPS, 1 - EPS)
    return np.log1p(-p), np.log(p)
