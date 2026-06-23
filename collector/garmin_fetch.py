"""
Shared Garmin download logic (no GUI / no server dependency).

Produces a DataFrame in the same schema as garmin_download.py / the study CSV.
Imported by both the desktop collector and the FastAPI server so the fetch logic
lives in one place.
"""

import time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from garminconnect import GarminConnectTooManyRequestsError


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
        current_start = current_end + timedelta(days=1)
    return all_entries


def build_calendar_days(calendar_entries):
    calendar_days = {}

    complete_cycle_lengths = []
    for i in range(len(calendar_entries) - 1):
        s_i = datetime.fromisoformat(calendar_entries[i]["startDate"]).date()
        s_next = datetime.fromisoformat(calendar_entries[i + 1]["startDate"]).date()
        complete_cycle_lengths.append((s_next - s_i).days)
    avg_cycle_len = round(sum(complete_cycle_lengths) / len(complete_cycle_lengths)) if complete_cycle_lengths else 28

    for idx, entry in enumerate(calendar_entries):
        start = datetime.fromisoformat(entry["startDate"]).date()
        period_len = int(entry.get("periodLength", 1) or 1)

        fertile_start = entry.get("fertileWindowStart", None)
        fertile_len = int(entry.get("lengthOfFertileWindow", 0) or 0)

        if idx + 1 < len(calendar_entries):
            next_start = datetime.fromisoformat(calendar_entries[idx + 1]["startDate"]).date()
        else:
            next_start = start + timedelta(days=avg_cycle_len)

        # 1) Menstrual
        for d in range(period_len):
            day = start + timedelta(days=d)
            calendar_days[day.isoformat()] = "Menstrual"

        # Fertile window
        if fertile_start is not None and fertile_len > 0:
            fertile_window_start = start + timedelta(days=int(fertile_start))
            fertile_window_end = fertile_window_start + timedelta(days=fertile_len - 1)
        else:
            cycle_len = (next_start - start).days
            fertile_len = 7
            mid = start + timedelta(days=cycle_len // 2)
            fertile_window_start = mid - timedelta(days=fertile_len // 2)
            fertile_window_end = fertile_window_start + timedelta(days=fertile_len - 1)
            min_start = start + timedelta(days=period_len)
            max_end = next_start - timedelta(days=1)
            if fertile_window_start < min_start:
                fertile_window_start = min_start
                fertile_window_end = fertile_window_start + timedelta(days=fertile_len - 1)
            if fertile_window_end > max_end:
                fertile_window_end = max_end
                fertile_window_start = fertile_window_end - timedelta(days=fertile_len - 1)

        # 2) Follicular: after period -> day before fertile window
        fol_start = start + timedelta(days=period_len)
        fol_end = fertile_window_start - timedelta(days=1)
        day = fol_start
        while day <= fol_end:
            calendar_days[day.isoformat()] = "Follicular"
            day += timedelta(days=1)

        # 3) Fertile window split: first half Follicular, second half Luteal
        fertile_days = [fertile_window_start + timedelta(days=i) for i in range(fertile_len)]
        split = fertile_len // 2
        for i, day in enumerate(fertile_days):
            if calendar_days.get(day.isoformat()) != "Menstrual":
                calendar_days[day.isoformat()] = "Follicular" if i < split else "Luteal"

        # 4) Luteal: after fertile window -> day before next period
        lut_start = fertile_window_end + timedelta(days=1)
        lut_end = next_start - timedelta(days=1)
        day = lut_start
        while day <= lut_end:
            if calendar_days.get(day.isoformat()) != "Menstrual":
                calendar_days[day.isoformat()] = "Luteal"
            day += timedelta(days=1)

    return calendar_days


def login_with_retry(api, max_attempts=5, base_delay=15):
    """Retry Garmin login when Garmin Connect temporarily rate-limits auth."""
    def is_rate_limit_error(exc):
        current = exc
        while current is not None:
            message = str(current).lower()
            if "429" in message or "too many requests" in message or "rate limit" in message:
                return True
            current = current.__cause__ or current.__context__
        return False

    for attempt in range(1, max_attempts + 1):
        try:
            api.login()
            return
        except Exception as exc:
            if not (isinstance(exc, GarminConnectTooManyRequestsError) or is_rate_limit_error(exc)):
                raise
            if attempt == max_attempts:
                raise
            time.sleep(base_delay * (2 ** (attempt - 1)))


def download_dataframe(api, days_back, status=lambda m: None):
    """Download all metrics and return the merged DataFrame (study CSV schema)."""
    today = date.today()
    start_date = today - timedelta(days=days_back)
    date_list = [start_date + timedelta(days=i) for i in range(days_back + 1)]

    hrv_list, rhr_list, sleep_list, cycle_list = [], [], [], []
    stress_list, body_battery_list, training_load_list, training_status_list = [], [], [], []

    status("Downloading HRV…")
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

    status("Downloading resting heart rate…")
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

    status("Downloading sleep…")
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

    status("Downloading menstrual cycle…")
    calendar_entries = fetch_menstrual_calendar(api, start_date, today)
    calendar_days = build_calendar_days(calendar_entries)
    for d in date_list:
        cycle_list.append({"date": d.isoformat(), "cycle_phase": calendar_days.get(d.isoformat(), "Not logged")})

    status("Downloading stress…")
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

    status("Downloading body battery…")
    for d in date_list:
        try:
            data = api.get_body_battery(d.isoformat())
            if data and isinstance(data, list) and data[0]:
                bb = data[0]
                body_battery_list.append({"date": d.isoformat(), "charged": bb.get("charged"), "drained": bb.get("drained")})
            else:
                body_battery_list.append({"date": d.isoformat(), "charged": None, "drained": None})
        except Exception:
            body_battery_list.append({"date": d.isoformat(), "charged": None, "drained": None})

    status("Downloading training status…")
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

    status("Merging data…")
    dfs = {"hrv": pd.DataFrame(hrv_list), "rhr": pd.DataFrame(rhr_list), "sleep": pd.DataFrame(sleep_list),
           "cycle": pd.DataFrame(cycle_list), "stress": pd.DataFrame(stress_list),
           "body_battery": pd.DataFrame(body_battery_list), "training_load": pd.DataFrame(training_load_list),
           "training_status": pd.DataFrame(training_status_list)}
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
