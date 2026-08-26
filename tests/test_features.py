"""Tests for cyclelib.features robust standardization."""
import numpy as np
import pandas as pd

from cyclelib.features import MAD_SCALE, robust_standardize, standardize_per_group


def test_robust_standardize_centers_and_scales():
    df = pd.DataFrame({"sig": [1.0, 2.0, 3.0, 4.0, 5.0]})
    out = robust_standardize(df, ["sig"])

    # median (3) maps to 0; scale is MAD * 1.4826 (MAD of |x-3| = 1).
    assert out["z_sig"].iloc[2] == 0.0
    np.testing.assert_allclose(out["z_sig"].iloc[4], 2.0 / MAD_SCALE)
    # symmetric around the median
    np.testing.assert_allclose(out["z_sig"].to_numpy(),
                               -out["z_sig"].to_numpy()[::-1])


def test_robust_standardize_zero_mad_falls_back_to_one():
    df = pd.DataFrame({"flat": [5.0, 5.0, 5.0, 5.0]})
    out = robust_standardize(df, ["flat"])
    # constant column -> MAD 0 -> scale forced to 1 -> all zeros, no div-by-zero.
    assert (out["z_flat"] == 0.0).all()
    assert np.isfinite(out["z_flat"]).all()


def test_robust_standardize_interpolates_gaps():
    df = pd.DataFrame({"sig": [1.0, np.nan, 3.0, 4.0, 5.0]})
    out = robust_standardize(df, ["sig"])
    # the interior NaN is interpolated before standardizing -> no NaN in output.
    assert out["z_sig"].notna().all()


def test_standardize_per_group_is_independent_and_copies():
    # two groups with very different location/scale; each should standardize to itself.
    df = pd.DataFrame({
        "pid": ["a"] * 5 + ["b"] * 5,
        "sig": [1.0, 2.0, 3.0, 4.0, 5.0, 101.0, 102.0, 103.0, 104.0, 105.0],
    })
    out = standardize_per_group(df, ["sig"], ["pid"])

    za = out.loc[out["pid"] == "a", "z_sig"].to_numpy()
    zb = out.loc[out["pid"] == "b", "z_sig"].to_numpy()
    # identical shape around each group's own median despite a 100-unit offset.
    np.testing.assert_allclose(za, zb)
    assert out.loc[out["pid"] == "a", "z_sig"].iloc[2] == 0.0
    # returns a copy: the input frame is untouched.
    assert "z_sig" not in df.columns
