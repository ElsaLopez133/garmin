from garminconnect import Garmin, GarminConnectTooManyRequestsError
import json, os
from datetime import date, timedelta
import getpass
import time
from tqdm import tqdm

# ---------- CONFIG ----------

EMAIL = input("Email Garmin: ")
PASSWORD = getpass.getpass("Password: ")

OUTPUT_DIR = "./data/garmin_keys_explore"
os.makedirs(OUTPUT_DIR, exist_ok=True)

TEXT_FILE = os.path.join(OUTPUT_DIR, "garmin_keys_summary.txt")
JSON_FILE = os.path.join(OUTPUT_DIR, "garmin_keys_summary.json")

today = date.today()
start_date = today - timedelta(days=30)

# ---------- FUNCTIONS ----------

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
        lines.append("  " * indent + f"[list]")
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
            if not (
                isinstance(exc, GarminConnectTooManyRequestsError)
                or is_rate_limit_error(exc)
            ):
                raise
            if attempt == max_attempts:
                raise
            wait_seconds = base_delay * (2 ** (attempt - 1))
            print(
                f"⚠️ Garmin rate-limited login ({exc}). "
                f"Retrying in {wait_seconds} seconds "
                f"({attempt}/{max_attempts})..."
            )
            time.sleep(wait_seconds)


# ---------- LOGIN ----------

print("🔐 Logging in to Garmin Connect...")
api = Garmin(EMAIL, PASSWORD)
login_with_retry(api)

# ---------- METRICS TO EXPLORE ----------

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

# ---------- RUN EXPLORATION ----------

results_json = {}

with open(TEXT_FILE, "w", encoding="utf-8") as f:
    for name, (func_name, args) in tqdm(metrics.items(), desc="Exploring Garmin API"):
        print(f"\n🔍 Exploring {name}...")
        text, structure = explore_endpoint(api, func_name, args, name)
        print(text)
        f.write(text + "\n")
        if structure:
            results_json[name] = structure

# Save JSON version
with open(JSON_FILE, "w", encoding="utf-8") as f:
    json.dump(results_json, f, indent=2, ensure_ascii=False)

print(f"\n✅ Saved key structures to:\n - {TEXT_FILE}\n - {JSON_FILE}")
