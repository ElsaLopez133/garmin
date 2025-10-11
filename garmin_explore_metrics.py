from garminconnect import Garmin
import json, os, csv
from datetime import date, timedelta, datetime
from tqdm import tqdm  # Progress bar library
import pandas as pd

def print_nested_keys(d, indent=0):
    """Recursively print all keys in a nested dictionary or list"""
    if isinstance(d, dict):
        for k, v in d.items():
            print("  " * indent + f"📂 {k}")
            print_nested_keys(v, indent + 1)
    elif isinstance(d, list) and d:
        print("  " * indent + f"[list of {len(d)}]")
        print_nested_keys(d[0], indent + 1)

def save_raw_api_data(api_func, args, prefix, output_dir):
    """Fetch data from an API function, save raw JSON, and optionally print nested keys."""
    try:
        data = api_func(*args)
        if not data:
            print(f"⚠️ No data for {prefix}")
            return None

        # Save to file
        raw_file = os.path.join(output_dir, f"raw_{prefix}.json")
        with open(raw_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        print(f"💾 Saved raw {prefix} data → {raw_file}")
        return data

    except Exception as e:
        print(f"❌ Error fetching {prefix}: {e}")
        return None

# 👉 Your Garmin login
EMAIL = input("your_email@example.com: ")
PASSWORD = input("your_password: ")

# ---- LOGIN ----
print("🔐 Logging in to Garmin Connect...")
api = Garmin(EMAIL, PASSWORD)
api.login()


# ---- OUTPUT DIRECTORY ----
OUTPUT_DIR = f"./data/garmin_data_explore"
os.makedirs(OUTPUT_DIR, exist_ok=True)
print(f"\n📂 Data will be saved in: {OUTPUT_DIR}\n")

# Pick a single date to explore
today = date.today()
start_date = today - timedelta(days=30)

# ---- Metrics to explore ----
metrics = {
    "hrv": ("get_hrv_data", [today.isoformat()]),
    "rhr": ("get_rhr_day", [today.isoformat()]),
    "sleep": ("get_sleep_data", [today.isoformat()]),
    "menstrual_calendar": ("get_menstrual_calendar_data", [start_date.isoformat(), today.isoformat()])
}

for name, (func_name, args) in metrics.items():
    print(f"\n🔍 Fetching {name} data...")
    func = getattr(api, func_name)  # dynamically get the function
    data = save_raw_api_data(func, args, name, OUTPUT_DIR)

    if data:
        print(f"\n🧭 Keys inside {name}:")
        print_nested_keys(data)