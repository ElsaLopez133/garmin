"""Tests for the one-boundary HSMM core (cyclelib.hsmm)."""
import numpy as np
import pytest
from scipy.stats import multivariate_normal

from cyclelib.hsmm import (
    BoundaryWindow,
    DurationPrior,
    boundary_search,
    fit_gaussian,
    logmvn,
    segment_gaussian,
)


def _synthetic_cycle(cycle_len=28, period_end=5, boundary=17):
    """A cycle with clean per-day logscores that place the boundary at ``boundary``.

    Days 0..period_end-1 are menses. Follicular days score high before the boundary,
    luteal days score high from it on, so the MAP search should recover ``boundary``.
    """
    cd = np.arange(cycle_len)
    is_period = cd < period_end
    logp_foll = np.where(cd < boundary, 0.0, -10.0)
    logp_lut = np.where(cd >= boundary, 0.0, -10.0)
    return dict(cycle_day=cd, is_period=is_period, period_end=period_end,
                cycle_len=cycle_len, logp_foll=logp_foll, logp_lut=logp_lut)


def test_logmvn_matches_scipy():
    rng = np.random.default_rng(0)
    D = 3
    A = rng.normal(size=(D, D))
    Sig = A @ A.T + np.eye(D)          # random SPD
    mu = rng.normal(size=D)
    X = rng.normal(size=(7, D))
    np.testing.assert_allclose(
        logmvn(X, mu, Sig),
        multivariate_normal(mean=mu, cov=Sig).logpdf(X),
        rtol=1e-10, atol=1e-10,
    )


def test_fit_gaussian_none_when_too_few_rows():
    F = np.zeros((2, 2))              # need >= D + 2 = 4 rows
    L = np.ones((5, 2))
    assert fit_gaussian(F, L) is None


def test_fit_gaussian_params():
    rng = np.random.default_rng(1)
    F = rng.normal(size=(20, 2))
    L = rng.normal(size=(20, 2)) + 3.0
    reg = 0.2
    muF, SigF, muL, SigL = fit_gaussian(F, L, reg=reg)
    np.testing.assert_allclose(muF, F.mean(0))
    np.testing.assert_allclose(muL, L.mean(0))
    np.testing.assert_allclose(SigF, np.cov(F.T) + reg * np.eye(2))


def test_boundary_search_recovers_injected_boundary():
    c = _synthetic_cycle(boundary=17)
    res = boundary_search(c["cycle_day"], c["is_period"], c["period_end"],
                          c["cycle_len"], c["logp_foll"], c["logp_lut"],
                          use_duration=False)
    assert res["transition_day"] == 17
    np.testing.assert_allclose(res["post"].sum(), 1.0)
    assert 0.0 < res["confidence"] <= 1.0


def test_boundary_search_empty_window_returns_none():
    # cycle too short for any feasible luteal segment -> c1 < c0.
    cd = np.arange(8)
    res = boundary_search(cd, cd < 5, 5, 8,
                          np.zeros(8), np.zeros(8))
    assert res is None


def test_duration_prior_pulls_boundary_to_its_mean():
    # flat emissions -> only the luteal-duration prior decides the boundary.
    cycle_len = 28
    cd = np.arange(cycle_len)
    prior = DurationPrior(mu=13.0, sd=2.5)
    res = boundary_search(cd, cd < 5, 5, cycle_len,
                          np.zeros(cycle_len), np.zeros(cycle_len),
                          prior=prior, use_duration=True)
    # luteal length = cycle_len - b is pulled to mu=13 -> b = 15.
    assert res["transition_day"] == cycle_len - int(prior.mu)


def test_segment_gaussian_rejects_nan_rows():
    Xz = np.zeros((28, 1))
    Xz[10, 0] = np.nan
    emission = (np.array([0.0]), np.array([[1.0]]),
                np.array([3.0]), np.array([[1.0]]))
    cd = np.arange(28)
    assert segment_gaussian(cd, cd < 5, 5, 28, Xz, emission) is None


def test_segment_gaussian_end_to_end():
    boundary = 16
    cd = np.arange(28)
    # 1-D signal ~0 before the boundary, ~3 after; Gaussian emissions should split there.
    Xz = np.where(cd < boundary, 0.0, 3.0).reshape(-1, 1).astype(float)
    F = np.array([[-0.2], [0.1], [0.0], [-0.1], [0.2]])
    L = np.array([[2.8], [3.1], [3.0], [2.9], [3.2]])
    emission = fit_gaussian(F, L)
    res = segment_gaussian(cd, cd < 5, 5, 28, Xz, emission, use_duration=False)
    assert res["transition_day"] == boundary


def test_boundary_window_defaults():
    w = BoundaryWindow()
    assert (w.min_after, w.min_lut, w.max_lut) == (4, 4, 19)
