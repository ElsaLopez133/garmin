"""Tests for cyclelib.emissions anchor extraction + emission fits."""
import numpy as np
import pandas as pd

from cyclelib.emissions import (
    AnchorConfig,
    anchor_rows,
    clean_anchors,
    discriminative_logscores,
    fit_discriminative,
)


def _cycle_frame(cycle_len=28, period_end=5):
    """One cycle whose z_sig column equals cycle_day, so selected anchor rows are legible."""
    cd = np.arange(cycle_len)
    return pd.DataFrame({
        "cycle_day": cd,
        "is_period": cd < period_end,
        "cycle_len": cycle_len,
        "period_end": period_end,
        "z_sig": cd.astype(float),
    })


def test_anchor_rows_selects_expected_windows():
    cfg = AnchorConfig()  # foll_width=6, foll_guard=16, lut_width=6
    F, L = anchor_rows([_cycle_frame()], ["sig"], cfg)
    # follicular: cd in [period_end, min(pe+6, cl-16)) = [5, 11)
    assert sorted(F[:, 0]) == [5, 6, 7, 8, 9, 10]
    # luteal: last 6 non-period days before cycle end = [22, 28)
    assert sorted(L[:, 0]) == [22, 23, 24, 25, 26, 27]


def test_anchor_rows_stacks_across_cycles():
    F, L = anchor_rows([_cycle_frame(), _cycle_frame()], ["sig"])
    assert F.shape == (12, 1)
    assert L.shape == (12, 1)


def test_clean_anchors_drops_nan_rows():
    F = np.array([[1.0], [np.nan], [3.0]])
    L = np.array([[1.0], [2.0]])
    Fc, Lc = clean_anchors(F, L)
    assert Fc.shape == (2, 1)
    assert Lc.shape == (2, 1)


def test_fit_discriminative_none_when_too_few_rows():
    F = np.zeros((2, 1))   # need >= D + 2 = 3 clean rows per side
    L = np.ones((2, 1))
    assert fit_discriminative(F, L) is None


def test_discriminative_logscores_are_valid_and_oriented():
    F = np.array([[-0.2], [0.0], [0.1], [-0.1]])   # follicular ~ 0
    L = np.array([[4.8], [5.0], [5.1], [4.9]])     # luteal ~ 5
    clf = fit_discriminative(F, L)
    assert clf is not None

    Xz = np.array([[0.0], [5.0]])
    lf, ll = discriminative_logscores(Xz, clf)
    assert lf.shape == (2,) and ll.shape == (2,)
    # log-probabilities: exp sums to 1 per row (foll vs luteal are complementary).
    np.testing.assert_allclose(np.exp(lf) + np.exp(ll), 1.0, atol=1e-3)
    # a follicular-looking day favours follicular; a luteal-looking day favours luteal.
    assert lf[0] > ll[0]
    assert ll[1] > lf[1]
