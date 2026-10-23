# 12: Admission scoring, Risk Queue and Request lookup

**What to build:** Each new Modeling Cohort Request is scored once and appears in a Risk Queue an analyst can review, with full provenance in a lookup.

**Blocked by:** 07 (Publication gate), 11 (Features, baselines and evaluation, with Model Performance page)

**Status:** ready-for-agent

**Mirror:** GitHub #13

- [ ] The model is registered with preprocessing, feature schema, dependencies and metrics; promotion is manual and the previous version stays available for rollback
- [ ] The daily task graph scores eligible Requests: Modeling Cohort, First Observed within 72h of creation, still below the horizon; frozen feature snapshots
- [ ] Each score is stored with request, model and feature versions, cutoff, scoring time and publication ID; retries are idempotent; scores are never recomputed
- [ ] Risk Queue shows only Request ID, Problem, agency, borough, created date and Admission Score, with a note that predicted delay is not urgency
- [ ] Request lookup shows selected fields, observation freshness and score provenance
- [ ] Seam 2 test: scoring idempotency
