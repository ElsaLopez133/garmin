"""
Garmin Health Data Collector  —  participant-facing app.

A participant runs this on THEIR OWN computer. They type their own Garmin
credentials into the window; the credentials never leave their machine. The app
downloads their health data into the same CSV schema used by the study, then
uploads only that CSV (no email/password) to the study's Drive folder.

Build into a double-click app with PyInstaller (see BUILD.md).

Before building, fill in ENDPOINT_URL and UPLOAD_TOKEN below with the values
from your deployed Google Apps Script (see upload_endpoint.gs + BUILD.md).
"""

import base64
import threading
import uuid
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import requests
from garminconnect import Garmin, GarminConnectTooManyRequestsError

import tkinter as tk
from tkinter import messagebox, simpledialog

# ---------------------------------------------------------------------------
# CONFIG  — fill these in before building the app (see BUILD.md)
# ---------------------------------------------------------------------------
ENDPOINT_URL = "PASTE_YOUR_APPS_SCRIPT_WEB_APP_URL_HERE"
UPLOAD_TOKEN = "PASTE_THE_SHARED_SECRET_HERE"
DEFAULT_DAYS = 365


# ===========================================================================
# Garmin download logic  (mirrors garmin_download.py so the CSV is identical)
# ===========================================================================
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
    import time

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
            wait_seconds = base_delay * (2 ** (attempt - 1))
            time.sleep(wait_seconds)


def download_dataframe(api, days_back, status):
    """Download all metrics and return the merged DataFrame (same schema as the study CSV)."""
    today = date.today()
    start_date = today - timedelta(days=days_back)
    date_list = [start_date + timedelta(days=i) for i in range(days_back + 1)]

    hrv_list, rhr_list, sleep_list, cycle_list = [], [], [], []
    stress_list, body_battery_list, training_load_list, training_status_list = [], [], [], []

    # ---- HRV ----
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
                    "date": d.isoformat(),
                    "hrv_rmssd": s.get("lastNightAvg"),
                    "status": s.get("status"),
                    "weekly_avg": weekly_avg,
                    "lastnight_5min_high": s.get("lastNight5MinHigh"),
                    "baseline": baseline,
                    "feedback_phrase": s.get("feedbackPhrase"),
                })
                last_weekly_avg, last_baseline = weekly_avg, baseline
            else:
                hrv_list.append({"date": d.isoformat(), "hrv_rmssd": None, "status": None,
                                 "weekly_avg": last_weekly_avg, "lastnight_5min_high": None,
                                 "baseline": last_baseline, "feedback_phrase": None})
        except Exception:
            hrv_list.append({"date": d.isoformat(), "hrv_rmssd": None, "status": None,
                             "weekly_avg": None, "lastnight_5min_high": None,
                             "baseline": None, "feedback_phrase": None})

    # ---- Resting Heart Rate ----
    status("Downloading resting heart rate…")
    for d in date_list:
        try:
            data = api.get_rhr_day(d.isoformat())
            if data and "allMetrics" in data:
                rhr_entries = data["allMetrics"].get("metricsMap", {}).get("WELLNESS_RESTING_HEART_RATE", [])
                rhr_list.append({"date": d.isoformat(),
                                 "resting_hr": rhr_entries[0].get("value") if rhr_entries else None})
            else:
                rhr_list.append({"date": d.isoformat(), "resting_hr": None})
        except Exception:
            rhr_list.append({"date": d.isoformat(), "resting_hr": None})

    # ---- Sleep ----
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
                "avg_sleep_hr": s.get("avgHeartRate"),
                "avg_sleep_respiration": s.get("averageRespirationValue"),
                "avg_sleep_stress": s.get("avgSleepStress"),
                "sleep_score": s.get("sleepScores", {}).get("overall", {}).get("value"),
                "sleep_quality": s.get("sleepScores", {}).get("overall", {}).get("qualifierKey"),
                "sleep_feedback": s.get("sleepScoreFeedback"),
                "sleep_need_baseline": s.get("sleepNeed", {}).get("baseline"),
                "sleep_need_actual": s.get("sleepNeed", {}).get("actual"),
                "sleep_need_feedback": s.get("sleepNeed", {}).get("feedback"),
            })
        except Exception:
            continue

    # ---- Menstrual Cycle ----
    status("Downloading menstrual cycle…")
    calendar_entries = fetch_menstrual_calendar(api, start_date, today)
    calendar_days = build_calendar_days(calendar_entries)
    for d in date_list:
        cycle_list.append({"date": d.isoformat(),
                           "cycle_phase": calendar_days.get(d.isoformat(), "Not logged")})

    # ---- Stress ----
    status("Downloading stress…")
    for d in date_list:
        try:
            data = api.get_stress_data(d.isoformat())
            if data:
                stress_list.append({"date": d.isoformat(),
                                    "maxStressLevel": data.get("maxStressLevel"),
                                    "avgStressLevel": data.get("avgStressLevel")})
            else:
                stress_list.append({"date": d.isoformat(), "maxStressLevel": None, "avgStressLevel": None})
        except Exception:
            stress_list.append({"date": d.isoformat(), "maxStressLevel": None, "avgStressLevel": None})

    # ---- Body Battery ----
    status("Downloading body battery…")
    for d in date_list:
        try:
            data = api.get_body_battery(d.isoformat())
            if data and isinstance(data, list) and data[0]:
                bb = data[0]
                body_battery_list.append({"date": d.isoformat(),
                                          "charged": bb.get("charged"), "drained": bb.get("drained")})
            else:
                body_battery_list.append({"date": d.isoformat(), "charged": None, "drained": None})
        except Exception:
            body_battery_list.append({"date": d.isoformat(), "charged": None, "drained": None})

    # ---- Training Status / Load ----
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
                        "date": d.isoformat(),
                        "trainingStatus": e.get("trainingStatus"),
                        "weeklyTrainingLoad": e.get("weeklyTrainingLoad"),
                        "feedback": e.get("trainingStatusFeedbackPhrase"),
                        "dailyTrainingLoadAcute": acute.get("dailyTrainingLoadAcute"),
                        "acwrPercent": acute.get("acwrPercent"),
                        "acwrStatus": acute.get("acwrStatus"),
                        "acwrStatusFeedback": acute.get("acwrStatusFeedback"),
                        "dailyTrainingLoadChronic": acute.get("dailyTrainingLoadChronic"),
                        "minTrainingLoadChronic": acute.get("minTrainingLoadChronic"),
                        "maxTrainingLoadChronic": acute.get("maxTrainingLoadChronic"),
                        "dailyAcuteChronicWorkloadRatio": acute.get("dailyAcuteChronicWorkloadRatio"),
                    })
                else:
                    training_status_list.append({"date": d.isoformat(), **empty_status})
        except Exception:
            training_load_list.append({"date": d.isoformat(), **empty_load})
            training_status_list.append({"date": d.isoformat(), **empty_status})
            continue

    # ---- Merge ----
    status("Merging data…")
    dfs = {
        "hrv": pd.DataFrame(hrv_list), "rhr": pd.DataFrame(rhr_list),
        "sleep": pd.DataFrame(sleep_list), "cycle": pd.DataFrame(cycle_list),
        "stress": pd.DataFrame(stress_list), "body_battery": pd.DataFrame(body_battery_list),
        "training_load": pd.DataFrame(training_load_list), "training_status": pd.DataFrame(training_status_list),
    }
    for df in dfs.values():
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
    df_all = pd.concat(dfs.values(), axis=1).sort_index().reset_index()

    def flatten_dict_column(df, column_name, prefix):
        if column_name in df.columns:
            expanded = (df[column_name].dropna()
                        .apply(lambda x: eval(x) if isinstance(x, str) else x)
                        .apply(pd.Series).add_prefix(f"{prefix}_"))
            df = pd.concat([df.drop(columns=[column_name]), expanded], axis=1)
        return df

    df_all = flatten_dict_column(df_all, "baseline", "baseline")
    return df_all


# ===========================================================================
# Upload
# ===========================================================================
def upload_csv(df, participant_id):
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    payload = {
        "token": UPLOAD_TOKEN,
        "participant_id": participant_id,
        "filename": f"garmin_data_{participant_id}.csv",
        "data": base64.b64encode(csv_bytes).decode("ascii"),
    }
    resp = requests.post(ENDPOINT_URL, data=payload, timeout=120)
    resp.raise_for_status()
    if "ok" not in resp.text.lower():
        raise RuntimeError(f"Server rejected upload: {resp.text[:200]}")


# ===========================================================================
# GUI
# ===========================================================================
AGE_OPTIONS = ["— select —", "18–24", "25–34", "35–44", "45+"]
CONTRACEPTION_OPTIONS = ["— select —", "None / non-hormonal", "Hormonal pill",
                         "Hormonal IUD", "Implant / injection / ring", "Prefer not to say"]
REGULARITY_OPTIONS = ["— select —", "Regular", "Irregular", "Not sure"]


class CollectorApp:
    def __init__(self, root):
        self.root = root
        root.title("Garmin Health Data Collector")
        root.geometry("470x590")
        root.resizable(False, False)

        self.participant_id = uuid.uuid4().hex[:8]

        pad = {"padx": 14, "pady": 3}
        tk.Label(root, text="Garmin Health Data Collector", font=("", 14, "bold")).pack(pady=(12, 2))
        tk.Label(root, text="Your login stays on this computer and is never sent\n"
                            "to anyone. Only your health data is shared.",
                 fg="#555", justify="center").pack(pady=(0, 4))

        form = tk.Frame(root)
        form.pack(pady=4)

        tk.Label(form, text="Garmin email:").grid(row=0, column=0, sticky="e", **pad)
        self.email = tk.Entry(form, width=28)
        self.email.grid(row=0, column=1, **pad)

        tk.Label(form, text="Garmin password:").grid(row=1, column=0, sticky="e", **pad)
        self.password = tk.Entry(form, width=28, show="•")
        self.password.grid(row=1, column=1, **pad)

        # --- Metadata (written into the CSV; needed for the research) ---
        tk.Label(form, text="Age range:").grid(row=2, column=0, sticky="e", **pad)
        self.age = tk.StringVar(value=AGE_OPTIONS[0])
        tk.OptionMenu(form, self.age, *AGE_OPTIONS).grid(row=2, column=1, sticky="ew", **pad)

        tk.Label(form, text="Contraception:").grid(row=3, column=0, sticky="e", **pad)
        self.contraception = tk.StringVar(value=CONTRACEPTION_OPTIONS[0])
        tk.OptionMenu(form, self.contraception, *CONTRACEPTION_OPTIONS).grid(row=3, column=1, sticky="ew", **pad)

        tk.Label(form, text="Cycle regularity:").grid(row=4, column=0, sticky="e", **pad)
        self.regularity = tk.StringVar(value=REGULARITY_OPTIONS[0])
        tk.OptionMenu(form, self.regularity, *REGULARITY_OPTIONS).grid(row=4, column=1, sticky="ew", **pad)

        tk.Label(form, text="Garmin device model:").grid(row=5, column=0, sticky="e", **pad)
        self.device = tk.Entry(form, width=28)
        self.device.grid(row=5, column=1, **pad)

        tk.Label(root, text=f"Your anonymous ID: {self.participant_id}", fg="#555").pack(pady=(4, 0))

        self.consent = tk.IntVar()
        tk.Checkbutton(root, text="I consent to share my Garmin health data for this research",
                       variable=self.consent, wraplength=420, justify="left").pack(pady=6)

        self.button = tk.Button(root, text="Download & send my data", command=self.start)
        self.button.pack(pady=6)

        self.status_var = tk.StringVar(value="Ready.")
        tk.Label(root, textvariable=self.status_var, fg="#0a6").pack(pady=(4, 10))

    def status(self, msg):
        self.root.after(0, lambda: self.status_var.set(msg))

    def prompt_mfa(self):
        """Ask for the 2-factor code on the main thread and return it."""
        result = {}
        done = threading.Event()

        def ask():
            result["code"] = simpledialog.askstring(
                "Two-factor code",
                "Garmin sent you a verification code.\nEnter it here:",
                parent=self.root)
            done.set()

        self.root.after(0, ask)
        done.wait()
        return result.get("code") or ""

    def start(self):
        if not self.email.get().strip() or not self.password.get():
            messagebox.showwarning("Missing info", "Please enter your Garmin email and password.")
            return
        if not self.consent.get():
            messagebox.showwarning("Consent needed", "Please tick the consent box to continue.")
            return
        if self.contraception.get() == CONTRACEPTION_OPTIONS[0] or self.age.get() == AGE_OPTIONS[0]:
            messagebox.showwarning("Missing info", "Please select your age range and contraception.")
            return

        # Fixed window for everyone — set via DEFAULT_DAYS at the top of this file.
        days = DEFAULT_DAYS
        meta = {
            "participant_id": self.participant_id,
            "age_range": self.age.get(),
            "contraception": self.contraception.get(),
            "cycle_regularity": self.regularity.get() if self.regularity.get() != REGULARITY_OPTIONS[0] else "",
            "device_model": self.device.get().strip(),
        }
        self.button.config(state="disabled")
        threading.Thread(target=self.worker, args=(self.email.get().strip(),
                                                    self.password.get(), days, meta), daemon=True).start()

    def worker(self, email, password, days, meta):
        try:
            self.status("Logging in to Garmin…")
            api = Garmin(email=email, password=password, prompt_mfa=self.prompt_mfa)
            login_with_retry(api)

            df = download_dataframe(api, days, self.status)

            # Prepend the metadata as constant columns so each CSV is self-describing
            for key, value in meta.items():
                df[key] = value
            meta_cols = list(meta.keys())
            df = df[meta_cols + [c for c in df.columns if c not in meta_cols]]

            self.status("Uploading your data…")
            upload_csv(df, self.participant_id)

            self.status("Done! Thank you for participating. ✓")
            self.root.after(0, lambda: messagebox.showinfo(
                "Success", "Your data was sent successfully.\nThank you! You can close this window."))
        except Exception as e:
            self.status("Something went wrong.")
            msg = str(e)
            self.root.after(0, lambda: messagebox.showerror(
                "Error", f"Could not complete:\n\n{msg}\n\nPlease check your login and internet "
                         "connection, then try again."))
        finally:
            self.root.after(0, lambda: self.button.config(state="normal"))


def main():
    root = tk.Tk()
    CollectorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
