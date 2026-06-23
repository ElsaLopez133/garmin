# Garmin collector — web server (FastAPI)

A web version of the collector. Participants open a page on **any device** (phone,laptop, any OS), type their Garmin login, and the server downloads their data and saves a CSV. No download, no install.

## ⚠️ The trade-off (read this)

Unlike the desktop app, **the participant's password is sent to this server.** This reverses the original "we never see your password" guarantee. The app is written to handle it as safely as possible:

- the password is used **only in memory**, only during the login request;
- it is **never** written to disk, a database, or any log;
- for 2-factor auth, only the auth *resume-state* is kept in memory (not the password);
- the local reference to the password is dropped right after login.

But "not stored" is not the same as "never seen": while logging in, the server process does hold the plaintext password. So:
- run it behind **HTTPS** in production;
- keep the server private and patched;
- **get your DPO / ethics sign-off** before using it under Inria's name;

## Run it locally

```bash
source .venv/bin/activate
pip install fastapi "uvicorn[standard]" jinja2 python-multipart garminconnect pandas numpy
uvicorn collector.server.app:app --reload
```
Open <http://127.0.0.1:8000>. Test it with your own Garmin login first.

## Where finished CSVs go

The server picks based on environment variables:

- **`GARMIN_ENDPOINT_URL` set** → each CSV is POSTed to that URL (your Google Apps
  Script Drive endpoint, the same `upload_endpoint.gs` the desktop app uses) and **nothing
  is written to local disk**. Use this when hosting in the cloud.
- **unset** (local dev) → CSVs are saved to `collector/server/uploads/` (gitignored).

Either way each file is `garmin_data_<id>.csv` and already contains the metadata columns.

## Letting participants reach it

### Option A — tunnel (your computer must stay on)
Quick, free, no deploy. Run the app, then:
`cloudflared tunnel --url http://localhost:8000` (or `ngrok http 8000`). Share the
temporary HTTPS URL. CSVs land in your local `uploads/`. Good for a single collection
session; the link dies when you stop it.

### Option B — host it (always on, no computer needed)  ← you want this

Deploy to a free PaaS so the link works 24/7. Because cloud disks are wiped on restart,
**send CSVs to Drive** instead of local disk (set `GARMIN_ENDPOINT_URL`).

**Steps (Render free tier):**
1. Make sure `upload_endpoint.gs` is deployed and you have its URL + token
   (see `../BUILD.md` Part 1).
2. Push this repo to GitHub.
3. On <https://render.com> → **New → Web Service** → connect your repo.
4. Settings:
   - **Build command:** `pip install -r collector/server/requirements.txt`
   - **Start command:** `uvicorn collector.server.app:app --host 0.0.0.0 --port $PORT`
5. **Environment variables** (Render dashboard → Environment):
   - `GARMIN_ENDPOINT_URL` = your Apps Script web-app URL
   - `GARMIN_UPLOAD_TOKEN` = the same token as in the Apps Script
6. Deploy. Render gives you a permanent HTTPS URL like
   `https://garmin-cycle.onrender.com` — that's the link you share. HTTPS is automatic.

Railway and Fly.io work the same way (same build/start command + env vars).

> Keep secrets in env vars, **not** in the code — so they aren't committed to GitHub.
> Free tiers sleep after inactivity; the first request wakes them (slow first load).
> While someone is downloading, the auto-refreshing status page keeps the server awake.

## Things to know

- **Downloads are slow** (a year of data = thousands of Garmin API calls). The page shows
  a progress spinner and auto-refreshes; tell participants to keep the tab open. Jobs run
  in a background thread, so several people can run at once.
- **Rate limits:** many logins/downloads from one server IP can trip Garmin's 429s. Fine
  for a handful of people; another reason not to scale this version large.
- **State is in memory:** restarting the server drops in-flight jobs and pending MFA
  sessions (finished CSVs on disk are safe). Don't redeploy mid-collection.
- **No password logging:** keep it that way — don't enable debug/verbose request logging,
  and don't add an error tracker that captures local variables on that endpoint.

## How this maps to the rest of the project

- Download logic is shared with the desktop app via `collector/garmin_fetch.py` — one
  source of truth, so CSVs from the web server and the desktop app are identical.
- The Google Form (`collector/GOOGLE_FORM.md`) still works for consent + metadata; with
  the server you can skip the "download the app" section and just link to the web page.
  Participants still paste their shown ID into the form to link responses to CSVs.
