"""
Shared Garmin download logic (no GUI / no server dependency).

Produces a DataFrame in the same schema as garmin_download.py / the study CSV.
Imported by both the desktop collector and the FastAPI server so the fetch logic
lives in one place.
"""

import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from garminconnect import GarminConnectTooManyRequestsError

ALL_METRICS = ["hrv", "rhr", "sleep", "cycle", "stress", "body_battery", "training"]
MINIMAL_METRICS = ["hrv", "rhr", "sleep", "cycle"]

CALL_DELAY = float(os.environ.get("GARMIN_CALL_DELAY", "0.25"))
DEFAULT_TOKENSTORE = Path(__file__).resolve().parents[1] / ".garmin_tokens"


def _pace():
    """Small pause between Garmin API calls to avoid bursting"""
    if CALL_DELAY > 0:
        time.sleep(CALL_DELAY)


def fetch_menstrual_calendar(api, start_date, end_date):
    max_range_days = 90
    all_entries = []
    current_start = start_date
    while current_start < end_date:
        current_end = min(current_start + timedelta(days=max_range_days - 1), end_date)
        try:
            chunk = api.get_menstrual_calendar_data(current_start.isoformat(), current_end.isoformat())
            all_entries.extend(chunk.get("cycleSummaries", []))
        except Exception as e:
            print(f"Failed to fetch cycle {current_start}..{current_end}: {e}")
        _pace()
        current_start = current_end + timedelta(days=1)
    return all_entries


def build_calendar_days(calendar_entries):
    """Build cycle booleans from the logged period truth plus Garmin's fertile guess.

    Collection keeps only raw calendar facts:
    - ``is_period``: logged period / bleeding days.
    - ``garmin_predicted_fertile``: Garmin's fertile-window prediction.

    Reproductive phase labels such as Early follicular / Late luteal are a
    preprocessing choice, so ``cycle_phase`` is deliberately not filled here.
    """
    calendar_days = {}
    calendar_entries = sorted(calendar_entries, key=lambda e: e["startDate"])

    def record(day):
        return calendar_days.setdefault(day.isoformat(), {
            "is_period": False,
            "garmin_predicted_fertile": False,
        })

    for entry in calendar_entries:
        start = datetime.fromisoformat(entry["startDate"]).date()
        period_len = int(entry.get("periodLength", 1) or 1)
        fertile_start = entry.get("fertileWindowStart")
        fertile_len = int(entry.get("lengthOfFertileWindow", 0) or 0)

        for d in range(period_len):
            record(start + timedelta(days=d))["is_period"] = True

        if fertile_start is None or fertile_len <= 0:
            continue

        fertile_window_start = start + timedelta(days=int(fertile_start))
        for d in range(fertile_len):
            record(fertile_window_start + timedelta(days=d))["garmin_predicted_fertile"] = True

    return calendar_days


def login_with_retry(api, max_attempts=5, base_delay=15, tokenstore=None):
    """Log in with cached tokens, retrying when Garmin rate-limits auth."""
    def is_rate_limit_error(exc):
        current = exc
        while current is not None:
            message = str(current).lower()
            if "429" in message or "too many requests" in message or "rate limit" in message:
                return True
            current = current.__cause__ or current.__context__
        return False

    tokenstore = Path(os.environ.get("GARMINTOKENS", tokenstore or DEFAULT_TOKENSTORE))
    tokenstore.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, max_attempts + 1):
        try:
            api.login(tokenstore=str(tokenstore))
            return
        except Exception as exc:
            if not (isinstance(exc, GarminConnectTooManyRequestsError) or is_rate_limit_error(exc)):
                raise
            if attempt == max_attempts:
                raise
            time.sleep(base_delay * (2 ** (attempt - 1)))


def _fetch_hrv(api, date_list):
    hrv_list = []
    last_weekly_avg = last_baseline = None
    for d in date_list:
        try:
            data = api.get_hrv_data(d.isoformat())
            if data and "hrvSummary" in data:
                s = data["hrvSummary"]
                weekly_avg = s.get("weeklyAvg") or last_weekly_avg
                baseline = s.get("baseline") or last_baseline
                hrv_list.append({
                    "date": d.isoformat(), "hrv_rmssd": s.get("lastNightAvg"), "status": s.get("status"),
                    "weekly_avg": weekly_avg, "lastnight_5min_high": s.get("lastNight5MinHigh"),
                    "baseline": baseline, "feedback_phrase": s.get("feedbackPhrase")})
                last_weekly_avg, last_baseline = weekly_avg, baseline
            else:
                hrv_list.append({"date": d.isoformat(), "hrv_rmssd": None, "status": None,
                                 "weekly_avg": last_weekly_avg, "lastnight_5min_high": None,
                                 "baseline": last_baseline, "feedback_phrase": None})
        except Exception:
            hrv_list.append({"date": d.isoformat(), "hrv_rmssd": None, "status": None, "weekly_avg": None,
                             "lastnight_5min_high": None, "baseline": None, "feedback_phrase": None})
        _pace()
    return hrv_list


def _fetch_rhr(api, date_list):
    rhr_list = []
    for d in date_list:
        try:
            data = api.get_rhr_day(d.isoformat())
            if data and "allMetrics" in data:
                e = data["allMetrics"].get("metricsMap", {}).get("WELLNESS_RESTING_HEART_RATE", [])
                rhr_list.append({"date": d.isoformat(), "resting_hr": e[0].get("value") if e else None})
            else:
                rhr_list.append({"date": d.isoformat(), "resting_hr": None})
        except Exception:
            rhr_list.append({"date": d.isoformat(), "resting_hr": None})
        _pace()
    return rhr_list


def _fetch_sleep(api, date_list):
    sleep_list = []
    for d in date_list:
        try:
            data = api.get_sleep_data(d.isoformat())
            if not data or "dailySleepDTO" not in data:
                sleep_list.append({"date": d.isoformat(), "total_sleep_hr": 0, "deep_sleep_hr": 0,
                                   "light_sleep_hr": 0, "rem_sleep_hr": 0, "awake_sleep_hr": 0,
                                   "avg_sleep_hr": 0, "avg_sleep_respiration": 0, "avg_sleep_stress": 0,
                                   "sleep_score": 0, "sleep_quality": None, "sleep_feedback": None,
                                   "sleep_need_baseline": 0, "sleep_need_actual": 0, "sleep_need_feedback": None})
                continue
            s = data["dailySleepDTO"]
            sleep_list.append({
                "date": d.isoformat(),
                "total_sleep_hr": round(s.get("sleepTimeSeconds", 0) / 3600, 2) if s.get("sleepTimeSeconds") else np.nan,
                "deep_sleep_hr": round(s.get("deepSleepSeconds", 0) / 3600, 2) if s.get("deepSleepSeconds") else np.nan,
                "light_sleep_hr": round(s.get("lightSleepSeconds", 0) / 3600, 2) if s.get("lightSleepSeconds") else np.nan,
                "rem_sleep_hr": round(s.get("remSleepSeconds", 0) / 3600, 2) if s.get("remSleepSeconds") else np.nan,
                "awake_sleep_hr": round(s.get("awakeSleepSeconds", 0) / 3600, 2) if s.get("awakeSleepSeconds") else np.nan,
                "avg_sleep_hr": s.get("avgHeartRate"), "avg_sleep_respiration": s.get("averageRespirationValue"),
                "avg_sleep_stress": s.get("avgSleepStress"),
                "sleep_score": s.get("sleepScores", {}).get("overall", {}).get("value"),
                "sleep_quality": s.get("sleepScores", {}).get("overall", {}).get("qualifierKey"),
                "sleep_feedback": s.get("sleepScoreFeedback"),
                "sleep_need_baseline": s.get("sleepNeed", {}).get("baseline"),
                "sleep_need_actual": s.get("sleepNeed", {}).get("actual"),
                "sleep_need_feedback": s.get("sleepNeed", {}).get("feedback")})
        except Exception:
            continue
        finally:
            _pace()
    return sleep_list


def _fetch_cycle(api, date_list, start_date, today):
    calendar_entries = fetch_menstrual_calendar(api, start_date, today)
    calendar_days = build_calendar_days(calendar_entries)
    return [
        {
            "date": d.isoformat(),
            **calendar_days.get(d.isoformat(), {
                "is_period": False,
                "garmin_predicted_fertile": False,
            }),
        }
        for d in date_list
    ]


def _fetch_stress(api, date_list):
    stress_list = []
    for d in date_list:
        try:
            data = api.get_stress_data(d.isoformat())
            if data:
                stress_list.append({"date": d.isoformat(), "maxStressLevel": data.get("maxStressLevel"),
                                    "avgStressLevel": data.get("avgStressLevel")})
            else:
                stress_list.append({"date": d.isoformat(), "maxStressLevel": None, "avgStressLevel": None})
        except Exception:
            stress_list.append({"date": d.isoformat(), "maxStressLevel": None, "avgStressLevel": None})
        _pace()
    return stress_list


def _fetch_body_battery(api, date_list, start_date, today):
    # get_body_battery accepts a date range, so fetch in chunks instead of one
    # call per day (≈366 calls -> a handful). Index by date, emit one row/day.
    bb_by_date = {}
    max_range_days = 28
    current_start = start_date
    while current_start <= today:
        current_end = min(current_start + timedelta(days=max_range_days - 1), today)
        try:
            data = api.get_body_battery(current_start.isoformat(), current_end.isoformat())
            for day in (data or []):
                ds = day.get("date")
                if ds:
                    bb_by_date[ds] = {"charged": day.get("charged"), "drained": day.get("drained")}
        except Exception:
            pass
        _pace()
        current_start = current_end + timedelta(days=1)
    out = []
    for d in date_list:
        rec = bb_by_date.get(d.isoformat(), {"charged": None, "drained": None})
        out.append({"date": d.isoformat(), "charged": rec["charged"], "drained": rec["drained"]})
    return out


def _fetch_training(api, date_list):
    training_load_list, training_status_list = [], []
    empty_load = {"monthlyLoadAerobicLow": None, "monthlyLoadAerobicHigh": None, "monthlyLoadAnaerobic": None,
                  "monthlyLoadAerobicLowTargetMin": None, "monthlyLoadAerobicLowTargetMax": None,
                  "monthlyLoadAerobicHighTargetMin": None, "monthlyLoadAerobicHighTargetMax": None,
                  "monthlyLoadAnaerobicTargetMin": None, "monthlyLoadAnaerobicTargetMax": None,
                  "trainingBalanceFeedbackPhrase": None}
    empty_status = {"trainingStatus": None, "weeklyTrainingLoad": None, "feedback": None,
                    "dailyTrainingLoadAcute": None, "acwrPercent": None, "acwrStatus": None,
                    "acwrStatusFeedback": None, "dailyTrainingLoadChronic": None,
                    "minTrainingLoadChronic": None, "maxTrainingLoadChronic": None,
                    "dailyAcuteChronicWorkloadRatio": None}
    for d in date_list:
        try:
            data = api.get_training_status(d.isoformat())
            if data and "mostRecentTrainingLoadBalance" in data:
                load_map = data["mostRecentTrainingLoadBalance"].get("metricsTrainingLoadBalanceDTOMap", {})
                if load_map:
                    b = next(iter(load_map.values()))
                    training_load_list.append({"date": d.isoformat(), **{k: b.get(k) for k in empty_load}})
                else:
                    training_load_list.append({"date": d.isoformat(), **empty_load})
            if data and "mostRecentTrainingStatus" in data:
                latest = data["mostRecentTrainingStatus"].get("latestTrainingStatusData", {})
                if latest:
                    e = next(iter(latest.values()))
                    acute = e.get("acuteTrainingLoadDTO", {})
                    training_status_list.append({
                        "date": d.isoformat(), "trainingStatus": e.get("trainingStatus"),
                        "weeklyTrainingLoad": e.get("weeklyTrainingLoad"), "feedback": e.get("trainingStatusFeedbackPhrase"),
                        "dailyTrainingLoadAcute": acute.get("dailyTrainingLoadAcute"), "acwrPercent": acute.get("acwrPercent"),
                        "acwrStatus": acute.get("acwrStatus"), "acwrStatusFeedback": acute.get("acwrStatusFeedback"),
                        "dailyTrainingLoadChronic": acute.get("dailyTrainingLoadChronic"),
                        "minTrainingLoadChronic": acute.get("minTrainingLoadChronic"),
                        "maxTrainingLoadChronic": acute.get("maxTrainingLoadChronic"),
                        "dailyAcuteChronicWorkloadRatio": acute.get("dailyAcuteChronicWorkloadRatio")})
                else:
                    training_status_list.append({"date": d.isoformat(), **empty_status})
        except Exception:
            training_load_list.append({"date": d.isoformat(), **empty_load})
            training_status_list.append({"date": d.isoformat(), **empty_status})
            continue
        finally:
            _pace()
    return training_load_list, training_status_list


def download_dataframe(api, days_back, status=lambda m: None, metrics=None, progress=None):
    """Download the selected metrics and return the merged DataFrame.

    `metrics` selects which metrics to pull (default MINIMAL_METRICS; pass
    ALL_METRICS for the full set).
    """
    metrics = MINIMAL_METRICS if metrics is None else metrics
    today = date.today()
    start_date = today - timedelta(days=days_back)
    date_list = [start_date + timedelta(days=i) for i in range(days_back + 1)]

    def with_progress(items, label):
        if progress is None:
            return items
        return progress(items, label)

    # name -> DataFrame for whatever was fetched (merged in dict-insertion order)
    dfs = {}

    if "hrv" in metrics:
        status("Downloading HRV…")
        dfs["hrv"] = pd.DataFrame(_fetch_hrv(api, with_progress(date_list, "HRV")))

    if "rhr" in metrics:
        status("Downloading resting heart rate…")
        dfs["rhr"] = pd.DataFrame(_fetch_rhr(api, with_progress(date_list, "Resting HR")))

    if "sleep" in metrics:
        status("Downloading sleep…")
        dfs["sleep"] = pd.DataFrame(_fetch_sleep(api, with_progress(date_list, "Sleep")))

    if "cycle" in metrics:
        status("Downloading menstrual cycle…")
        dfs["cycle"] = pd.DataFrame(_fetch_cycle(api, with_progress(date_list, "Cycle"), start_date, today))

    if "stress" in metrics:
        status("Downloading stress…")
        dfs["stress"] = pd.DataFrame(_fetch_stress(api, with_progress(date_list, "Stress")))

    if "body_battery" in metrics:
        status("Downloading body battery…")
        dfs["body_battery"] = pd.DataFrame(_fetch_body_battery(
            api, with_progress(date_list, "Body battery"), start_date, today
        ))

    if "training" in metrics:
        status("Downloading training status…")
        load_list, status_list = _fetch_training(api, with_progress(date_list, "Training"))
        dfs["training_load"] = pd.DataFrame(load_list)
        dfs["training_status"] = pd.DataFrame(status_list)

    status("Merging data…")
    if not dfs:
        return pd.DataFrame()
    for df in dfs.values():
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
    df_all = pd.concat(dfs.values(), axis=1).sort_index().reset_index()

    if "baseline" in df_all.columns:
        expanded = (df_all["baseline"].dropna()
                    .apply(lambda x: eval(x) if isinstance(x, str) else x)
                    .apply(pd.Series).add_prefix("baseline_"))
        df_all = pd.concat([df_all.drop(columns=["baseline"]), expanded], axis=1)

    return df_all
