"""
Explore the structure of the Garmin API responses (keys only, no values).

Prompts for your Garmin login, calls each endpoint once, and writes the nested
key structure to data/garmin_keys_explore/garmin_keys_summary.{txt,json}.

Run (from the repo root):
    uv run python personal/garmin_explore_metrics.py
"""

import getpass
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from garminconnect import Garmin
from tqdm import tqdm

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from collector.garmin_fetch import login_with_retry, verify_login

OUTPUT_DIR = REPO_ROOT / "data" / "garmin_keys_explore"


def extract_structure(data):
    """Return a nested structure of keys without values."""
    if isinstance(data, dict):
        return {k: extract_structure(v) for k, v in data.items()}
    elif isinstance(data, list):
        if not data:
            return ["<empty list>"]
        return [extract_structure(data[0])]
    else:
        return "<value>"


def format_structure_text(data, indent=0):
    """Convert nested structure to indented text with 📂 icons."""
    lines = []
    if isinstance(data, dict):
        for k, v in data.items():
            lines.append("  " * indent + f"📂 {k}")
            lines.extend(format_structure_text(v, indent + 1))
    elif isinstance(data, list):
        lines.append("  " * indent + "[list]")
        if data and isinstance(data[0], (dict, list)):
            lines.extend(format_structure_text(data[0], indent + 1))
    return lines


def explore_endpoint(api, func_name, args, prefix):
    """Fetch data from Garmin API and return key structure in both text and dict formats."""
    try:
        func = getattr(api, func_name)
        data = func(*args)
        if not data:
            return f"⚠️ No data for {prefix}\n", None

        structure = extract_structure(data)
        text = f"\n🔹 {prefix.upper()} ({func_name})\n" + "\n".join(format_structure_text(structure)) + "\n"
        return text, structure

    except Exception as e:
        return f"❌ Error fetching {prefix}: {e}\n", None


def main():
    email = input("Email Garmin: ")
    password = getpass.getpass("Password: ")

    print("🔐 Logging in to Garmin Connect...")
    api = Garmin(email, password, prompt_mfa=lambda: input("MFA code: ").strip())
    login_with_retry(api)
    verify_login(api)

    today = date.today()
    start_date = today - timedelta(days=30)
    metrics = {
        "hrv": ("get_hrv_data", [today.isoformat()]),
        "rhr": ("get_rhr_day", [today.isoformat()]),
        "sleep": ("get_sleep_data", [today.isoformat()]),
        "stress": ("get_stress_data", [today.isoformat()]),
        "body_battery": ("get_body_battery", [today.isoformat()]),
        "training_readiness": ("get_training_readiness", [today.isoformat()]),
        "training_status": ("get_training_status", [today.isoformat()]),
        "menstrual_calendar": ("get_menstrual_calendar_data", [start_date.isoformat(), today.isoformat()]),
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    text_file = OUTPUT_DIR / "garmin_keys_summary.txt"
    json_file = OUTPUT_DIR / "garmin_keys_summary.json"

    results_json = {}
    with open(text_file, "w", encoding="utf-8") as f:
        for name, (func_name, args) in tqdm(metrics.items(), desc="Exploring Garmin API"):
            print(f"\n🔍 Exploring {name}...")
            text, structure = explore_endpoint(api, func_name, args, name)
            print(text)
            f.write(text + "\n")
            if structure:
                results_json[name] = structure

    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(results_json, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Saved key structures to:\n - {text_file}\n - {json_file}")


if __name__ == "__main__":
    main()
