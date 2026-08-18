"""One-boundary HSMM: the shared follicular->luteal transition core.

The model per cycle is a hidden semi-Markov chain with the ordered states
``Menstruation -> Follicular -> Luteal``. Menstruation is *observed* (period
timing is ground truth), so only the single **follicular->luteal boundary** ``b``
is latent. We score every feasible ``b`` by the total per-day emission
log-likelihood on each side plus a soft prior on luteal duration, then take the
MAP boundary (and keep the full posterior over ``b``).

The emission model is deliberately factored out of :func:`boundary_search`: it
consumes *precomputed* per-day follicular / luteal log-scores, so the exact same
search drives both the generative Gaussian emissions (:func:`fit_gaussian` +
:func:`gaussian_logscores`) and the discriminative ``P(luteal | x)`` emissions in
:mod:`cyclelib.emissions`.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BoundaryWindow:
    """Physiological feasibility window for the follicular->luteal boundary.

    The candidate boundary ``b`` (a ``cycle_day``) is constrained to
    ``[max(period_end + min_after, cycle_len - max_lut), cycle_len - min_lut]``.
    Defaults mirror ``01_discovery`` / ``02_model_hsmm``.
    """
    min_after: int = 4      # earliest boundary is this many days after menses ends
    min_lut: int = 4        # luteal phase is at least this long
    max_lut: int = 19       # luteal phase is at most this long


@dataclass(frozen=True)
class DurationPrior:
    """Soft Gaussian prior on luteal length (``cycle_len - b``), in days."""
    mu: float = 13.0
    sd: float = 2.5


def logmvn(X, mu, Sig):
    """Log-density of a multivariate normal, evaluated row-wise on ``X``.

    ``X`` is ``(n, D)``; ``mu`` is ``(D,)``; ``Sig`` is ``(D, D)``. Uses a Cholesky
    factor so it is stable for the small, regularised covariances used here.
    """
    mu = np.asarray(mu, float)
    D = mu.shape[0]
    Lc = np.linalg.cholesky(Sig)
    logdet = 2.0 * np.sum(np.log(np.diag(Lc)))
    sol = np.linalg.solve(Lc, (np.asarray(X, float) - mu).T)
    return -0.5 * (D * np.log(2.0 * np.pi) + logdet + np.sum(sol ** 2, 0))


def fit_gaussian(F, L, reg=0.2):
    """Gaussian emission params ``(muF, SigF, muL, SigL)`` from anchor rows.

    ``F`` / ``L`` are ``(n, D)`` arrays of clean follicular / luteal anchor days.
    Covariances are ridge-regularised by ``reg`` on the diagonal so they stay
    invertible with few rows. Returns ``None`` if either side has fewer than
    ``D + 2`` rows (too few to estimate a full covariance).
    """
    F = np.asarray(F, float)
    L = np.asarray(L, float)
    D = F.shape[1]
    if len(F) < D + 2 or len(L) < D + 2:
        return None
    I = np.eye(D)
    return (F.mean(0), np.cov(F.T) + reg * I,
            L.mean(0), np.cov(L.T) + reg * I)


def gaussian_logscores(Xz, emission):
    """Per-day ``(logp_foll, logp_lut)`` from Gaussian emissions.

    ``emission`` is the tuple returned by :func:`fit_gaussian`. This is the
    generative emission used to feed :func:`boundary_search`.
    """
    muF, SigF, muL, SigL = emission
    return logmvn(Xz, muF, SigF), logmvn(Xz, muL, SigL)


def boundary_search(cycle_day, is_period, period_end, cycle_len,
                    logp_foll, logp_lut, *, window=BoundaryWindow(),
                    prior=DurationPrior(), use_duration=True):
    """MAP follicular->luteal boundary + posterior for one cycle.

    Emission-agnostic: ``logp_foll`` / ``logp_lut`` are per-day log-scores under
    each state (Gaussian or discriminative), aligned with ``cycle_day``. The score
    of a candidate boundary ``b`` sums the follicular log-scores on non-period days
    before ``b`` and the luteal log-scores on non-period days from ``b`` on, plus
    (optionally) the luteal-duration prior. Returns ``None`` when the feasibility
    window is empty.

    The returned dict has ``cands`` (candidate boundaries), ``post`` (posterior over
    them), ``transition_day`` (MAP boundary), ``transition_expected`` (posterior
    mean), ``cycle_len`` and ``confidence`` (posterior mass at the MAP day).
    """
    cd = np.asarray(cycle_day)
    ip = np.asarray(is_period, bool)
    cl = int(cycle_len)
    pe = int(period_end)

    c0 = max(pe + window.min_after, cl - window.max_lut)
    c1 = cl - window.min_lut
    if c1 < c0:
        return None

    cands = np.arange(c0, c1 + 1)
    scores = np.empty(len(cands), float)
    for i, b in enumerate(cands):
        in_f = (~ip) & (cd >= pe) & (cd < b)
        in_l = (~ip) & (cd >= b)
        s = logp_foll[in_f].sum() + logp_lut[in_l].sum()
        if use_duration:
            s += -0.5 * ((cl - b - prior.mu) / prior.sd) ** 2
        scores[i] = s

    post = np.exp(scores - scores.max())
    post /= post.sum()
    b_map = int(cands[np.argmax(scores)])
    return {"cands": cands, "post": post, "cycle_len": cl,
            "transition_day": b_map,
            "transition_expected": float((post * cands).sum()),
            "confidence": float(post.max())}


def segment_gaussian(cycle_day, is_period, period_end, cycle_len, Xz, emission,
                     *, window=BoundaryWindow(), prior=DurationPrior(),
                     use_duration=True):
    """Convenience wrapper: Gaussian emissions -> :func:`boundary_search`.

    ``Xz`` is the ``(n, D)`` matrix of per-day standardized signals. Returns
    ``None`` if any feature day is NaN (the caller must impute upstream) or the
    feasibility window is empty.
    """
    Xz = np.asarray(Xz, float)
    if np.isnan(Xz).any():
        return None
    lF, lL = gaussian_logscores(Xz, emission)
    return boundary_search(cycle_day, is_period, period_end, cycle_len, lF, lL,
                           window=window, prior=prior, use_duration=use_duration)
