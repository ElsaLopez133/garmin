"""
Build the study CSV from a Garmin "Export Your Data" archive (no login needed).

Garmin's GDPR export (Account > Data Management > Export Your Data) is a zip with
~40 folders; only a handful of JSON files under DI_CONNECT/ are used here (see
EXPORT_FILES). Pass the zip itself, the extracted folder, or just its DI_CONNECT/
folder. Nothing else in the archive is opened (no GPS tracks, profile, contacts...).

Columns shared with garmin_fetch.download_dataframe() keep the API names; the
export has many more daily metrics, which are all kept. Differences vs the API:
sleep respiration/stress keep their decimals, trainingStatus is the name (e.g.
"PRODUCTIVE") rather than the API's numeric code, and a few API-only columns
(HRV weekly average/status, sleep need, monthly load balance) are absent.
Free-text period-log notes are deliberately not read.

Usage (from the repo root):
    uv run python collector/garmin_export.py path/to/export.zip [out.csv]
"""

import json
import re
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import garmin_fetch

# kind -> regex on the archive path. Files are read in sorted-name order so later
# (more recent) chunks win where chunk date ranges overlap.
EXPORT_FILES = {
    "uds": re.compile(r"DI-Connect-Aggregator/UDSFile_[^/]*\.json$"),
    "sleep": re.compile(r"DI-Connect-Wellness/[^/]*_sleepData\.json$"),
    "health": re.compile(r"DI-Connect-Wellness/[^/]*_healthStatusData\.json$"),
    "cycles": re.compile(r"DI-Connect-Wellness/[^/]*_MenstrualCycles\.json$"),
    "cycle_logs": re.compile(r"DI-Connect-Wellness/[^/]*_DailyMenstrualLogs\.json$"),
    "lifestyle": re.compile(r"DI-Connect-Wellness/[^/]*_LifestyleLogging\.json$"),
    "acute_load": re.compile(r"DI-Connect-Metrics/MetricsAcuteTrainingLoad_[^/]*\.json$"),
    "training_history": re.compile(r"DI-Connect-Metrics/TrainingHistory_[^/]*\.json$"),
    "vo2max": re.compile(r"DI-Connect-Metrics/MetricsMaxMetData_[^/]*\.json$"),
    "acclimation": re.compile(r"DI-Connect-Metrics/MetricsHeatAltitudeAcclimation_[^/]*\.json$"),
}

# Overnight health-status metric type -> column prefix. HRV keeps the API name.
HEALTH_METRICS = {"HRV": "hrv", "HR": "overnight_hr", "RESPIRATION": "overnight_respiration",
                  "SPO2": "overnight_spo2", "SKIN_TEMP_C": "skin_temp_c"}
SLEEP_SUBSCORES = ["quality", "duration", "recovery", "deep", "rem", "light", "awakeningsCount",
                   "awakeTime", "combinedAwake", "restfulness", "interruptions"]
BODY_BATTERY_STATS = {"HIGHEST": "bb_highest", "LOWEST": "bb_lowest", "STARTOFDAY": "bb_start_of_day",
                      "ENDOFDAY": "bb_end_of_day", "DURINGSLEEP": "bb_during_sleep",
                      "SLEEPSTART": "bb_sleep_start", "SLEEPEND": "bb_sleep_end"}
STRESS_DURATIONS = {"restDuration": "stress_rest_min", "lowDuration": "stress_low_min",
                    "mediumDuration": "stress_medium_min", "highDuration": "stress_high_min",
                    "activityDuration": "stress_activity_min", "uncategorizedDuration": "stress_uncategorized_min",
                    "totalDuration": "stress_total_min"}
ACUTE_LOAD = ["dailyTrainingLoadAcute", "acwrPercent", "acwrStatus", "acwrStatusFeedback",
              "dailyTrainingLoadChronic", "dailyAcuteChronicWorkloadRatio"]


class ExportFormatError(Exception):
    """The given file/folder doesn't look like a Garmin data export."""


def read_export(path):
    """Return {kind: [records]} from only the EXPORT_FILES entries of a zip or folder."""
    path = Path(path)
    found = {kind: [] for kind in EXPORT_FILES}

    def collect(name, read_bytes):
        for kind, pattern in EXPORT_FILES.items():
            if pattern.search(name):
                data = json.loads(read_bytes())
                found[kind].extend(data if isinstance(data, list) else [data])
                return

    if path.is_dir():
        # Prefix the folder name so a bare DI_CONNECT/ folder still matches the patterns.
        for p in sorted(path.rglob("*.json")):
            collect(f"{path.name}/{p.relative_to(path).as_posix()}", p.read_bytes)
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            for name in sorted(zf.namelist()):
                collect(name, lambda: zf.read(name))
    else:
        raise ExportFormatError(f"{path} is neither a folder nor a zip file.")

    if not any(found.values()):
        raise ExportFormatError("No Garmin health files found — is this the DI_CONNECT export?")
    return found


def _hours(seconds):
    return round(seconds / 3600, 2) if seconds else np.nan


def _minutes(seconds):
    return round(seconds / 60, 1) if seconds is not None else np.nan


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _health_rows(health):
    for r in health:
        row = {"date": r["calendarDate"]}
        for m in r.get("metrics", []):
            prefix = HEALTH_METRICS.get(m.get("type"))
            if prefix is None or m.get("status") == "UNKNOWN":  # UNKNOWN = not measured
                continue
            row["hrv_rmssd" if prefix == "hrv" else prefix] = m.get("value")
            row[f"{prefix}_baseline_low"] = m.get("baselineLowerLimit")
            row[f"{prefix}_baseline_high"] = m.get("baselineUpperLimit")
            row[f"{prefix}_status"] = m.get("status")
        yield row


def _uds_rows(uds):
    for r in uds:
        stress = {a.get("type"): a for a in (r.get("allDayStress") or {}).get("aggregatorList", [])}
        total = stress.get("TOTAL", {})
        bb = r.get("bodyBattery") or {}
        bb_stats = {s.get("bodyBatteryStatType"): s.get("statsValue") for s in bb.get("bodyBatteryStatList", [])}
        resp = r.get("respiration") or {}
        local, gmt = r.get("wellnessStartTimeLocal"), r.get("wellnessStartTimeGmt")
        yield {
            "date": r["calendarDate"],
            # The API's daily resting HR; restingHeartRate here is a 7-day value.
            "resting_hr": r.get("currentDayRestingHeartRate"),
            "resting_hr_7d": r.get("restingHeartRate"),
            "min_hr": r.get("minHeartRate"),
            "max_hr": r.get("maxHeartRate"),
            "waking_respiration_avg": resp.get("avgWakingRespirationValue"),
            "waking_respiration_low": resp.get("lowestRespirationValue"),
            "waking_respiration_high": resp.get("highestRespirationValue"),
            "maxStressLevel": total.get("maxStressLevel"),
            "avgStressLevel": total.get("averageStressLevel"),
            "stress_awake_avg": stress.get("AWAKE", {}).get("averageStressLevel"),
            "stress_asleep_avg": stress.get("ASLEEP", {}).get("averageStressLevel"),
            **{col: _minutes(total.get(k)) for k, col in STRESS_DURATIONS.items()},
            "charged": bb.get("chargedValue"),
            "drained": bb.get("drainedValue"),
            **{col: bb_stats.get(k) for k, col in BODY_BATTERY_STATS.items()},
            "total_steps": r.get("totalSteps"),
            "total_distance_m": r.get("totalDistanceMeters"),
            "total_kcal": r.get("totalKilocalories"),
            "active_kcal": r.get("activeKilocalories"),
            "moderate_intensity_min": r.get("moderateIntensityMinutes"),
            "vigorous_intensity_min": r.get("vigorousIntensityMinutes"),
            "floors_ascended_m": r.get("floorsAscendedInMeters"),
            # < 24 means a partial day (e.g. the export day itself).
            "day_coverage_hr": _hours((r.get("durationInMilliseconds") or 0) / 1000),
            "utc_offset_hr": ((datetime.fromisoformat(local) - datetime.fromisoformat(gmt)).total_seconds() / 3600
                              if local and gmt else np.nan),
        }


def _sleep_rows(sleep):
    for s in sleep:
        if "calendarDate" not in s:  # nap-only / retro stubs carry no night of sleep
            continue
        stages = [s.get(k) for k in ("deepSleepSeconds", "lightSleepSeconds", "remSleepSeconds")]
        scores = s.get("sleepScores") or {}
        yield {
            "date": s["calendarDate"],
            "total_sleep_hr": _hours(sum(x or 0 for x in stages)),
            "deep_sleep_hr": _hours(s.get("deepSleepSeconds")),
            "light_sleep_hr": _hours(s.get("lightSleepSeconds")),
            "rem_sleep_hr": _hours(s.get("remSleepSeconds")),
            "awake_sleep_hr": _hours(s.get("awakeSleepSeconds")),
            "avg_sleep_respiration": s.get("averageRespiration"),
            "lowest_sleep_respiration": s.get("lowestRespiration"),
            "highest_sleep_respiration": s.get("highestRespiration"),
            "avg_sleep_stress": s.get("avgSleepStress"),
            "sleep_awake_count": s.get("awakeCount"),
            "sleep_restless_moments": s.get("restlessMomentCount"),
            "sleep_start_gmt": s.get("sleepStartTimestampGMT"),
            "sleep_end_gmt": s.get("sleepEndTimestampGMT"),
            "sleep_score": scores.get("overallScore"),
            **{f"sleep_score_{_slug(k)}": scores.get(f"{k}Score") for k in SLEEP_SUBSCORES},
            "sleep_feedback": scores.get("feedback"),
            "sleep_insight": scores.get("insight"),
        }


def _training_rows(acute_load, training_history, vo2max, acclimation):
    # Several records per day are possible (multiple syncs/devices): sort so the
    # latest comes last, which _frame() keeps.
    for r in sorted(acute_load, key=lambda r: r.get("timestamp") or 0):
        day = datetime.fromtimestamp(r["calendarDate"] / 1000, tz=timezone.utc).date()
        yield {"date": day.isoformat(), **{k: r.get(k) for k in ACUTE_LOAD}}
    for r in sorted(training_history, key=lambda r: r.get("timestamp") or ""):
        yield {"date": r["calendarDate"], "trainingStatus": r.get("trainingStatus"),
               "trainingStatusSport": r.get("sport"), "fitnessLevelTrend": r.get("fitnessLevelTrend"),
               "trainingStatusFeedback": r.get("trainingStatus2FeedbackPhrase")}
    for r in sorted(vo2max, key=lambda r: r.get("updateTimestamp") or ""):
        cycling = "CYCLING" in (r.get("sport"), r.get("maxMetCategory"))
        yield {"date": r["calendarDate"], f"vo2max_{'cycling' if cycling else 'running'}": r.get("vo2MaxValue")}
    for r in sorted(acclimation, key=lambda r: r.get("timestamp") or ""):
        yield {"date": r["calendarDate"], "heat_acclimation_pct": r.get("heatAcclimationPercentage"),
               "altitude_acclimation": r.get("altitudeAcclimation")}


def _cycle_log_rows(logs):
    # Free-text "notes" are skipped on purpose.
    for r in sorted(logs, key=lambda r: r.get("reportTimestamp") or ""):
        yield {"date": r["calendarDate"], "logged_flow": r.get("flow"),
               "logged_ovulation_day": r.get("ovulationDay"),
               **{f"logged_{_slug(k)}": ";".join(r[k]) if isinstance(r.get(k), list) else r.get(k)
                  for k in ("symptoms", "moods", "discharge", "sexDrive")}}


def _lifestyle_rows(lifestyle):
    """One lifestyle_<behaviour> column (1 = yes, 0 = no) per tracked behaviour, plus
    lifestyle_<behaviour>_amount where an amount was logged (e.g. caffeine, alcohol)."""
    for block in lifestyle:
        for r in block.get("dailyLogList", []):
            name = f"lifestyle_{_slug(r['behaviourName'])}"
            row = {"date": date(*r["calendarDate"]).isoformat(),
                   name: {"YES": 1, "NO": 0}.get(r.get("status"))}
            amounts = [d.get("amount") for d in r.get("dailyLogDetailDTOList") or [] if d.get("amount") is not None]
            if amounts:
                row[f"{name}_amount"] = sum(amounts)
            yield row


def _cycle_entries(cycles):
    """Map export cycle records onto the API keys build_calendar_days() expects."""
    for c in cycles:
        yield {
            "startDate": c["startDate"],
            # actualPeriodLength is empty for the in-progress cycle; applicable* falls back to the prediction.
            "periodLength": c.get("actualPeriodLength") or c.get("applicablePeriodLength"),
            "fertileWindowStart": c.get("fertileWindowStart"),
            "lengthOfFertileWindow": c.get("fertileWindowLength"),
        }


def _cycle_rows(cycles, date_list):
    """Daily is_period / fertile flags plus the info of the cycle each day falls in."""
    calendar_days = garmin_fetch.build_calendar_days(list(_cycle_entries(cycles)))
    starts = sorted(cycles, key=lambda c: c["startDate"])
    i = -1
    for d in date_list:
        while i + 1 < len(starts) and starts[i + 1]["startDate"] <= d.isoformat():
            i += 1
        c = starts[i] if i >= 0 else {}
        yield {"date": d.isoformat(),
               **calendar_days.get(d.isoformat(), {"is_period": False, "garmin_predicted_fertile": False}),
               "cycle_start": c.get("startDate"),
               "cycle_length": c.get("actualCycleLength") or c.get("applicableMenstrualCycleLength"),
               "hormonal_contraception": c.get("hormonalContraception")}


def _frame(rows):
    df = pd.DataFrame(list(rows))
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    # Overlapping chunks / repeated records: combine per day, latest non-null wins.
    return df.groupby("date").last()


def export_dataframe(path, days_back=None):
    """Read a Garmin export and return the merged one-row-per-day DataFrame."""
    found = read_export(path)
    dfs = [
        _frame(_health_rows(found["health"])),
        _frame(_uds_rows(found["uds"])),
        _frame(_sleep_rows(found["sleep"])),
        _frame(_training_rows(found["acute_load"], found["training_history"],
                              found["vo2max"], found["acclimation"])),
    ]
    dfs = [df for df in dfs if not df.empty]
    if not dfs:
        raise ExportFormatError("The export contains no daily health data.")

    first = min(df.index.min() for df in dfs).date()
    last = max(df.index.max() for df in dfs).date()
    if days_back is not None:
        first = max(first, last - timedelta(days=days_back))
    date_list = [first + timedelta(days=i) for i in range((last - first).days + 1)]

    extra = [_frame(_cycle_rows(found["cycles"], date_list)),
             _frame(_cycle_log_rows(found["cycle_logs"])),
             _frame(_lifestyle_rows(found["lifestyle"]))]
    df_all = (pd.concat([*dfs, *(df for df in extra if not df.empty)], axis=1, sort=True)
              .reindex(pd.DatetimeIndex(date_list, name="date")))
    return df_all.reset_index()


def participant_dataframe(zip_path):
    """export_dataframe() for an upload from the participant page (upload_page.html),
    with its metadata.json answers as leading columns, like the server's CSVs."""
    with zipfile.ZipFile(zip_path) as zf:
        meta = json.loads(zf.read("metadata.json"))
    df = export_dataframe(zip_path)
    for key, value in meta.items():
        df[key] = value
    return df[list(meta) + [c for c in df.columns if c not in meta]]


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    df = export_dataframe(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) == 3 else Path("data/garmin_export/garmin_export.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"{len(df)} days ({df['date'].min():%Y-%m-%d} -> {df['date'].max():%Y-%m-%d}), "
          f"{df.shape[1] - 1} columns -> {out}")
