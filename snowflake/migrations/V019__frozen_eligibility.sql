-- Ticket 12 review: the eligibility decision is frozen with the features.
-- ML.FEATURE_SNAPSHOTS now takes every Modeling Cohort Request once, at the
-- first Publication that sees it, with `eligible` recording whether it could
-- get an Admission Score (First Observed within 72 h of creation, still
-- below the Seven-Day Horizon). A replay that later moves First Observed
-- earlier cannot make it eligible. Rows so far were all eligible.
ALTER TABLE ML.FEATURE_SNAPSHOTS ADD COLUMN eligible BOOLEAN NOT NULL DEFAULT TRUE;
