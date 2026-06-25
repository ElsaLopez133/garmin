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
import sys
import threading
import uuid
from pathlib import Path

import requests
from garminconnect import Garmin

import tkinter as tk
from tkinter import messagebox, simpledialog

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import garmin_fetch  # noqa: E402

# ---------------------------------------------------------------------------
# CONFIG  — fill these in before building the app (see BUILD.md)
# ---------------------------------------------------------------------------
ENDPOINT_URL = "PASTE_YOUR_APPS_SCRIPT_WEB_APP_URL_HERE"
UPLOAD_TOKEN = "PASTE_THE_SHARED_SECRET_HERE"
DEFAULT_DAYS = 365


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
            garmin_fetch.login_with_retry(api)

            df = garmin_fetch.download_dataframe(api, days, self.status)

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
