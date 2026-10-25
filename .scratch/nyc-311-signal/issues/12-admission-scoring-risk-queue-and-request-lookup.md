# 12: Admission scoring, Risk Queue and Request lookup

**What to build:** Each new Modeling Cohort Request is scored once and appears in a Risk Queue an analyst can review, with full provenance in a lookup.

**Blocked by:** 07 (Publication gate), 11 (Features, baselines and evaluation, with Model Performance page)

**Status:** done

**Mirror:** GitHub #13

- [x] The model is registered with preprocessing, feature schema, dependencies and metrics; promotion is manual and the previous version stays available for rollback
- [x] The daily task graph scores eligible Requests: Modeling Cohort, First Observed within 72h of creation, still below the horizon; frozen feature snapshots
- [x] Each score is stored with request, model and feature versions, cutoff, scoring time and publication ID; retries are idempotent; scores are never recomputed
- [x] Risk Queue shows only Request ID, Problem, agency, borough, created date and Admission Score, with a note that predicted delay is not urgency
- [x] Request lookup shows selected fields, observation freshness and score provenance
- [x] Scores are a `PUB_*` output of each Publication (`R__publication.sql`), with a check; a Publication never fills in missing scores (from ticket 07)
- [x] Seam 2 test: scoring idempotency

## Comments

**2026-10-24 (agent, from 05):** `CLEAN.SERVICE_REQUESTS.first_observed_at` is the minimum extraction time over all Observations. A true replay that carries an earlier original extraction can move it earlier. Admission scoring must snapshot First Observed (and the 72-hour eligibility decision) when it scores, and never re-derive it later. The Request lookup can show versions and provenance from `CLEAN.REQUEST_VERSIONS`, whose valid_from_* and last_seen_* columns include run and file.

**2026-10-25 (agent): built.**
- **Registration.** A version is registered by `ml.train` in `ML.MODEL_EVALUATIONS`. It records:
  - preprocessing: category levels and Other;
  - the feature schema: `features-v1` and its fields;
  - the logistic regression weights, calibration and threshold;
  - dependency versions and metrics;
  - 20 reference scores computed in Python.
- **Promotion** (`uv run --env-file .env python -m ml.promote <model_version> [note]`, by hand) appends to `ML.MODEL_PROMOTIONS`. The approved model is the latest promoted (`ML.APPROVED_MODEL`), so promoting an earlier version rolls back. Versions are never removed. Promotion refuses:
  - a version that is not registered;
  - another feature version;
  - one with no SQL-scorable weights (so gradient boosting can't be promoted yet);
  - one whose reference scores `ML.DELAYED_PROBABILITY` misses by more than 1e-9.
- **Scoring.** `ML.DELAYED_PROBABILITY` is a SQL function computing the calibrated logistic regression from the registered weights; a level without a weight takes the Other weight. It runs inside `OPS.PUBLISH()`, the daily task graph's last step:
  - `ML.FEATURE_SNAPSHOTS` freezes every Modeling Cohort Request once, at the first Publication that sees it. Frozen with it: features-v1 values, First Observed, the horizon, the prediction cutoff (the latest processed extraction), the source payload hash, and the **eligibility decision** (First Observed within 72 h of creation and still below the horizon).
  - `ML.REQUEST_PREDICTIONS`: in that same Publication, each eligible Request is scored once by the approved model. Each score records model and feature versions, cutoff, scoring time and Publication.
  - No later Publication fills in a missing score, and no score is recomputed.
  - Rows from a held attempt are removed, so its retry scores once.
  - A promotion counts as an input change, so the next call publishes.
- **Risk Queue** (`ANALYTICS.PUB_RISK_QUEUE`, `APP.PUBLISHED_RISK_QUEUE`): eligible Requests still open and below the horizon.
  - Columns: Request ID, Problem, agency, borough, created date and Admission Score (NULL, shown as "No score", when none was computed).
  - New checks:
    - one snapshot and one score per Request;
    - scores in [0, 1];
    - queue ≤ open Requests;
    - no NYPD;
    - every stored score is shown.
  - The page (`dashboard/pages/risk.py`) carries the "not urgency, danger or deserved priority" warning.
- **Request lookup** (`dashboard/pages/lookup.py`):
  - `APP.REQUEST_LOOKUP`: selected fields of each Request Version (no address, location or free text). The page shows the latest version with `valid_from_extracted_at` ≤ the current Publication's data time, plus freshness.
  - `APP.ADMISSION_SCORES`: published scores with provenance and frozen inputs.
- **Dev:**
  - V018 and V019 applied.
  - `model-20261025T035838` retrained on the same examples (now with reference scores) and promoted.
  - `20261025T040009-b919258b`: 14,283 eligible Requests scored; Risk Queue 11,441, all scored; every check passing.
  - `20261025T042134-b845284f` froze all 3,774,960 cohort Requests once (14,283 eligible) in 118 s.
- **Tests** (`warehouse/tests/test_scoring.py`, Seam 2):
  - Promotion refusals and rollback.
  - Idempotency across five passes:
    - unscored without a model, never filled in later;
    - a replayed earlier extraction that doesn't make an ineligible Request eligible;
    - a new model scoring only new Requests;
    - a held attempt whose retry scores once;
    - Requests leaving the queue at the horizon.
  - `test_modeling.py` now promotes the model it trains, so the SQL scorer reproduces a real trained model.
- **Review fixes:**
  - A duplicate Current Request can't multiply snapshots (`QUALIFY`).
  - The eligibility decision is frozen.
  - Scoring happens only in the freezing Publication.
  - Promotion checks the feature version.
  - The lookup shows the frozen First Observed.
  - `ml.examples.load_examples` orders by `unique_key`, so retraining is reproducible.
- **Left as is:**
  - The 72 h eligibility window is its own policy, not the 72 h reporting allowance. It is stated once, in `OPS.PUBLISH()`.
  - Scores are shown through the published-filtered `APP.ADMISSION_SCORES` rather than a `PUB_*` copy. They are append-only and keyed per Publication, so a copy would add nothing.
  - The lookup reads Request Versions live, limited by the Publication's data time.
  - `risk.py` is built like `overdue.py`.
- **Limitations:**
  - Features are the Current Request at the first Publication that sees it. If Publications are held for days, that may be later than the first Observation.
  - The 14,283 Requests eligible before the first promotion were scored by the first scoring Publication, under the earlier rule (which was tightened in review).
