# Cycle phase & follicular→luteal transition — modeling plan

Companion doc for [`cycle_transition_model.ipynb`](./cycle_transition_model.ipynb).

This describes the approach for reconstructing menstrual cycle phases from Garmin
biometrics, with the primary goal of **locating the follicular→luteal transition
(ovulation)**. It is intentionally built step by step; the notebook currently
implements Steps 0–4.

---

## 1. The problem, and the one constraint that shapes everything

We want to know, for any day, which cycle phase it belongs to — and above all,
**where the follicular phase ends and the luteal phase begins** (i.e. ovulation).

The constraint: **the only real ground truth we have is period timing.** It is
self-logged and reliable. Everything anchored to it is trustworthy; everything
else is inference.

Concretely, period timing is preserved as its own boolean (`is_period`), while
`cycle_phase` stores the reproductive-phase anchor label:

| Label              | Meaning                                             | Trust        |
| ------------------ | --------------------------------------------------- | ------------ |
| `is_period`        | Menses / bleeding days (known)                      | Ground truth |
| `Early follicular` | Fixed window starting on period day 1; **overlaps menses** | Ground truth |
| `Late luteal`      | Fixed window right **before** the next menses       | Ground truth |
| `Not logged`       | The middle of the cycle (mid-follicular → fertile → early luteal) | **Unlabeled** |
| `garmin_predicted_fertile` (column) | Garmin's fertile-window guess      | **Not** truth — never used as a label |

The naive approach — a classifier over only the anchored labels — throws away the
middle and never sees the boundary it's supposed to find. So we reframe.

## 2. The reframe: it's a semi-supervised transition-location problem

The data already has the right shape:

- **Two confident, well-separated endpoints** (`Early follicular`, including period
  days, and `Late luteal`)
  that bracket the transition.
- **An unlabeled middle** (`Not logged`) where the transition actually happens.

So the task is **not** "classify a day into a bucket." It is:

> Learn what confident-follicular vs confident-luteal biometrics look like, then
> use that to find *where the switch happens* inside the unlabeled middle.

The transition is a **latent variable we infer**, not a label we need.

**Why this is physiologically sound.** The follicular→luteal transition is a real
*step change*, not a gradual drift. After ovulation the corpus luteum raises
progesterone, which persistently lifts **resting heart rate**, **respiration
rate**, and **body temperature**, and tends to **lower HRV**. So the model is
detecting a level shift — and indeed the existing `Pipeline-model.ipynb` already
found `resting_hr_slope14`, `avg_sleep_respiration_slope*`, and
`hrv_rmssd_slope14` to be its most important features. We're formalizing what the
data was already telling us.

## 3. Data structure (verified)

- ~731 daily rows over 2 years (`data/garmin_data_730days/garmin_data_730days.csv`).
- **27 cycles**, lengths 23–32 days (median 28).
- Each cycle is a deterministic, period-anchored sequence:
  `Early follicular` (with `is_period=True` on bleeding days) → `Not logged` →
  `Late luteal` → next `Early follicular`.
- Follicular length varies cycle to cycle; **luteal length is stable (~12–14 d)** —
  this is the key fact we exploit for both alignment and validation.

## 4. The pipeline, step by step

### Step 0 — Confirm the signal exists *(implemented)*

Before modeling, verify the step is real. Align every cycle by cycle-day and plot
mean RHR / respiration / HRV:

1. **Forward** (days since period start) — ovulation timing varies, so the step is
   smeared but should still be visible.
2. **Backward** (days before next period) — since luteal length is stable, the step
   should be **sharper** here.
3. **Per-cycle, baseline-normalized** — subtract each cycle's own early-follicular
   mean to remove the months-long RHR drift (fitness, illness) and expose the
   *within-cycle* shift.

**Decision gate:** a clean step → the rest of the pipeline works well. A weak/noisy
step → lean harder on normalization / a composite signal before modeling, and
lower expectations on timing precision.

### Step 1 — Binary anchor classifier

Train **`Early follicular` (follicular-like, including period days) vs `Late luteal`
(luteal-like)** on the confident rows *only*. `is_period` is handled separately
(it's already known), and the `Not logged` middle is held out of training. Two well-separated endpoints make a
much cleaner decision boundary than the old 3-class problem.

- Features: the biometric signals plus lagged/rolling/slope/diff versions (past-only),
  as in the existing pipeline.
- Prefer **L1 / elastic-net** regularization to prune the many correlated
  lag/roll windows into an interpretable set.
- **Normalize each cycle to its own follicular baseline** so the model learns the
  within-cycle shift, not absolute levels.

#### How Step 1 is evaluated (and why)

The whole evaluation is engineered to answer one question **honestly**: *"Shown a cycle it
has never seen, how well can the model tell a follicular day from a luteal day?"* Honesty
matters because Steps 2–4 rely on the model behaving sensibly on `Not logged` days it was
never trained on.

**The pipeline** (`SimpleImputer(median)` → `StandardScaler` → L1 `LogisticRegression`) runs
as one object, which is the key anti-leakage detail: during cross-validation the imputer's
medians and the scaler's mean/sd are learned **only from the training cycles**, never from the
held-out cycle. Preprocessing before splitting would leak test information into those statistics.

- **Median imputation** — features have NaNs (a `slope7` needs several prior days). Median, not
  mean, because biometric distributions are skewed with outliers (a sick day spikes RHR).
- **Standardization is mandatory with L1** — the L1 penalty acts on raw coefficient magnitude,
  so features must share a scale (z-scores) or the penalty would judge them by their units, not
  their usefulness.
- **L1 logistic regression** minimizes `log-loss + (1/C)·Σ|wⱼ|`. The absolute-value penalty
  drives many coefficients to *exactly zero*, giving automatic feature selection — it collapses
  each cluster of correlated lag/roll/slope windows to one representative. `C` is the *inverse*
  regularization strength (small `C` = fewer surviving features). `class_weight="balanced"` is
  harmless insurance (classes are ~even, 189 vs 182).

**Leave-one-cycle-out CV** (`LeaveOneGroupOut`, `groups=cycle_id`) is the crux. Days within a
cycle are highly autocorrelated (today's RHR ≈ yesterday's), so ordinary random k-fold would put
adjacent days of the *same* cycle in both train and test — the model would "remember" that
cycle's level and score misleadingly high. Grouping by cycle forbids this: each of the 27 folds
holds out one entire cycle and trains on the other 26 — exactly the deployment scenario.

**Tuning `C`** — `GridSearchCV` tries 12 log-spaced values (0.01–10) and picks the one with the
best mean leave-one-cycle-out score. We optimize **ROC-AUC, not accuracy**, because (a) AUC is
threshold-independent and (b) Steps 2–4 use the *probability ranking* `P(luteal)`, not hard
labels — we want the `C` that best *ranks* days, not the one best at one arbitrary cutoff.

**Out-of-fold predictions** — `cross_val_predict` re-runs the leave-one-cycle-out loop and, for
each cycle, records the `P(luteal)` from the model trained on the *other* 26. Every row's
probability therefore comes from a model that never saw its cycle. These OOF probabilities are
the honest signal; thresholding at 0.5 gives hard labels for the report/confusion matrix.

**Metrics:**
- **ROC-AUC** (on the OOF probabilities) is the headline: the probability that a random luteal
  day is scored higher than a random follicular day. It measures whether the probability
  *trajectory rises through the cycle* — precisely what Step 3's crossing-point needs. 0.5 =
  chance, 1.0 = perfect ranking.
- **Classification report** (precision/recall/F1) describes behavior at the single 0.5 threshold;
  it can disagree with AUC if 0.5 is a poorly placed cutoff.
- **Confusion matrix** shows *error direction* — e.g. follicular days mislabeled luteal would
  drag Step 3's estimated ovulation earlier.

**Honest caveat:** `C` is chosen using folds from all cycles and then evaluated with the same
leave-one-cycle-out scheme, so the OOF AUC is a *mild* over-estimate. A fully rigorous version
would nest the `C`-search inside each outer fold; with one scalar hyperparameter and 27 cycles
the effect is small.

#### Step 1 findings

Run on 26 complete cycles (312 balanced anchor rows after complete-cycle filtering):

- **Out-of-fold AUC ≈ 0.85** (CV 0.87), balanced ~78% precision/recall — a solid
  follicular-vs-luteal separator on entirely unseen cycles.
- The joint `C` / `l1_ratio` search was *free to choose L2* (which keeps correlated
  features together). After correcting follicular anchors to include period days, it
  picked **elastic-net with weak regularization** (`C≈5.34`, `l1_ratio=0.2`) and kept
  all engineered features, so the separator is broader and less sparse than the earlier
  RHR-only version.
- **HRV is real but weaker and partly redundant — not insignificant.** Standalone
  leave-one-cycle-out AUCs: `resting_hr` 0.81, `avg_sleep_stress` 0.76, `avgStressLevel`
  0.71, `hrv_rmssd` 0.68, `avg_sleep_respiration` 0.68, `sleep_score` 0.64. HRV features
  remain correlated with the RHR/stress features, but the corrected-anchor model no longer
  prunes them out.
- Takeaway: `P(luteal)` remains useful for ranking follicular vs luteal anchor days, but
  the corrected anchors make Step 3 less clean than the earlier sparse RHR-only run.

### Step 2 — Score the full calendar

Apply the classifier to **every day**, including `Not logged`, to get a per-day
`P(luteal)` trajectory: low early in the cycle, high late.

### Step 3 — Locate the transition per cycle from `P(luteal)`

The follicular→luteal boundary is where `P(luteal)` rises from low to high. For each
cycle, find that crossing/change-point on the probability trajectory, but only inside
a physiologically plausible middle window. The detector now constrains the search to:

- the unlabeled `Not logged` middle,
- at least a few days after period end,
- at least a few days before the next period,
- and no longer than a tunable maximum luteal length.

This prevents impossible early picks, such as a transition that would imply a
~24-day luteal phase. If the model trajectory does not contain a usable low→high
step, use a clearly marked low-confidence constrained-midpoint fallback rather than
pretending the probability curve localized the transition.

#### Step 3 findings

Run on 26 complete cycles:

- Estimated a transition for **26 / 26** complete cycles.
- **24 / 26** estimates were model-supported change-points or crossings; **2 / 26**
  used the low-confidence constrained-midpoint fallback.
- Luteal length (`next period start − estimated transition`) clustered around
  **median 15 days** (mean 14.27, SD 3.58, range 8–20).
- **15 / 26** estimates landed inside the 10–16 day review band: both low-confidence
  fallbacks were inside the band, while the model-supported estimates split 13 ok /
  11 review. This is useful but still weak validation, so Step 3 should be treated as
  a model-based candidate transition rather than final truth.
- Garmin's fertile-window prediction remains only a weak comparator; the detected
  transitions generally fall near or just after that window, but it was not used as
  a label.

### Step 4 — Direct change-point on AUC-selected engineered features

Step 4 is a signal-based cross-check, not another classifier. It uses the already-built
engineered features from Step 1, but does not use the multifeature `P(luteal)` model.

The logic:

1. Compute standalone leave-one-cycle-out AUC for **every engineered feature**:
   baseline-normalized values, lags, rolling means, diffs, and slopes.
2. Rank features by `abs(AUC − 0.5)`, because features below 0.5 can still be useful
   after flipping their direction.
3. Select a small, interpretable set: the best feature from each biometric signal
   family, capped at a tunable maximum.
4. Orient each selected feature so higher means "more luteal".
5. Build an AUC-weighted composite, with weights proportional to `abs(AUC − 0.5)`.
6. Apply the same two-level low→high change-point function directly to that composite,
   inside the same constrained middle window as Step 3.

So Step 4 asks:

> If we ignore the classifier and look directly at the strongest engineered biometric
> trajectories, where is the best sustained low→high step?

This makes Step 4 an independent sanity check on Step 3. If the `P(luteal)` transition
and the engineered-feature transition agree, confidence increases. If they disagree,
the per-cycle plots show whether the probability model or the raw biometric trend is
more plausible.

#### Step 4 findings

Implemented in the notebook, but the full notebook has not yet been re-executed after
the latest Step 4 rewrite. Findings should be filled from the Step 4 output tables
after running:

- top standalone engineered features by leave-one-cycle-out AUC,
- selected Step 4 features and weights,
- feature-step transition dates,
- luteal-length validation,
- agreement with Step 3 `P(luteal)` transitions,
- comparison with Garmin's fertile-window end.

### Step 5 — Reconstruct full phases

With period (known) + transition (estimated), every phase follows — no Garmin
prediction needed:

- **Period** = known separately via `is_period`.
- **Follicular** = period start → estimated transition.
- **Luteal** = estimated transition → next period.

## 5. Validation without transition ground truth

We can't directly check ovulation timing, but we have a strong biological prior:
**luteal phase length is stable (~12–14 days), while follicular length varies.** So:

- Check that `next period start − estimated transition` **clusters tightly** around
  ~12–14 days across the 27 cycles. Tight clustering is real evidence the crossing
  lands on true ovulation.
- **Flag** cycles where it falls outside ~10–16 days as likely errors.
- Use `garmin_predicted_fertile` only as a **weak comparator** — agreement is
  reassuring, disagreement proves nothing either way.

## 6. Refinements that matter

- **Per-cycle baseline normalization** (subtract early-follicular mean) — usually
  makes or breaks transition detection, because the ~2–4 bpm luteal rise is small
  next to months-long baseline drift.
- **Consider mid-luteal, not late-luteal, as the "high" anchor** — just before
  menses, progesterone withdrawal starts dropping RHR again, weakening
  `Late luteal` as a clean "high" endpoint.
- **Group cross-validation by cycle** (leave-one-cycle-out) rather than a plain
  time-series split, so the estimate reflects "generalizes to an unseen cycle."

## 7. Honest caveats

- **~27 cycles / ~27 ovulation events** is a small sample for a noisy step. Realistic
  target: locate the transition **to within a few days**, not exactly.
- Labels are fixed windows around menses, so the endpoints themselves carry some
  slop; the method tolerates this but can't beat it entirely.

## 8. Status / roadmap

- [x] **Step 0** — cycle segmentation + alignment plots (signal check). *Step confirmed a visible luteal step.*
- [x] **Step 1** — binary follicular-vs-luteal classifier (per-cycle baseline normalization, elastic-net regularization, leave-one-cycle-out CV). *OOF AUC ≈ 0.85 after correcting follicular anchors to include period days.*
- [x] **Step 2** — score `P(luteal)` across the full calendar (honest leave-one-cycle-out per day, including `Not logged`).
- [x] **Step 3** — per-cycle transition location + validation against the luteal-length prior. *26 / 26 cycles estimated; 15 / 26 within the 10–16 day review band; 24 model-supported estimates.*
- [x] **Step 4** — direct two-level change-point on AUC-selected engineered features. *Implemented; findings pending notebook re-run.*
- [ ] **Step 5** — full-phase reconstruction and per-cycle transition plots.
