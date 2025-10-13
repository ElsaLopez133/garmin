from garminconnect import Garmin
import json, os, csv
from datetime import date, timedelta, datetime
from tqdm import tqdm  # Progress bar library
import pandas as pd
import getpass
import numpy as np

def print_nested_keys(d, indent=0):
    """Recursively print all keys in a nested dictionary or list"""
    if isinstance(d, dict):
        for k, v in d.items():
            print("  " * indent + f"📂 {k}")
            print_nested_keys(v, indent + 1)
    elif isinstance(d, list) and d:
        print("  " * indent + f"[list of {len(d)}]")
        print_nested_keys(d[0], indent + 1)

def fetch_menstrual_calendar(api, start_date, end_date):
    max_range_days = 90
    all_entries = []

    current_start = start_date
    while current_start < end_date:
        current_end = min(current_start + timedelta(days=max_range_days - 1), end_date)
        try:
            chunk = api.get_menstrual_calendar_data(current_start.isoformat(), current_end.isoformat())
            all_entries.extend(chunk.get('cycleSummaries', []))
        except Exception as e:
            print(f"⚠️ Failed to fetch from {current_start} to {current_end}: {e}")
        current_start = current_end + timedelta(days=1)

    return all_entries

# To run directly: "C:\Users\elsal\AppData\Roaming\Python\Python312\Scripts\garmin-backup.exe" elsa.lopez.133@outlook.es --password  'xxxxxxxxx' --backup-dir .\garmin_data
# 👉 Your Garmin login
EMAIL = input("Email: ")
PASSWORD = getpass.getpass("Password: ")

# ---- LOGIN ----
print("🔐 Logging in to Garmin Connect...")
api = Garmin(EMAIL, PASSWORD)
api.login()

# ---- SELECT NUMBER OF DAYS ----
while True:
    try:
        DAYS_BACK = int(input("How many days of data do you want to download? (e.g. 30, 90, 180): "))
        if DAYS_BACK <= 0:
            raise ValueError
        break
    except ValueError:
        print("⚠️ Please enter a positive number (e.g. 90).")


# ---- OUTPUT DIRECTORY ----
OUTPUT_DIR = f"./data/garmin_data_{DAYS_BACK}days"
os.makedirs(OUTPUT_DIR, exist_ok=True)
print(f"\n📂 Data will be saved in: {OUTPUT_DIR}\n")

today = date.today()
start_date = today - timedelta(days=DAYS_BACK)
date_list = [start_date + timedelta(days=i) for i in range(DAYS_BACK + 1)]

# ---- Initialize lists ----
hrv_list, rhr_list, sleep_list, cycle_list, stress_list, body_battery_list, training_load_list, training_status_list  = [], [], [], [], [], [], [], []

# ---- HRV ----
print("\n📈 Downloading HRV data...")
last_weekly_avg = None
last_baseline = None

for date in tqdm(date_list):
    try:
        data = api.get_hrv_data(date.isoformat())
        if data and 'hrvSummary' in data:
            summary = data['hrvSummary']
            weekly_avg = summary.get("weeklyAvg") or last_weekly_avg
            baseline = summary.get("baseline") or last_baseline
            
            hrv_list.append({
                "date": date.isoformat(),
                "hrv_rmssd": summary.get("lastNightAvg"),
                "status": summary.get("status"),
                "weekly_avg": weekly_avg,
                "lastnight_5min_high": summary.get("lastNight5MinHigh"),
                "baseline": baseline,
                "feedback_phrase": summary.get("feedbackPhrase"),
            })
            
            # Update last seen values
            last_weekly_avg = weekly_avg
            last_baseline = baseline
            
        else:
            hrv_list.append({
                "date": date.isoformat(),
                "hrv_rmssd": None,
                "status": None,
                "weekly_avg": last_weekly_avg,
                "lastnight_5min_high": None,
                "baseline": last_baseline,
                "feedback_phrase": None,
            })
    except Exception as e:
        print(f"Error fetching HRV for {date}: {e}")
        hrv_list.append({
            "date": date.isoformat(),
            "hrv_rmssd": None,
            "status": None,
            "weekly_avg": None,
            "lastnight_5min_high": None,
            "baseline": None,
            "feedback_phrase": None,
        })

# ---- Resting Heart Rate ----
print("\n❤️ Downloading Resting HR data...")

for date in tqdm(date_list):
    try:
        data = api.get_rhr_day(date.isoformat())
        if data and 'allMetrics' in data:
            rhr_entries = data['allMetrics'].get('metricsMap', {}).get('WELLNESS_RESTING_HEART_RATE', [])
            if rhr_entries:
                rhr_list.append({
                    "date": date.isoformat(),
                    "resting_hr": rhr_entries[0].get('value')
                })
            else:
                rhr_list.append({"date": date.isoformat(), "resting_hr": None})
        else:
            rhr_list.append({"date": date.isoformat(), "resting_hr": None})
    except Exception as e:
        print(f"Error fetching RHR for {date}: {e}")
        rhr_list.append({"date": date.isoformat(), "resting_hr": None})

# ---- Sleep ----
print("\n😴 Downloading Sleep data...")

for date in tqdm(date_list):
    try:
        data = api.get_sleep_data(date.isoformat())
        if not data or "dailySleepDTO" not in data:
            # No data → append zeroed sleep info
            sleep_list.append({
                "date": date.isoformat(),
                "total_sleep_hr": 0,
                "deep_sleep_hr": 0,
                "light_sleep_hr": 0,
                "rem_sleep_hr": 0,
                "awake_sleep_hr": 0,
                "avg_sleep_hr": 0,
                "avg_sleep_respiration": 0,
                "avg_sleep_stress": 0,
                "sleep_score": 0,
                "sleep_quality": None,
                "sleep_feedback": None,
                "sleep_need_baseline": 0,
                "sleep_need_actual": 0,
                "sleep_need_feedback": None
            })
            continue

        s = data["dailySleepDTO"]

        sleep_list.append({
            "date": date.isoformat(),
            "total_sleep_hr": round(s.get("sleepTimeSeconds", 0) / 3600, 2),
            "deep_sleep_hr": round(s.get("deepSleepSeconds", 0) / 3600, 2),
            "light_sleep_hr": round(s.get("lightSleepSeconds", 0) / 3600, 2),
            "rem_sleep_hr": round(s.get("remSleepSeconds", 0) / 3600, 2),
            "awake_sleep_hr": round(s.get("awakeSleepSeconds", 0) / 3600, 2),
            "avg_sleep_hr": s.get("avgHeartRate"),
            "avg_sleep_respiration": s.get("averageRespirationValue"),
            "avg_sleep_stress": s.get("avgSleepStress"),
            "sleep_score": s.get("sleepScores", {}).get("overall", {}).get("value"),
            "sleep_quality": s.get("sleepScores", {}).get("overall", {}).get("qualifierKey"),
            "sleep_feedback": s.get("sleepScoreFeedback"),
            "sleep_need_baseline": s.get("sleepNeed", {}).get("baseline"),
            "sleep_need_actual": s.get("sleepNeed", {}).get("actual"),
            "sleep_need_feedback": s.get("sleepNeed", {}).get("feedback")
        })

    except Exception as e:
        print(f"Error fetching sleep for {date}: {e}")
        continue

# ---- Menstrual Cycle ----
print("\n🌸 Downloading Menstrual Cycle data...")

# Create a lookup table: each date mapped to its phase
calendar_days = {}
calendar_entries = fetch_menstrual_calendar(api, start_date, today)
#print(calendar_entries)

for i, entry in enumerate(calendar_entries):
    start = datetime.fromisoformat(entry['startDate']).date()
    period_length = entry.get('periodLength', 1)
    fertile_start = entry.get('fertileWindowStart', 0)
    fertile_length = entry.get('lengthOfFertileWindow', 0)
    
    # Next cycle start if available
    next_start = None
    if i + 1 < len(calendar_entries):
        next_start = datetime.fromisoformat(calendar_entries[i + 1]["startDate"]).date()

    # --- Period ---
    for i in range(period_length):
        day = start + timedelta(days=i)
        calendar_days[day.isoformat()] = "Menstrual"

    # --- Follicular Phase (after period until fertile window) ---
    if fertile_start > period_length:
        follicular_start = start + timedelta(days=period_length)
        follicular_end = start + timedelta(days=fertile_start - 1)
        for j in range((follicular_end - follicular_start).days + 1):
            day = follicular_start + timedelta(days=j)
            if calendar_days.get(day.isoformat()) is None:
                calendar_days[day.isoformat()] = "Follicular"

    # --- Fertile Window & Ovulation ---
    fertile_window_start_date = start + timedelta(days=fertile_start)
    for i in range(fertile_length):
        day = fertile_window_start_date + timedelta(days=i)
        if calendar_days.get(day.isoformat()) is None:
            calendar_days[day.isoformat()] = "Fertile"

    # Ovulation = middle of fertile window
    if fertile_length > 0:
        ovulation_day = fertile_window_start_date + timedelta(days=fertile_length // 2)
        calendar_days[ovulation_day.isoformat()] = "Ovulation"

    # --- Luteal Phase (after ovulation until next period, approximate) ---
    luteal_start = ovulation_day + timedelta(days=1)
    # Assume luteal phase ends at 28 days cycle (can adjust if you have next period info)
    luteal_end = next_start - timedelta(days=1) if next_start else luteal_start + timedelta(days=13)
    for i in range((luteal_end - luteal_start).days + 1):
        day = luteal_start + timedelta(days=i)
        if calendar_days.get(day.isoformat()) is None:
            calendar_days[day.isoformat()] = "Luteal"

# Fill in the cycle list for the requested date range
cycle_list = []
for date in tqdm(date_list):
    phase = calendar_days.get(date.isoformat(), "Not logged")
    cycle_list.append({"date": date.isoformat(), "cycle_phase": phase})

# ---- Stress ----
print("\n💢 Downloading Stress data...")

for date in tqdm(date_list):
    try:
        data = api.get_stress_data(date.isoformat())
        if data:
            stress_list.append({
                "date": date.isoformat(),
                "maxStressLevel": data.get("maxStressLevel"),
                "avgStressLevel": data.get("avgStressLevel"),
            })
        else:
            stress_list.append({
                "date": date.isoformat(),
                "maxStressLevel": None,
                "avgStressLevel": None
            })
    except Exception as e:
        print(f"Error fetching stress for {date}: {e}")
        stress_list.append({
            "date": date.isoformat(),
            "maxStressLevel": None,
            "avgStressLevel": None
        })

# ---- Body Battery ----
print("\n🔋 Downloading Body Battery data...")

for date in tqdm(date_list):
    try:
        data = api.get_body_battery(date.isoformat())
        if data and isinstance(data, list) and data[0]:
            bb = data[0]
            body_battery_list.append({
                "date": date.isoformat(),
                "charged": bb.get("charged"),
                "drained": bb.get("drained"),
                #"avg_battery": np.mean([v for sub in bb.get("bodyBatteryValuesArray", []) for v in sub]) if bb.get("bodyBatteryValuesArray") else None
            })
        else:
            body_battery_list.append({"date": date.isoformat(), "charged": None, "drained": None})
    
    except Exception as e:
        print(f"Error fetching body battery for {date}: {e}")
        body_battery_list.append({"date": date.isoformat(), "charged": None, "drained": None})


# ---- Training Readiness / Effect ----
print("\n💪 Downloading Training Status data...")

for date in tqdm(date_list):
    try:
        data = api.get_training_status(date.isoformat())
        if data and "mostRecentTrainingLoadBalance" in data:
            load_map = data["mostRecentTrainingLoadBalance"].get("metricsTrainingLoadBalanceDTOMap", {})
            if load_map:
                # Get the first (and usually only) entry
                b = next(iter(load_map.values()))
                training_load_list.append({
                "date": date.isoformat(),
                "monthlyLoadAerobicLow": b.get("monthlyLoadAerobicLow"),
                "monthlyLoadAerobicHigh": b.get("monthlyLoadAerobicHigh"),
                "monthlyLoadAnaerobic": b.get("monthlyLoadAnaerobic"),
                "monthlyLoadAerobicLowTargetMin": b.get("monthlyLoadAerobicLowTargetMin"),
                "monthlyLoadAerobicLowTargetMax": b.get("monthlyLoadAerobicLowTargetMax"),
                "monthlyLoadAerobicHighTargetMin": b.get("monthlyLoadAerobicHighTargetMin"),
                "monthlyLoadAerobicHighTargetMax": b.get("monthlyLoadAerobicHighTargetMax"),
                "monthlyLoadAnaerobicTargetMin": b.get("monthlyLoadAnaerobicTargetMin"),
                "monthlyLoadAnaerobicTargetMax": b.get("monthlyLoadAnaerobicTargetMax"),
                "trainingBalanceFeedbackPhrase": b.get("trainingBalanceFeedbackPhrase")
                })
            else:
                training_load_list.append({
                "date": date.isoformat(),
                "monthlyLoadAerobicLow": None,
                "monthlyLoadAerobicHigh": None,
                "monthlyLoadAnaerobic": None,
                "monthlyLoadAerobicLowTargetMin": None,
                "monthlyLoadAerobicLowTargetMax": None,
                "monthlyLoadAerobicHighTargetMin": None,
                "monthlyLoadAerobicHighTargetMax": None,
                "monthlyLoadAnaerobicTargetMin": None,
                "monthlyLoadAnaerobicTargetMax": None,
                "trainingBalanceFeedbackPhrase": None,
                })
                
        if data and "mostRecentTrainingStatus" in data:
            latest = data["mostRecentTrainingStatus"].get("latestTrainingStatusData", {})
            if latest:
                # Get the first (and usually only) entry
                last_entry = next(iter(latest.values()))
                training_status_list.append({
                "date": date.isoformat(),
                "trainingStatus": last_entry.get("trainingStatus"),
                "weeklyTrainingLoad": last_entry.get("weeklyTrainingLoad"),
                "feedback": last_entry.get("trainingStatusFeedbackPhrase"),
                "dailyTrainingLoadAcute": last_entry.get("acuteTrainingLoadDTO", {}).get("dailyTrainingLoadAcute"),
                "acwrPercent": last_entry.get("acuteTrainingLoadDTO", {}).get("acwrPercent"),
                "acwrStatus": last_entry.get("acuteTrainingLoadDTO", {}).get("acwrStatus"),
                "acwrStatusFeedback": last_entry.get("acuteTrainingLoadDTO", {}).get("acwrStatusFeedback"),
                "dailyTrainingLoadChronic": last_entry.get("acuteTrainingLoadDTO", {}).get("dailyTrainingLoadChronic"),
                "minTrainingLoadChronic": last_entry.get("acuteTrainingLoadDTO", {}).get("minTrainingLoadChronic"),
                "maxTrainingLoadChronic": last_entry.get("acuteTrainingLoadDTO", {}).get("maxTrainingLoadChronic"),
                "dailyAcuteChronicWorkloadRatio": last_entry.get("acuteTrainingLoadDTO", {}).get("dailyAcuteChronicWorkloadRatio")
                })
            else:
                training_status_list.append({
                "date": date.isoformat(),
                "trainingStatus": None,
                "weeklyTrainingLoad": None,
                "feedback": None,
                "dailyTrainingLoadAcute": None,
                "acwrPercent": None,
                "acwrStatus": None,
                "acwrStatusFeedback": None,
                "dailyTrainingLoadChronic": None,
                "minTrainingLoadChronic": None,
                "maxTrainingLoadChronic": None,
                "dailyAcuteChronicWorkloadRatio": None
                })
                
    except Exception as e:
        print(f"Error fetching training data for {date}: {e}")
        training_load_list.append({
        "date": date.isoformat(),
        "monthlyLoadAerobicLow": None,
        "monthlyLoadAerobicHigh": None,
        "monthlyLoadAnaerobic": None,
        "monthlyLoadAerobicLowTargetMin": None,
        "monthlyLoadAerobicLowTargetMax": None,
        "monthlyLoadAerobicHighTargetMin": None,
        "monthlyLoadAerobicHighTargetMax": None,
        "monthlyLoadAnaerobicTargetMin": None,
        "monthlyLoadAnaerobicTargetMax": None,
        "trainingBalanceFeedbackPhrase": None,
        })
        training_status_list.append({
        "date": date.isoformat(),
        "trainingStatus": None,
        "weeklyTrainingLoad": None,
        "feedback": None,
        "dailyTrainingLoadAcute": None,
        "acwrPercent": None,
        "acwrStatus": None,
        "acwrStatusFeedback": None,
        "dailyTrainingLoadChronic": None,
        "minTrainingLoadChronic": None,
        "maxTrainingLoadChronic": None,
        "dailyAcuteChronicWorkloadRatio": None
        })
        continue


# ---- SAVE JSON ----
print("\n💾 Saving JSON files...")
with open(f"{OUTPUT_DIR}/hrv_data.json", "w") as f:
    json.dump(hrv_list, f, indent=2)

with open(f"{OUTPUT_DIR}/rhr_data.json", "w") as f:
    json.dump(rhr_list, f, indent=2)

with open(f"{OUTPUT_DIR}/sleep_data.json", "w") as f:
    json.dump(sleep_list, f, indent=2)

with open(f"{OUTPUT_DIR}/cycle_data.json", "w") as f:
    json.dump(cycle_list, f, indent=2)

with open(f"{OUTPUT_DIR}/body_battery_data.json", "w") as f:
    json.dump(body_battery_list, f, indent=2)

with open(f"{OUTPUT_DIR}/stress_data.json", "w") as f:
    json.dump(stress_list, f, indent=2)

with open(f"{OUTPUT_DIR}/training_load_data.json", "w") as f:
    json.dump(training_load_list, f, indent=2)
    
with open(f"{OUTPUT_DIR}/training_status_data.json", "w") as f:
    json.dump(training_status_list, f, indent=2)
    
# ---- MERGE DATA ----
print("\n📊 Merging data into CSV...")

# --- Convert lists to DataFrames ---
dfs = {
    "hrv": pd.DataFrame(hrv_list),
    "rhr": pd.DataFrame(rhr_list),
    "sleep": pd.DataFrame(sleep_list),
    "cycle": pd.DataFrame(cycle_list),
    "stress": pd.DataFrame(stress_list),
    "body_battery": pd.DataFrame(body_battery_list),
    "training_load": pd.DataFrame(training_load_list),
    "training_status": pd.DataFrame(training_status_list)
}

# --- Ensure all DataFrames have 'date' as datetime index ---
# Standardize date index
for df in dfs.values():
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)

df_all = pd.concat(dfs.values(), axis=1).sort_index().reset_index()

# --- Flatten nested dict columns like 'baseline' ---
def flatten_dict_column(df, column_name, prefix):
    if column_name in df.columns:
        expanded = (
            df[column_name]
            .dropna()
            .apply(lambda x: eval(x) if isinstance(x, str) else x)
            .apply(pd.Series)
            .add_prefix(f"{prefix}_")
        )
        df = pd.concat([df.drop(columns=[column_name]), expanded], axis=1)
    return df

df_all = flatten_dict_column(df_all, "baseline", "baseline")

df_all.to_csv(f"{OUTPUT_DIR}/garmin_data_{DAYS_BACK}days.csv", index=False)
print(f"\n✅ Done! Saved to {OUTPUT_DIR}/garmin_data_{DAYS_BACK}days.csv")


