"""Parse a tiny synthetic Garmin export (zip and folder) into the study schema."""
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))
import garmin_export  # noqa: E402

FILES = {
    "DI_CONNECT/DI-Connect-Aggregator/UDSFile_2025-01-01_2025-01-03.json": [
        {"calendarDate": d, "currentDayRestingHeartRate": 50 + i, "restingHeartRate": 99,
         "allDayStress": {"aggregatorList": [{"type": "TOTAL", "averageStressLevel": 20, "maxStressLevel": 80}]},
         "bodyBattery": {"chargedValue": 60, "drainedValue": 40}}
        for i, d in enumerate(["2025-01-01", "2025-01-03"])
    ],
    "DI_CONNECT/DI-Connect-Wellness/2025-01-01_2025-01-03_1_sleepData.json": [
        {"calendarDate": "2025-01-01", "deepSleepSeconds": 3600, "lightSleepSeconds": 14400,
         "remSleepSeconds": 7200, "sleepScores": {"overallScore": 80}},
        {"retro": True},
    ],
    "DI_CONNECT/DI-Connect-Wellness/2025-01-01_2025-01-03_1_healthStatusData.json": [
        {"calendarDate": "2025-01-03", "metrics": [{"type": "HR", "value": 50}, {"type": "HRV", "value": 70.0}]},
    ],
    "DI_CONNECT/DI-Connect-Wellness/1_MenstrualCycles.json": [
        {"startDate": "2025-01-01", "actualPeriodLength": 2, "fertileWindowStart": 2, "fertileWindowLength": 1},
    ],
    "DI_CONNECT/DI-Connect-Wellness/1_DailyMenstrualLogs.json": [
        {"calendarDate": "2025-01-02", "flow": "LIGHT", "symptoms": ["ACNE", "CRAMPS"],
         "ovulationDay": False, "notes": "private text"},
    ],
    "DI_CONNECT/DI-Connect-Wellness/1_LifestyleLogging.json": [
        {"dailyLogList": [
            {"behaviourName": "Alcohol", "status": "YES", "calendarDate": [2025, 1, 1],
             "dailyLogDetailDTOList": [{"subTypeId": 1, "amount": 2}]},
            {"behaviourName": "Morning Caffeine", "status": "NO", "calendarDate": [2025, 1, 3]},
        ]},
    ],
    "DI_CONNECT/DI-Connect-Metrics/MetricsAcuteTrainingLoad_20250101_20250103_1.json": [
        {"calendarDate": 1735689600000, "timestamp": 2, "dailyTrainingLoadAcute": 500},
        {"calendarDate": 1735689600000, "timestamp": 1, "dailyTrainingLoadAcute": 400},
    ],
    # Must never be read: not on the whitelist.
    "DI_CONNECT/DI-Connect-User/user_profile.json": "not json",
}


def _write_folder(root):
    for name, content in FILES.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content if isinstance(content, str) else json.dumps(content))


@pytest.fixture(params=["zip", "folder", "di_connect"])
def export_path(request, tmp_path):
    _write_folder(tmp_path / "export")
    if request.param == "folder":
        return tmp_path / "export"
    if request.param == "di_connect":
        return tmp_path / "export" / "DI_CONNECT"
    zpath = tmp_path / "export.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        for p in (tmp_path / "export").rglob("*.json"):
            zf.write(p, p.relative_to(tmp_path / "export").as_posix())
    return zpath


def test_export_dataframe(export_path):
    df = garmin_export.export_dataframe(export_path).set_index("date")

    assert [d.strftime("%Y-%m-%d") for d in df.index] == ["2025-01-01", "2025-01-02", "2025-01-03"]
    assert df["resting_hr"].tolist()[::2] == [50, 51]  # daily value, not the 7-day one
    assert df.loc["2025-01-03", "hrv_rmssd"] == 70.0
    assert df.loc["2025-01-01", "total_sleep_hr"] == 7.0
    assert df["is_period"].tolist() == [True, True, False]
    assert df["garmin_predicted_fertile"].tolist() == [False, False, True]
    assert df.loc["2025-01-01", "dailyTrainingLoadAcute"] == 500  # latest record wins
    assert df["cycle_start"].tolist() == ["2025-01-01"] * 3
    assert df.loc["2025-01-02", "logged_symptoms"] == "ACNE;CRAMPS"
    assert not any("notes" in c for c in df.columns)
    assert df.loc["2025-01-01", "lifestyle_alcohol"] == 1
    assert df.loc["2025-01-01", "lifestyle_alcohol_amount"] == 2
    assert df.loc["2025-01-03", "lifestyle_morning_caffeine"] == 0


def test_not_an_export(tmp_path):
    (tmp_path / "notes.txt").write_text("hi")
    with pytest.raises(garmin_export.ExportFormatError):
        garmin_export.export_dataframe(tmp_path)


def test_participant_dataframe(tmp_path):
    """An upload from the participant page: filtered export + metadata.json."""
    _write_folder(tmp_path / "export")
    zpath = tmp_path / "garmin_export_abcd1234.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        for p in (tmp_path / "export").rglob("*.json"):
            zf.write(p, p.relative_to(tmp_path / "export").as_posix())
        zf.writestr("metadata.json", json.dumps({"participant_id": "abcd1234", "contraception": "Hormonal IUD"}))

    df = garmin_export.participant_dataframe(zpath)

    assert list(df.columns[:3]) == ["participant_id", "contraception", "date"]
    assert set(df["participant_id"]) == {"abcd1234"}
