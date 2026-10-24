# 11: Features, baselines and evaluation, with Model Performance page

**What to build:** An honestly evaluated model with real measured numbers, shown on a Model Performance page next to the baselines it has to beat.

**Blocked by:** 10 (Labels and Modeling Cohort)

**Status:** ready-for-agent

**Mirror:** GitHub #12

- [ ] Features use only fields known at submission: Problem, agency, borough, channel, creation hour, weekday, month. Closure, status, resolution and duration fields are structurally blocked
- [ ] Chronological train, validation and test splits by creation time with a gap of horizon plus allowance; all Observations of a Request stay in one split
- [ ] Preprocessing is fit on training data only; unseen Problems are handled explicitly
- [ ] Constant-prevalence, historical-Problem and agency-only baselines; logistic regression; tree models only if they materially improve
- [ ] Threshold and calibration chosen on validation; test period used once
- [ ] Metrics: prevalence, precision/recall, PR-AUC, top-10% precision/recall/lift with defined ties, Brier score, slices with sample sizes
- [ ] Seam 2 tests: no post-cutoff fields in features; training and evaluation IDs never overlap
- [ ] Model Performance page shows the metrics, splits, model version and limitations
