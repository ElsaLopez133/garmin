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
preprocessing/ cycle_phases.py            derive Early-follicular / Late-luteal anchors
               outliers.py                flag confounded days (illness / alcohol / travel)
analysis/      garmin_cycle_check.py, garmin_ovulation_check.py,
               combine_participants.py, garmin_correlations.ipynb, Pipeline-model.ipynb,
               cycle_transition_model.ipynb   change-point ovulation estimate (baseline)
               cycle_hsmm_model.ipynb         hidden semi-Markov ovulation estimate
               mcphases_truth.py              hormonal ground truth from the mcPHASES dataset
               validate_mcphases.ipynb        validate the HSMM against real (LH-surge) ovulation
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
  threshold is picked empirically in the transition notebook's masking sweep (Step B).

```bash
python preprocessing/cycle_phases.py data/…/….csv -a 5      # add anchor labels
python preprocessing/outliers.py     data/…/….csv --mask -o clean.csv
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

### Ovulation-timing models

Both take the preprocessed data (phase anchors + confounder masking) and estimate the
follicular→luteal boundary (ovulation) per cycle, checked against Garmin's rule (ovulation =
fertile-window end − 2 days).

- **cycle_transition_model.ipynb** — *baseline.* Per-cycle **change-point** on an AUC-weighted
  composite of RHR/HRV/respiration/sleep-stress: find the day that best splits each cycle's
  post-period signal into a low then a high level. Includes a confounder-masking threshold sweep.
- **cycle_hsmm_model.ipynb** — the same boundary as a **hidden semi-Markov model**
  (`Menstruation → Follicular → Luteal`). Multivariate-Gaussian emissions per state (seeded
  semi-supervised from the anchors) plus an explicit **luteal-duration prior** — the semi-Markov
  piece a plain HMM lacks (its geometric dwell can't represent a ~13-day luteal phase). Yields a
  MAP ovulation day *and* a posterior over the boundary (per-cycle confidence). On the owner's
  record it slightly beats the change-point baseline (tighter luteal spread, offset centered on 0).

### External validation on hormonal truth

The models above could only be checked against Garmin's *own* calendar rule (two algorithms
agreeing, not truth). **`validate_mcphases.ipynb`** scores the HSMM against the **LH surge** —
real hormonal ovulation — using the public [mcPHASES](https://physionet.org/content/mcphases/)
dataset (Fitbit biometrics + Mira hormone assays, 42 participants). `mcphases_truth.py` maps the
Fitbit signals onto our Garmin schema (`resting_hr`, `hrv_rmssd`, `avg_sleep_respiration`;
Garmin's sleep-stress has no clean equivalent) and extracts the per-cycle LH-peak ovulation day.
The dataset lives under `data/external/` (gitignored). Headline: with **pooled** emissions on
per-person-standardized signals the boundary lands ~1 day after the LH surge (within-2 days ≈ 57%,
within-3 ≈ 76%), and the hormonal luteal length (13.3 d) independently confirms the model's
13-day duration prior. Honest caveats — Fitbit `resting_hr` transfers poorly (inverted vs Garmin)
and raw skin temperature needs baseline-relative handling — are documented in the notebook.

---

## Data flow

```
personal/garmin_download.py ─┐
collector (app / server) ────┼─> CSVs (data/, Drive)
                             │
fetch_uploads.py ────────────┘-> data/participants/
combine_participants.py ───────-> data/combined/  ─┐
                                                    ├─> preprocessing/ (phase anchors,
                                                    │     confounder masking)
                                                    └─> analysis + Pipeline-model.ipynb
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
