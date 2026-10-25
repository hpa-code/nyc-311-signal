# 11: Features, baselines and evaluation, with Model Performance page

**What to build:** An honestly evaluated model with real measured numbers, shown on a Model Performance page next to the baselines it has to beat.

**Blocked by:** 10 (Labels and Modeling Cohort)

**Status:** done

**Mirror:** GitHub #12

- [x] Features use only fields known at submission: Problem, agency, borough, channel, creation hour, weekday, month. Closure, status, resolution and duration fields are structurally blocked
- [x] Chronological train, validation and test splits by creation time with a gap of horizon plus allowance; all Observations of a Request stay in one split
- [x] Preprocessing is fit on training data only; unseen Problems are handled explicitly
- [x] Constant-prevalence, historical-Problem and agency-only baselines; logistic regression; tree models only if they materially improve
- [x] Threshold and calibration chosen on validation; test period used once
- [x] Metrics: prevalence, precision/recall, PR-AUC, top-10% precision/recall/lift with defined ties, Brier score, slices with sample sizes
- [x] Seam 2 tests: no post-cutoff fields in features; training and evaluation IDs never overlap
- [x] Model Performance page shows the metrics, splits, model version and limitations

## Comments

**2026-10-24 (agent, from 10):**
- **Duplicate share.** Report it in the model card both overall and within each Label, from `APP.PUBLISHED_LABEL_SUMMARY.duplicate_closures` (ADR 0003). In dev, 7.2% of labeled Requests are Duplicate Closures: 258,078 of the 0s and 8,757 of the 1s.
- **Matured labels.** `ML.TRAINING_LABELS` holds label versions (see ticket 10's review fixes). The Labels known at a cutoff T are, per Request, the latest version with `observed_through <= T` that has a Label. Record T with the model.

**2026-10-25 (agent): built.**
- **Features (`features-v1`).** `V017__features_and_model_evaluation.sql` adds `ML.FEATURES`: one row per Modeling Cohort Request, with Problem, agency, borough, channel, and creation hour, weekday and month (New York time).
  - It has no Closure time, `status`, `resolution_description` or duration fields, so no model can read them. A test asserts its exact columns.
  - Values come from the Current Request, so retrospective evaluation is approximate. Frozen first-observed snapshots are ticket 12.
- **Examples (`ml/examples.py`).** `snapshot_examples` writes one row per Request per model version to `ML.MODEL_EXAMPLES`: split, Label and features, as the snapshot ticket 10 asked for.
  - A Label is the Request's latest label version with `observed_through <=` the label cutoff, used only if that version has one. This is stricter than "the latest version that has a Label": a Request reopened by the cutoff is left out, as instruction §10 requires.
  - Splits are half-open UTC creation windows. `SplitPlan` refuses windows less than 240 h apart.
  - Each split takes only Labels matured by the time it would have been used: validation start, test start, cutoff.
  - The snapshot refuses label versions whose allowance is longer than the gap assumes.
- **Training (`ml/train.py`, run by hand: `uv run --env-file .env python -m ml.train`).**
  - The default plan uses 8-week validation and test windows ending at the cutoff minus 240 h. Training covers everything from 2025-01-01 up to 240 h before validation.
  - Fit on training data only:
    - Category levels: those with at least 30 examples, at most 250 per feature. A Problem that is rare, or unseen in training, is scored as Other and counted.
    - Three baselines: constant prevalence, the Problem's delay rate, the agency's delay rate.
    - Logistic regression and gradient boosting.
  - On validation: Platt calibration and the F1-best threshold for each scorer. Gradient boosting is chosen only if its PR-AUC is at least 0.02 higher.
  - The test period is scored once: the baselines and the chosen model, overall with a 95% bootstrap PR-AUC interval, and by agency, Problem and borough.
  - Results go to `ML.MODEL_EVALUATIONS` (splits, selection, levels, weights, calibration, threshold, dependencies) and `ML.MODEL_METRICS`, read through `APP.MODEL_EVALUATIONS` and `APP.MODEL_METRICS`.
  - These are not per-Publication outputs: an evaluation is a manual step and outlives Publications.
- **Metrics (`ml/metrics.py`).** The top 10% is ceil(10%) of Requests. Requests tied at the cutoff share the remaining places (the expected value under a random tie-break), so a constant score has lift 1. A metric with no defined value is NULL.
- **Model Performance page** (`dashboard/pages/model.py`):
  - test comparison and a lift chart;
  - splits, with dates, counts, the Duplicate Closure share within each Label, and unseen Problems;
  - the validation choice;
  - slices beside the agency-only baseline, flagged under 100 Requests;
  - Label counts and reasons from the current Publication;
  - limitations.
- **Dev:** V017 applied. `model-20261025T022936` was evaluated in 81 s. Write-up in `docs/model-card.md`.
  - Splits: train 2,926,799 (32.7% Delayed), validation 322,663 (42.0%), test 313,607 (42.3%, created 2026-08-19 to 2026-10-14).
  - Test PR-AUC: logistic regression 0.711 (0.709–0.713), historical Problem 0.696, agency-only 0.616, constant 0.423. Lift in the top 10%: 1.76, 1.64, 1.72, 1.00.
  - Validation PR-AUC: logistic regression 0.767, gradient boosting 0.762, so gradient boosting was not chosen.
  - The model clearly beats the agency-only baseline but adds only a little over the Problem's rate.
  - Within DEP, PR-AUC is 0.16 against a prevalence of 0.20, worse than chance.
- **Tests:**
  - `ml/tests/test_metrics.py` (offline): ties, rounding, Brier, undefined metrics, F1 threshold.
  - `warehouse/tests/test_modeling.py` (Seam 2):
    - split boundaries to the second, the gap, label versions at two cutoffs (a reopening drops the Request), and a Request with two Request Versions giving one example;
    - no ID in two splits, and exactly the allowed feature columns;
    - an end-to-end evaluation that records every baseline and only the chosen model on test.
  - The dashboard-role test covers the new views.
- **Review fixes (two-axis review):**
  - Vocabulary: "target" and "sample" removed from the page and card.
  - A direct overlap assertion.
  - One `labels_known_at` source.
  - The allowance is checked against the label versions.
  - The overall duplicate share was added to the card.
  - `ML.MODEL_METRICS` added to the role test.
- **Left as is:**
  - V017's comment says "resolution". It is applied to dev, and applied migrations are never edited.
  - Before 2026-10-24 there are no label versions, so the earlier splits could check only that a Label had matured, not that it was observed then. This is disclosed on the page and in the card.
  - Re-running `ml.train` re-scores the same default test window. The test-once rule is a working practice; it isn't enforced.
  - The test's `FEATURE_COLUMNS` deliberately restates the allowed fields instead of importing them.
- **For ticket 12:**
  - Only logistic regression stores scoring weights (`model.logistic_regression`). If gradient boosting is ever chosen, it needs a stored artifact.
  - Registration, promotion and frozen feature snapshots remain open.
