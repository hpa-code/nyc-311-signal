# Model card: Delayed Service Requests

The dashboard's Model Performance page shows the latest evaluation live, from `APP.MODEL_EVALUATIONS` and `APP.MODEL_METRICS`. This file records the first one, measured in dev.

## What it predicts

The probability that a Modeling Cohort Request will be Delayed, meaning not Closed within 168 hours of creation. It uses only fields known at submission. NYPD is excluded (ADR 0001). A Label records an administrative Closure. A Duplicate Closure counts as a Closure and is labeled by the same timing rule (ADR 0003).

## How it was evaluated

`uv run --env-file .env python -m ml.train` (`ml/train.py`), run by hand.

- **Features (`features-v1`, `ML.FEATURES`):** Problem, agency, borough, channel, and creation hour, weekday and month (New York time). All are categories. A level with fewer than 30 training examples, or one never seen in training, is scored as Other and counted. The view has no Closure time, `status`, `resolution_description` or duration fields.
- **Labels:** each Request's latest label version observed by the label cutoff, used only if that version has a Label. The examples are snapshotted per model version in `ML.MODEL_EXAMPLES`.
- **Splits:** by creation time, as half-open UTC windows. Each window starts at least 240 h (the horizon plus the 72 h allowance) after the previous one ends. Each Request is one example. Each split uses only Labels that had matured by the time it would have been used.
- **Baselines and candidates:**
  - Constant prevalence.
  - The training delay rate of the Request's Problem.
  - The training delay rate of its agency.
  - Two candidates: logistic regression (C = 1) and gradient boosting (scikit-learn `HistGradientBoostingClassifier`, 200 iterations).
  - Everything is fit on training data only.
- **Validation only:** each candidate gets Platt calibration. Each scorer gets the threshold with the highest F1. Gradient boosting is chosen only if its validation PR-AUC is at least 0.02 higher than logistic regression's.
- **Test, once:** the three baselines and the chosen model, overall and by agency, Problem and borough.
  - Top 10% means the ceil(10%) highest scores. Requests tied at the cutoff share the places left, so a constant score has lift 1.
  - The PR-AUC interval is a 95% bootstrap over Requests, 100 resamples.

## First evaluation (dev, `model-20261025T022936`)

Label cutoff 2026-10-24 18:58 UTC. scikit-learn 1.9.1, Python 3.13.

| Split | Created (UTC) | Requests | Delayed | Duplicate Closures among 0s / 1s |
|---|---|---:|---:|---:|
| Train | 2025-01-01 05:00 to 2026-06-04 | 2,926,799 | 32.7% | 11.8% / 0.6% |
| Validation | 2026-06-14 to 2026-08-09 | 322,663 | 42.0% | 6.3% / 1.1% |
| Test | 2026-08-19 to 2026-10-14 | 313,607 | 42.3% | 5.5% / 0.8% |

46 test Requests had a Problem never seen in training; 739 were scored as Other.

Duplicate Closures overall: 7.2% of labeled Modeling Cohort Requests in the current Publication (`APP.PUBLISHED_LABEL_SUMMARY`): 257,884 of the 0s (10.7%) and 8,755 of the 1s (0.7%).

**Validation (model choice).** PR-AUC: logistic regression 0.767, gradient boosting 0.762. Gradient boosting was 0.004 lower, short of the 0.02 bar, so logistic regression was chosen. The historical-Problem baseline scored 0.755 and agency-only 0.627.

**Test.**

| Scorer | PR-AUC (95% interval) | Precision / recall, top 10% | Lift, top 10% | Precision / recall at threshold | Brier |
|---|---|---|---:|---|---:|
| Constant prevalence | 0.423 (0.421–0.425) | 0.423 / 0.100 | 1.00 | 0.423 / 1.000 | 0.2533 |
| Agency-only rate | 0.616 (0.614–0.618) | 0.728 / 0.172 | 1.72 | 0.591 / 0.782 | 0.2149 |
| Historical Problem rate | 0.696 (0.694–0.699) | 0.695 / 0.164 | 1.64 | 0.623 / 0.872 | 0.1798 |
| Logistic regression | 0.711 (0.709–0.713) | 0.743 / 0.176 | 1.76 | 0.623 / 0.870 | 0.1777 |

What the numbers say:

- The model beats the agency-only baseline clearly: PR-AUC +0.095, Brier −0.037.
- Over the historical-Problem baseline it adds only a little: PR-AUC +0.015, top-10% lift 1.76 vs 1.64.
- Most of the signal is the Problem itself.

**Slices (test, logistic regression).**

- By borough, PR-AUC runs from 0.63 (Staten Island) to 0.75 (Queens).
- By agency it varies widely:
  - DOHMH 0.91, DPR 0.88, DOT 0.83, HPD 0.68.
  - DEP 0.16, below its prevalence of 0.20. Within DEP the model ranks worse than chance.
  - TLC and EDC are almost all Delayed, so their numbers mean little.
- The page flags slices with under 100 Requests.

## Approved model (dev, `model-20261025T035838`)

This version was retrained on the same examples and label cutoff, so that it records the reference scores promotion checks (ticket 12). It was promoted on 2026-10-25 and is the model that computes Admission Scores.

- **Test results:** PR-AUC 0.710 (0.708–0.713), top-10% precision / recall 0.739 / 0.175, lift 1.75, Brier 0.1777.
- **Validation:** gradient boosting was 0.002 lower again.
- **Why the numbers moved slightly:** the examples were read in no fixed order. Since then they are read in `unique_key` order, so retraining is reproducible.
- **Scoring:** `ML.DELAYED_PROBABILITY` reproduces its 20 reference scores to within 1e-9.
- **First Publication with scores:** 14,283 eligible Requests were scored, and 11,441 open ones were in the Risk Queue. Each Request is scored once, by the first Publication that sees it, and never again.

## Limitations

- **Retrospective, so approximate.**
  - Features come from the Current Request. The source may have corrected agency, Problem or borough after submission.
  - Label versions start with ticket 10's first Publication (2026-10-24). The splits could check only that a Label had matured, not that it had been observed at the time.
  - The trustworthy measure is a prospective evaluation on frozen first-observed inputs (ticket 12 onward).
- **Prevalence shifts.** Training covers both winters, and 32.7% of it is Delayed. The summer-to-autumn evaluation periods are 42% Delayed. Calibration on validation corrects for part of this.
- **Date-Only Closures** are judged by calendar day. One on the horizon day has no Label.
- **Associations, not causes.** A predicted delay is not urgency or deserved priority.
- **Weights for scoring.** Only logistic regression's weights are stored in `ML.MODEL_EVALUATIONS.model`. Gradient boosting would need a stored artifact before ticket 12 could score with it.
