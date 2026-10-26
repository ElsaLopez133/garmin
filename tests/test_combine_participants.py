"""Exercise the multi-participant collector path on synthetic data (no real CSVs)."""
import numpy as np
import pandas as pd

from combine_participants import build_combined, cycle_stats
from cyclelib.features import standardize_per_group


def _participant(pid, days=60, period_starts=(0, 28, 56),
                 rhr_present=True, contraception="None"):
    dates = pd.date_range("2025-01-01", periods=days, freq="D")
    is_period = np.zeros(days, dtype=bool)
    for s in period_starts:
        is_period[s:s + 4] = True
    rhr = 60.0 + (np.arange(days) % 5) if rhr_present else np.full(days, np.nan)
    return pd.DataFrame({
        "participant_id": pid,
        "date": dates,
        "is_period": is_period,   # raw truth only, like a collected CSV (no cycle_phase)
        "resting_hr": rhr,
        "contraception": contraception,
    })


def test_cycle_stats_counts_starts_and_length():
    n_cyc, avg_len, pct_logged = cycle_stats(_participant("p"))
    assert n_cyc == 3          # three period onsets
    assert avg_len == 28.0     # two 28-day gaps
    # derived 5-day anchors: early follicular 0-4, 28-32, 56-59; late luteal 23-27, 51-55
    assert pct_logged == round(24 / 60 * 100, 1)


def test_build_combined_stacks_and_flags_usability():
    items = [
        ("f1", _participant("p1")),                                   # usable
        ("f2", _participant("p2", contraception="Hormonal pill")),    # excluded: hormonal
        ("f3", _participant("p3", rhr_present=False)),                # excluded: sparse RHR
    ]
    combined, qc = build_combined(items)

    assert len(combined) == 180
    assert set(combined["participant_id"]) == {"p1", "p2", "p3"}
    usable = dict(zip(qc["participant_id"], qc["ovulation_usable"]))
    assert usable == {"p1": "yes", "p2": "no", "p3": "no"}


def test_build_combined_fallback_id_when_column_missing():
    df = _participant("ignored").drop(columns=["participant_id"])
    combined, qc = build_combined([("file_stem", df)])
    # participant_id falls back to the file stem and every row is labelled.
    assert (combined["participant_id"] == "file_stem").all()
    assert qc["participant_id"].iloc[0] == "file_stem"


def test_per_group_standardization_over_combined_frame():
    combined, _ = build_combined([("f1", _participant("p1")),
                                  ("f2", _participant("p2"))])
    out = standardize_per_group(combined, ["resting_hr"], ["participant_id"])
    for pid in ("p1", "p2"):
        z = out.loc[out["participant_id"] == pid, "z_resting_hr"]
        assert np.isfinite(z).all()
        assert abs(z.median()) < 1e-9
