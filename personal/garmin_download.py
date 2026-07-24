import getpass
import json
import os
import sys
from pathlib import Path

from garminconnect import Garmin
from tqdm import tqdm

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from collector.garmin_fetch import ALL_METRICS, download_dataframe, login_with_retry


def prompt_days_back():
    while True:
        try:
            days_back = int(input("How many days of data do you want to download? (e.g. 30, 90, 180): "))
            if days_back <= 0:
                raise ValueError
            return days_back
        except ValueError:
            print("Please enter a positive number (e.g. 90).")


def prompt_mfa():
    return input("MFA code: ").strip()


def save_cycle_json(df, output_dir):
    cycle_cols = ["date", "is_period", "garmin_predicted_fertile"]
    existing_cols = [c for c in cycle_cols if c in df.columns]
    if not existing_cols:
        return

    cycle_rows = df[existing_cols].copy()
    if "date" in cycle_rows.columns:
        cycle_rows["date"] = cycle_rows["date"].astype(str)

    with open(output_dir / "cycle_data.json", "w") as f:
        json.dump(cycle_rows.to_dict(orient="records"), f, indent=2)


def main():
    email = input("Email: ")
    password = getpass.getpass("Password: ")
    days_back = prompt_days_back()

    output_dir = REPO_ROOT / "data" / f"garmin_data_{days_back}days"
    os.makedirs(output_dir, exist_ok=True)
    print(f"\nData will be saved in: {output_dir}\n")

    print("Logging in to Garmin Connect...")
    api = Garmin(email, password, prompt_mfa=prompt_mfa)
    login_with_retry(api)

    df = download_dataframe(
        api,
        days_back,
        status=lambda message: print(message),
        metrics=ALL_METRICS,
        progress=lambda items, label: tqdm(items, desc=label),
    )

    csv_path = output_dir / f"garmin_data_{days_back}days.csv"
    df.to_csv(csv_path, index=False)
    save_cycle_json(df, output_dir)

    print(f"\nDone. Saved to {csv_path}")


if __name__ == "__main__":
    main()
