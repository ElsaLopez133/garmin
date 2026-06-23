# Garmin Cycle Analytics

Download Garmin health data (HRV, resting heart rate, sleep, stress, body battery, training load, menstrual cycle), merge it into a single CSV, visualize it, and build ML models to **detect menstrual cycle phases and ovulation** from biometric signals.

The project has three parts:

1. **`personal/`** — download and explore *your own* Garmin data.
2. **`collector/`** — gather data from *other consenting participants* (desktop app **or** web server), so models generalize beyond one person.
3. **`analysis/`** — verify the data and run the cycle/ovulation analysis + ML pipeline.

---

## Repository structure

```
personal/      garmin_download.py, garmin_explore_metrics.py, garmin_plot.py
analysis/      garmin_cycle_check.py, garmin_ovulation_check.py,
               combine_participants.py, garmin_correlations.ipynb, Pipeline-model.ipynb
collector/     garmin_fetch.py            shared download engine
               fetch_uploads.py           pull collected CSVs from Drive
               upload_endpoint.gs         Google Apps Script (Drive endpoint)
               desktop/                   tkinter double-click app + build docs
               server/                    FastAPI web app (deployed) + templates
data/  figs/   outputs (gitignored)
```

---

## Installation

Python 3.10+ (the venv uses 3.12).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run all scripts **from the repo root** so relative `data/`/`figs/` paths resolve.

---

## 1. Your own data (`personal/`)

```bash
python personal/garmin_download.py        # prompts: email, password, number of days
python personal/garmin_plot.py            # prompts: number of days (data must exist)
python personal/garmin_explore_metrics.py # explore the API response structure
```

`garmin_download.py` saves per-metric JSON + a merged CSV to `data/garmin_data_{N}days/`. Credentials are entered at runtime, never stored.

---

## 2. Collecting from participants (`collector/`)

Two front-ends, sharing one download engine (`garmin_fetch.py`), so the CSV is identical:

- **Desktop app** (`collector/desktop/`): a double-click app participants run locally; their password never leaves their machine. Built per-OS with PyInstaller (or via the GitHub Actions workflow). See `collector/desktop/BUILD.md`.
- **Web server** (`collector/server/`): a FastAPI page participants open in any browser (phone, laptop, any OS). Deployed on Render. See `collector/server/README.md`.

Both write a CSV (with consent + metadata columns) to a **Google Drive folder** via the Apps Script in `upload_endpoint.gs`.

### Retrieving the collected data

Set your endpoint + token once in a `.env` file (copy `.env.example` → `.env`), then:

```bash
python collector/fetch_uploads.py     # download all participant CSVs -> data/participants/
```

---

## 3. Analysis (`analysis/`)

```bash
python analysis/combine_participants.py   # stack participant CSVs + QC -> data/combined/
python analysis/garmin_cycle_check.py     data/participants/garmin_data_<id>.csv
python analysis/garmin_ovulation_check.py data/participants/garmin_data_<id>.csv
```

- **garmin_cycle_check.py** — verify cycle phases (summary + plot).
- **garmin_ovulation_check.py** — test whether ovulation is detectable from RHR/HRV
  (paired pre/post test + aligned plot).
- **combine_participants.py** — merge all participants, print a QC triage table, and write `data/combined/{all_participants,ovulation_eligible,participant_summary}.csv`.
- **Pipeline-model.ipynb** — feature engineering + cycle-phase classification.

---

## Data flow

```
personal/garmin_download.py ─┐
collector (app / server) ────┼─> CSVs (data/, Drive)
                             │
fetch_uploads.py ────────────┘-> data/participants/
combine_participants.py ───────-> data/combined/  ─> analysis + Pipeline-model.ipynb
```

---

## Notes

- `data/`, `figs/`, `.venv/`, `.env`, and most `collector/*.md` docs are gitignored.
- Collector secrets (`GARMIN_ENDPOINT_URL`, `GARMIN_UPLOAD_TOKEN`) live in `.env` / Render
  env vars, never in code.
- Menstrual phases (Menstrual / Follicular / Luteal) are derived from Garmin cycle summaries; the fertile window anchors ovulation (the Follicular→Luteal boundary).
