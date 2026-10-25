-- Features and model evaluation (ticket 11).

-- Feature version features-v1: one row per Modeling Cohort Request, with only
-- the fields known when it was submitted. Closure, status, resolution and
-- duration fields are not here, so no model can read them; a Label is joined
-- on separately. Times are New York local. Read from the Current Request, so
-- a value the source corrected later shows its corrected form (retrospective
-- evaluation is approximate).
CREATE OR REPLACE VIEW ML.FEATURES AS
SELECT
  unique_key,
  created_at_utc,
  COALESCE(problem, 'Unspecified')            AS problem,
  COALESCE(agency, 'Unspecified')             AS agency,
  COALESCE(borough, 'Unspecified')            AS borough,
  COALESCE(open_data_channel_type, 'UNKNOWN') AS channel,
  HOUR(created_at_local)                      AS created_hour,
  DAYOFWEEKISO(created_at_local)              AS created_weekday,
  MONTH(created_at_local)                     AS created_month
FROM ML.MODELING_COHORT;

-- The examples one model version was trained and evaluated on: its split,
-- the Label known at its label cutoff, and the features. A snapshot, so the
-- evaluation can be reproduced after later data changes the views.
CREATE TABLE IF NOT EXISTS ML.MODEL_EXAMPLES (
  model_version           STRING         NOT NULL,
  split                   STRING         NOT NULL,
  unique_key              STRING         NOT NULL,
  created_at_utc          TIMESTAMP_NTZ  NOT NULL,
  label                   NUMBER         NOT NULL,
  duplicate_closure       BOOLEAN        NOT NULL,
  label_observed_through  TIMESTAMP_NTZ  NOT NULL,
  problem                 STRING         NOT NULL,
  agency                  STRING         NOT NULL,
  borough                 STRING         NOT NULL,
  channel                 STRING         NOT NULL,
  created_hour            NUMBER         NOT NULL,
  created_weekday         NUMBER         NOT NULL,
  created_month           NUMBER         NOT NULL
);

-- One row per evaluated model version (`python -m ml.train`, run by hand):
-- the label cutoff, the split plan with sample sizes and Duplicate Closure
-- shares, the validation comparison that chose the model, and the fitted
-- model (preprocessing, weights, calibration, threshold, dependencies).
-- Kept for every version; not tied to a Publication.
CREATE TABLE IF NOT EXISTS ML.MODEL_EVALUATIONS (
  model_version    STRING         NOT NULL,
  feature_version  STRING         NOT NULL,
  chosen_model     STRING         NOT NULL,
  label_cutoff     TIMESTAMP_NTZ  NOT NULL,
  evaluated_at     TIMESTAMP_TZ   NOT NULL,
  splits           VARIANT        NOT NULL,
  selection        VARIANT        NOT NULL,
  model            VARIANT        NOT NULL
);

-- Metrics per model version, split (validation, test), scorer (the three
-- baselines and the models) and slice: overall (slice_value NULL), or one
-- agency, Problem or borough. NULL where a metric has no defined value.
CREATE TABLE IF NOT EXISTS ML.MODEL_METRICS (
  model_version    STRING  NOT NULL,
  split            STRING  NOT NULL,
  scorer           STRING  NOT NULL,
  slice_kind       STRING  NOT NULL,
  slice_value      STRING,
  requests         NUMBER  NOT NULL,
  delayed          NUMBER  NOT NULL,
  prevalence       FLOAT,
  pr_auc           FLOAT,
  pr_auc_low       FLOAT,
  pr_auc_high      FLOAT,
  threshold        FLOAT,
  precision        FLOAT,
  recall           FLOAT,
  top10_precision  FLOAT,
  top10_recall     FLOAT,
  top10_lift       FLOAT,
  brier            FLOAT
);

CREATE OR REPLACE SECURE VIEW APP.MODEL_EVALUATIONS COPY GRANTS AS
SELECT * FROM ML.MODEL_EVALUATIONS;

CREATE OR REPLACE SECURE VIEW APP.MODEL_METRICS COPY GRANTS AS
SELECT * FROM ML.MODEL_METRICS;

GRANT SELECT ON VIEW APP.MODEL_EVALUATIONS TO ROLE {{dashboard_role}};
GRANT SELECT ON VIEW APP.MODEL_METRICS TO ROLE {{dashboard_role}};
