"""
Garmin collector — web server version (FastAPI).

Participants open a web page on ANY device (phone, laptop, any OS), type their
Garmin login, and the server downloads their data and saves a CSV. No download,
no install.

⚠️ PRIVACY TRADE-OFF: unlike the desktop app, the password is sent to this server.
We handle it as safely as possible:
  - it is used only in memory, only during the login request
  - it is NEVER written to disk, a database, or logs
  - for 2FA we keep only the auth resume-state in memory, not the password
  - run behind HTTPS in production

Run locally:
    python -m venv .venv
    source .venv/bin/activate
    pip install -r collector/server/requirements.txt
    uvicorn collector.server.app:app --reload
Then open http://127.0.0.1:8000
"""

import base64
import os
import secrets
import sys
import threading
import time
import uuid
from pathlib import Path

import requests
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from garminconnect import Garmin

# Make the shared fetch module importable whether run as a module or a script
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import garmin_fetch  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = Path(os.environ.get("GARMIN_UPLOAD_DIR", BASE_DIR / "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_DAYS = 365

# Where finished CSVs go. If GARMIN_ENDPOINT_URL is set (e.g. your Google Apps
# Script Drive endpoint), CSVs are POSTed there and nothing is written to disk —
# this is what lets the server run on an ephemeral cloud host. If it's unset
# (local dev), CSVs are saved to UPLOAD_DIR instead.
ENDPOINT_URL = os.environ.get("GARMIN_ENDPOINT_URL", "")
UPLOAD_TOKEN = os.environ.get("GARMIN_UPLOAD_TOKEN", "")


def _save_dataframe(df, participant_id):
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    if ENDPOINT_URL:
        payload = {"token": UPLOAD_TOKEN, "participant_id": participant_id,
                   "filename": f"garmin_data_{participant_id}.csv",
                   "data": base64.b64encode(csv_bytes).decode("ascii")}
        resp = requests.post(ENDPOINT_URL, data=payload, timeout=120)
        resp.raise_for_status()
        if "ok" not in resp.text.lower():
            raise RuntimeError(f"Drive upload rejected: {resp.text[:200]}")
    else:
        (UPLOAD_DIR / f"garmin_data_{participant_id}.csv").write_bytes(csv_bytes)

app = FastAPI()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# In-memory only. Nothing here is persisted.
PENDING_MFA = {}   # token -> {api, client_state, days, meta}
JOBS = {}          # job_id -> {status, done, error, participant_id}

AGE_OPTIONS = ["18–24", "25–34", "35–44", "45+"]
CONTRACEPTION_OPTIONS = ["None / non-hormonal", "Hormonal pill", "Hormonal IUD",
                         "Implant / injection / ring", "Prefer not to say"]
REGULARITY_OPTIONS = ["Regular", "Irregular", "Not sure"]
OVERNIGHT_OPTIONS = ["Most nights", "Some nights", "Rarely"]
CONDITION_OPTIONS = ["PCOS", "Endometriosis", "Thyroid condition", "None", "Prefer not to say"]


def _ensure_display_name(api):
    """garminconnect's return_on_mfa login path can return before loading the profile,
    leaving api.display_name = None — which breaks every endpoint whose URL contains it
    (resting HR, sleep, ...) with a 403 on '.../None'. Populate it if missing."""
    if getattr(api, "display_name", None):
        return
    # garth.profile lazily loads /userprofile-service/socialProfile and caches it
    # (this is the same source garminconnect's own login uses).
    profile = api.garth.profile
    if isinstance(profile, dict):
        api.display_name = profile.get("displayName")
        api.full_name = profile.get("fullName")


def _start_download_job(api, days, meta):
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"status": "Starting…", "done": False, "error": None,
                    "participant_id": meta["participant_id"]}

    def run():
        try:
            _ensure_display_name(api)
            df = garmin_fetch.download_dataframe(
                api, days, status=lambda m: JOBS[job_id].update(status=m))
            for key, value in meta.items():
                df[key] = value
            meta_cols = list(meta.keys())
            df = df[meta_cols + [c for c in df.columns if c not in meta_cols]]
            _save_dataframe(df, meta["participant_id"])
            JOBS[job_id].update(status="Done", done=True)
        except Exception as e:  # noqa: BLE001
            JOBS[job_id].update(status="Error", done=True, error=str(e))

    threading.Thread(target=run, daemon=True).start()
    return job_id


def _is_rate_limit(exc):
    """True if the exception chain looks like a Garmin 429 / rate-limit."""
    cur = exc
    while cur is not None:
        m = str(cur).lower()
        if "429" in m or "too many requests" in m or "rate limit" in m:
            return True
        cur = cur.__cause__ or cur.__context__
    return False


# --- Login throttle + circuit breaker -------------------------------------
# Garmin rate-limits its sign-in (SSO) endpoint per source IP. On a shared host
# (e.g. Render) every participant logs in from the SAME outbound IP, so a burst
# of sign-ins — or repeated "try again" clicks after a failure — can get the
# whole server temporarily blocked. To stay under the limit we:
#   1. serialize logins (only one at a time),
#   2. keep a minimum gap between consecutive logins, and
#   3. once Garmin returns a 429, stop attempting for a cooldown window instead
#      of hammering and extending the ban.
_LOGIN_LOCK = threading.Lock()
_MIN_LOGIN_GAP = float(os.environ.get("GARMIN_MIN_LOGIN_GAP", "60"))               # seconds
_RATE_LIMIT_COOLDOWN = float(os.environ.get("GARMIN_RATE_LIMIT_COOLDOWN", "1800"))  # seconds
_last_login_ts = 0.0      # monotonic time of the last login attempt
_blocked_until = 0.0      # monotonic time before which we refuse to attempt


class _RateLimited(Exception):
    """Raised when our own circuit breaker is open (we won't even call Garmin)."""

    def __init__(self, retry_after):
        super().__init__("rate limited")
        self.retry_after = retry_after


def _throttled_login(email, password):
    """Log in to Garmin under a global throttle + circuit breaker.

    Returns (api, login_result). Raises _RateLimited if the breaker is open, or
    re-raises the underlying login error (after opening the breaker on a 429).
    """
    global _last_login_ts, _blocked_until
    with _LOGIN_LOCK:
        now = time.monotonic()
        if now < _blocked_until:
            raise _RateLimited(_blocked_until - now)
        gap = now - _last_login_ts
        if gap < _MIN_LOGIN_GAP:
            time.sleep(_MIN_LOGIN_GAP - gap)
        api = Garmin(email=email, password=password, return_on_mfa=True)
        try:
            result = api.login()
        except Exception as exc:
            if _is_rate_limit(exc):
                _blocked_until = time.monotonic() + _RATE_LIMIT_COOLDOWN
            raise
        finally:
            _last_login_ts = time.monotonic()
        return api, result


def _index_ctx(extra=None):
    ctx = {"default_days": DEFAULT_DAYS, "ages": AGE_OPTIONS,
           "contraceptions": CONTRACEPTION_OPTIONS, "regularities": REGULARITY_OPTIONS,
           "overnight_options": OVERNIGHT_OPTIONS, "condition_options": CONDITION_OPTIONS}
    if extra:
        ctx.update(extra)
    return ctx


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", _index_ctx())


@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return templates.TemplateResponse(request, "privacy.html")


@app.post("/start", response_class=HTMLResponse)
def start(request: Request, email: str = Form(...), password: str = Form(...),
          age_range: str = Form(...),
          contraception: str = Form(...), cycle_regularity: str = Form(""),
          device_model: str = Form(""), wears_overnight: str = Form(""),
          conditions: list[str] = Form(default=[]),
          consent: str = Form(None)):
    # Fixed server-side; participants cannot change how much history is pulled.
    days = DEFAULT_DAYS
    if not consent:
        return templates.TemplateResponse(
            request, "index.html",
            _index_ctx({"error": "Please tick the consent box to continue."}), status_code=400)

    participant_id = uuid.uuid4().hex[:8]
    meta = {"participant_id": participant_id, "age_range": age_range,
            "contraception": contraception, "cycle_regularity": cycle_regularity,
            "device_model": device_model.strip(), "wears_overnight": wears_overnight,
            "conditions": "; ".join(conditions)}

    try:
        api, result = _throttled_login(email, password)
    except _RateLimited as e:  # our circuit breaker is open — we didn't even call Garmin
        wait_min = max(1, round(e.retry_after / 60))
        msg = (f"Garmin is temporarily limiting sign-ins from this server because of too many "
               f"recent attempts. This is not a problem with your account — please wait about "
               f"{wait_min} minutes and try again.")
        return templates.TemplateResponse(request, "index.html",
                                          _index_ctx({"error": msg}), status_code=429)
    except Exception as e:  # noqa: BLE001
        if _is_rate_limit(e):
            msg = ("Garmin is temporarily limiting sign-ins because of too many recent "
                   "attempts. This is not a problem with your account — please wait about "
                   "30 minutes and try again.")
        else:
            msg = "Could not log in to Garmin. Please double-check your email and password and try again."
        return templates.TemplateResponse(request, "index.html",
                                          _index_ctx({"error": msg}), status_code=400)
    finally:
        # Drop the local reference to the password as soon as login is attempted.
        password = None

    # MFA needed?
    if isinstance(result, tuple) and result and result[0] == "needs_mfa":
        token = secrets.token_urlsafe(16)
        PENDING_MFA[token] = {"api": api, "client_state": result[1], "days": days, "meta": meta}
        return templates.TemplateResponse(request, "mfa.html", {"token": token})

    job_id = _start_download_job(api, days, meta)
    return RedirectResponse(url=f"/status/{job_id}", status_code=303)


@app.post("/mfa", response_class=HTMLResponse)
def mfa(request: Request, token: str = Form(...), code: str = Form(...)):
    entry = PENDING_MFA.pop(token, None)
    if not entry:
        return templates.TemplateResponse(
            request, "index.html",
            _index_ctx({"error": "Your session expired. Please start again."}), status_code=400)
    try:
        entry["api"].resume_login(entry["client_state"], code.strip())
    except Exception as e:  # noqa: BLE001
        return templates.TemplateResponse(
            request, "mfa.html",
            {"token": token, "error": f"That code didn't work: {e}"}, status_code=400)

    job_id = _start_download_job(entry["api"], entry["days"], entry["meta"])
    return RedirectResponse(url=f"/status/{job_id}", status_code=303)


@app.get("/status/{job_id}", response_class=HTMLResponse)
def status(request: Request, job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "status.html", {"job": job, "job_id": job_id})
