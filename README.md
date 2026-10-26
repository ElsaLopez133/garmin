# Garmin Cycle Analytics

Download Garmin health data (HRV, resting heart rate, sleep, stress, body battery, training load, menstrual cycle), merge it into a single CSV, visualize it, and build ML models to **detect menstrual cycle phases and ovulation** from biometric signals.

The project has three parts:

1. **`personal/`** — download and explore *your own* Garmin data.
2. **`collector/`** — gather data from *other consenting participants* (Garmin data-export upload, web server, or desktop app), so models generalize beyond one person.
3. **`analysis/`** — verify the data and run the cycle/ovulation analysis + ML pipeline.

---

## Repository structure

```
personal/      garmin_download.py         download your own data -> data/garmin_data_{N}days/
               garmin_plot.py             plot it -> figs/
               garmin_explore_metrics.py  dump the API response structure
               slice_garmin_data.py       cut shorter-window CSVs out of a long one
preprocessing/ cycle_phases.py            derive Early-follicular / Late-luteal anchors
               outliers.py                flag confounded days (illness / alcohol / travel)
analysis/      01_discovery.ipynb         is the follicular→luteal shift detectable? (baseline)
               02_model_hsmm.ipynb        one-boundary HSMM ovulation estimate + posterior
               03_validate_mcphases.ipynb validate the HSMM vs real (LH-surge) ovulation
               04_live_detection.ipynb    online / forward-filtered detector
               cyclelib/                  shared model library (hsmm, emissions, features, truth)
               theory/                    HTML companion docs (design plan, HSMM maths)
               garmin_cycle_check.py, garmin_ovulation_check.py, combine_participants.py
collector/     garmin_fetch.py            shared download engine (API flows)
               garmin_export.py           Garmin "Export Your Data" archive -> study CSV
               fetch_uploads.py           sync uploads from Drive -> data/participants/
               upload_endpoint.gs         Google Apps Script (upload page + Drive endpoint)
               upload_page.html           participant upload page (served by the Apps Script)
               privacy_page.html          its privacy policy page
               desktop/                   tkinter double-click app
               server/                    FastAPI web app (deployed on Render) + templates
tests/         pytest suite (cyclelib, combine_participants, garmin_export)
data/  figs/   outputs (gitignored)
```

---

## Installation

Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-extras          # full env: core + server + analysis + dev extras
uv run pytest                 # run the tests
```

Dependencies are defined in `pyproject.toml`: a shared core plus `[server]`, `[analysis]`
and `[dev]` optional groups, with exact versions locked in `uv.lock`. Plain `uv sync`
installs only the core (download + collector); use `--all-extras`, or a single group like
`--extra analysis`, for the rest. Render installs the server with `pip install .[server]`.

Run scripts **from the repo root** so relative `data/`/`figs/` paths resolve. The
notebooks locate `data/` themselves and run from any directory.

---

## 1. Your own data (`personal/`)

```bash
uv run python personal/garmin_download.py        # prompts: email, password, number of days
uv run python personal/garmin_plot.py            # prompts: number of days (data must exist)
uv run python personal/garmin_explore_metrics.py # explore the API response structure
uv run python personal/slice_garmin_data.py      # 60/90/180/365-day CSVs from the 730-day one
```

`garmin_download.py` saves a merged CSV to `data/garmin_data_{N}days/`. Credentials are
entered at runtime and never stored (no login token is cached either).

---

## 2. Collecting from participants (`collector/`)

Three ways to take part. All of them ask the same consent and questionnaire (age range,
contraception, cycle regularity, device, overnight wear, conditions), give the participant a
random ID, and store the result in a **Google Drive folder** through the Apps Script in
`upload_endpoint.gs`:

- **Garmin export upload** (no password): the participant requests Garmin's *Export Your
  Data* archive and uploads the zip on the Apps Script web page (`upload_page.html`). Their
  browser keeps only the daily health-summary files, strips account/device IDs and free-text
  notes, and sends that reduced zip. `garmin_export.py` turns it into the study CSV (with many
  more daily metrics than the API flows).
- **Web server** (`collector/server/`): a FastAPI page, deployed on Render, where the
  participant signs in with their Garmin login; the password is used only in memory. Downloads
  HRV, resting HR, sleep and cycle data via `garmin_fetch.py`.
- **Desktop app** (`collector/desktop/`): the same download, run on the participant's own
  computer so the password never leaves it. Built per-OS by the GitHub Actions workflow.

The Apps Script is not deployed from this repo: paste `upload_endpoint.gs` and the two HTML
files into the script project and deploy a new version (setup steps are at the top of the
`.gs` file).

### Retrieving the collected data

Set your endpoint + token once in a `.env` file (copy `.env.example` → `.env`), then:

```bash
uv run python collector/fetch_uploads.py     # sync new uploads -> data/participants/
```

It downloads only uploads you don't have yet. Export zips are kept in
`data/participant_exports/` and converted to `garmin_data_<id>.csv` next to the other CSVs.

---

## Preprocessing (`preprocessing/`)

The collector writes only raw facts (`is_period`, `garmin_predicted_fertile`). Turning
those into modelling inputs is a separate stage, so collection → preprocessing → analysis
stay decoupled and the modelling choices below are tunable in one place.

- **cycle_phases.py** — `derive_cycle_phases(df, anchor_days=...)` builds the
  **Early-follicular** / **Late-luteal** anchor windows around each logged period.
  Window length is a modelling choice (default 5–6 days), not a download-time fact.
- **outliers.py** — `flag_confounders(df, ...)` marks **confounded days** (illness,
  alcohol, travel, hard training) that shift the biometrics for a day or two unrelated
  to the cycle. It tests each signal's residual from a local rolling median — so the
  normal luteal RHR rise is preserved — combining a per-signal **Hampel filter** with a
  robust **Mahalanobis distance** that catches the multi-signal illness signature. It
  adds `is_confounded` / `confound_score`; `apply_confound_mask` then NaNs those days'
  signals so downstream median imputation absorbs them (flag, don't delete). The
  threshold is picked empirically in the masking sweep of `01_discovery.ipynb` (Step B).

```bash
uv run python preprocessing/cycle_phases.py data/…/….csv -a 5      # add anchor labels
uv run python preprocessing/outliers.py     data/…/….csv --mask -o clean.csv
```

---

## 3. Analysis (`analysis/`)

```bash
uv run python analysis/combine_participants.py   # stack participant CSVs + QC -> data/combined/
uv run python analysis/garmin_cycle_check.py     data/participants/garmin_data_<id>.csv
uv run python analysis/garmin_ovulation_check.py data/participants/garmin_data_<id>.csv
```

- **garmin_cycle_check.py** — verify cycle phases (summary + plot).
- **garmin_ovulation_check.py** — test whether ovulation is detectable from RHR/HRV
  (paired pre/post test + aligned plot).
- **combine_participants.py** — merge all participants, print a QC triage table, and write `data/combined/{all_participants,ovulation_eligible,participant_summary}.csv`.

### The four-stage notebook arc

The analysis is a sequence from *does a signal exist?* to *does it run live?*, all built on
the shared **`cyclelib/`** library so every stage uses the same one-boundary HSMM.
See **`analysis/README.md`** for the full walkthrough.

- **`01_discovery.ipynb`** — *baseline.* Is the follicular→luteal shift detectable at all?
  Aligned-cycle profiles → a binary follicular-vs-luteal anchor classifier → a per-day
  `P(luteal)` trajectory → per-cycle **change-point**. Design companion:
  `analysis/theory/discovery_plan.html`.
- **`02_model_hsmm.ipynb`** — the boundary as a **hidden semi-Markov model**
  (`Menstruation → Follicular → Luteal`). Multivariate-Gaussian emissions per state plus an
  explicit **luteal-duration prior** — the semi-Markov piece a plain HMM lacks (its geometric
  dwell can't represent a ~13-day luteal phase). Yields a MAP ovulation day *and* a posterior
  over the boundary (per-cycle confidence).
- **`03_validate_mcphases.ipynb`** — validate the HSMM against the **LH surge** (real hormonal
  ovulation), not just Garmin's calendar rule, using the public
  [mcPHASES](https://physionet.org/content/mcphases/) dataset (Fitbit biometrics + Mira hormone
  assays). `cyclelib/truth_mcphases.py` maps the Fitbit signals onto our Garmin schema
  (`resting_hr`, `hrv_rmssd`, `avg_sleep_respiration`) and extracts the per-cycle LH-peak day.
  The dataset lives under `data/external/` (gitignored). The boundary tracks labelled luteal
  onset and trails the LH peak by ~3.5 days (the expected biometric lag); the hormonal luteal
  length independently supports the model's 13-day prior.
- **`04_live_detection.ipynb`** — can it run **online**, with no future knowledge? Causal
  features, a CUSUM detector, and a **forward-filtered HSMM** that updates
  `P(transition has happened by today)` as days arrive. Maths companion:
  `analysis/theory/hsmm_filter_explanation.html`.

**`cyclelib/`** is the single source of truth for the model: `hsmm.py` (one-boundary boundary
search + Gaussian emissions), `emissions.py` (anchor extraction + generative/discriminative
emissions), `features.py` (robust median/MAD standardization), `truth_mcphases.py` (mcPHASES
hormonal ground truth).

---

## Data flow

```
your own data:
  personal/garmin_download.py ──> data/garmin_data_{N}days/ ──────────┐
                                                                      │
participants:                                                         │
  export upload / server / desktop ──> Google Drive                   │
  fetch_uploads.py (+ garmin_export.py) ──> data/participants/        │
  combine_participants.py ──> data/combined/ ─────────────────────────┤
                                                                      ▼
                         preprocessing/ (phase anchors, confounder masking)
                                                                      │
                                                                      ▼
                         analysis notebooks 01–04 (via cyclelib/)
```

---

## Notes

- `data/`, `figs/`, `.venv/`, `.env`, and most `collector/*.md` docs are gitignored.
- Collector secrets (`GARMIN_ENDPOINT_URL`, `GARMIN_UPLOAD_TOKEN`) live in `.env` / Render
  env vars, never in code.
- The collector stores only raw truth (`is_period`, `garmin_predicted_fertile`).
  `preprocessing/cycle_phases.py` derives the Early-follicular / Late-luteal anchors, and
  `preprocessing/outliers.py` flags confounded days, before any analysis.
- Garmin's fertile window is a 7-day band; it estimates ovulation at 13 days before the
  cycle's final day, i.e. **2 days before the fertile-window end** — that offset is used
  as the external check for the biometric transition estimate.
