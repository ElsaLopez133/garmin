# Cycle-phase analysis — the follicular→luteal transition

This folder locates the **wearable follicular→luteal transition** (the biometric
"luteal onset") from Garmin-style signals, and validates it against real hormones.
The work is a four-stage arc — from *does a signal exist?* to *does it run live?* —
backed by a shared model library (`cyclelib/`) so every stage uses the same core.

> **What the model estimates.** A dated, uncertain estimate of the physiological
> shift from follicular-like to luteal-like biometrics (RHR/HRV/respiration/sleep).
> It is a signal transition for femtech / cycle-aware sports analytics — **not** a
> clinical ovulation test. External hormone validation shows the boundary trails the
> LH surge by a few days, so it should not be called exact ovulation without an
> explicit lag correction.

## The four stages (reading order)

| # | Notebook | Question | Method | Headline result |
|---|----------|----------|--------|-----------------|
| 1 | [`01_discovery.ipynb`](./01_discovery.ipynb) | Is the follicular→luteal shift detectable at all? | Aligned-cycle profiles → binary anchor classifier → `P(luteal)` trajectory → change-point (Steps 0–4) | OOF AUC ≈ 0.85; a real, per-cycle low→high step |
| 2 | [`02_model_hsmm.ipynb`](./02_model_hsmm.ipynb) | Frame it as what it is — a latent state sequence | One-boundary HSMM, Gaussian emissions, soft luteal-duration prior; MAP boundary **+ posterior** | median luteal 13 d, 65% within ±2 d of Garmin's rule, with per-cycle uncertainty |
| 3 | [`03_validate_mcphases.ipynb`](./03_validate_mcphases.ipynb) | Is it true vs **hormones**, not just Garmin's rule? | Same HSMM transferred to [mcPHASES](https://physionet.org/content/mcphases/) (LH/E3G/PdG assays); Gaussian + discriminative emissions | vs labelled luteal onset: 0-d bias, MAD 2 d, 65% within ±2 d; +3.5 d behind the LH peak (the expected biometric lag) |
| 4 | [`04_live_detection.ipynb`](./04_live_detection.ipynb) | Can it run **online**, with no future knowledge? | Causal features, CUSUM detector, and a forward-filtered HSMM, scored on a two-sided cost | fires within ~2 d of the full-information boundary; forward HSMM beats CUSUM |

Stages 1–3 are **retrospective** (they see the whole completed cycle, so they can
use luteal length as a constraint). Stage 4 is the **filtering** version that updates
`P(transition has happened by today)` as days arrive.

`01_discovery.ipynb` has a detailed design companion in
[`discovery_plan.html`](theory/discovery_plan.html) (the problem framing, the semi-supervised
reframe, and the Step 0–5 rationale).

## `cyclelib/` — the shared model (single source of truth)

```
cyclelib/
  hsmm.py           one-boundary HSMM: logmvn, fit_gaussian, boundary_search, segment_gaussian
  emissions.py      anchor extraction + generative (Gaussian) / discriminative (P(luteal)) emissions
  features.py       robust median/MAD standardization — global and per-person
  truth_mcphases.py mcPHASES hormonal ground truth (LH-peak ovulation, luteal onset)
```

The key seam: `boundary_search` is **emission-agnostic** — it consumes precomputed
per-day follicular/luteal log-scores, so the exact same search drives both the
Gaussian emissions and the discriminative `P(luteal | x)` classifier.

## Supporting scripts

- `combine_participants.py` — merge multiple participant CSVs (for the collector).
- `garmin_cycle_check.py` — CLI sanity check of a cycle CSV (phase counts, cycle
  starts/lengths) with a phases-vs-RHR/HRV plot. `python analysis/garmin_cycle_check.py [csv]`.
- `garmin_ovulation_check.py` — CLI: is ovulation detectable from RHR/HRV? (paired
  test + aligned plot).

## How to run

Run everything **from the repo root** so `data/` and `figs/` paths resolve, in the
project venv (`source .venv/bin/activate`). Stages 1, 2 and 4 read the owner's record
(`data/garmin_data_730days/…`); stage 3 reads the mcPHASES export under
`data/external/…`. The notebooks put both the repo root (for `preprocessing`) and
`analysis/` (for `cyclelib`) on `sys.path`, so a plain "Restart & Run All" works.

## Open threads / what's next

- **Honest out-of-fold validation on mcPHASES.** The pooled emissions in stage 3 are
  fit in-sample on all anchors; leave-one-participant-out would make the numbers
  defensible rather than mildly optimistic. *(Highest-value gap.)*
- **Temperature.** The single strongest ovulation biomarker in the literature, and
  currently unused. mcPHASES ships skin temperature — add biphasic / baseline-relative
  temperature features (not raw temperature as another Gaussian dimension).
- **Carry uncertainty through.** The HSMM produces a boundary *posterior* but
  everything downstream collapses to a point; surface the credible interval end-to-end.
- **Cold-start population prior for the live detector** — start population-informed
  (pooled emissions/hazard), shrink to the individual as cycles accumulate, so the
  warm-up cycles become usable.
