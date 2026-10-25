-- Admission Scores (ticket 12): model promotion, frozen feature snapshots,
-- scores and the Risk Queue.

-- A registered logistic regression's calibrated Delayed probability for one
-- Request's features-v1 values (instruction.md §11), computed from the
-- weights ml.train recorded in ML.MODEL_EVALUATIONS.model. A level without a
-- weight (rare or unseen in training) takes the Other weight, as in training.
-- The one definition of scoring; promotion checks it reproduces training's scores.
CREATE OR REPLACE FUNCTION ML.DELAYED_PROBABILITY(
  model VARIANT, problem STRING, agency STRING, borough STRING, channel STRING,
  created_hour NUMBER, created_weekday NUMBER, created_month NUMBER
)
RETURNS FLOAT
AS
$$
  1 / (1 + EXP(-(
    model:calibration:slope::FLOAT * (
      model:logistic_regression:intercept::FLOAT
      + COALESCE(GET(model:logistic_regression:weights:problem, problem), model:logistic_regression:weights:problem:Other)::FLOAT
      + COALESCE(GET(model:logistic_regression:weights:agency, agency), model:logistic_regression:weights:agency:Other)::FLOAT
      + COALESCE(GET(model:logistic_regression:weights:borough, borough), model:logistic_regression:weights:borough:Other)::FLOAT
      + COALESCE(GET(model:logistic_regression:weights:channel, channel), model:logistic_regression:weights:channel:Other)::FLOAT
      + COALESCE(GET(model:logistic_regression:weights:created_hour, created_hour::STRING), model:logistic_regression:weights:created_hour:Other)::FLOAT
      + COALESCE(GET(model:logistic_regression:weights:created_weekday, created_weekday::STRING), model:logistic_regression:weights:created_weekday:Other)::FLOAT
      + COALESCE(GET(model:logistic_regression:weights:created_month, created_month::STRING), model:logistic_regression:weights:created_month:Other)::FLOAT
    )
    + model:calibration:intercept::FLOAT
  )))
$$;

-- Every promotion, in order (`python -m ml.promote <model_version>`, by hand).
-- The approved model is the latest one promoted; promoting an earlier version
-- rolls back. Registered versions are never removed.
CREATE TABLE IF NOT EXISTS ML.MODEL_PROMOTIONS (
  promotion_number  NUMBER AUTOINCREMENT ORDER,  -- increasing in promotion order
  model_version     STRING        NOT NULL,
  promoted_at       TIMESTAMP_TZ  NOT NULL,
  note              STRING
);

CREATE OR REPLACE VIEW ML.APPROVED_MODEL AS
SELECT e.model_version, e.feature_version, e.model, p.promoted_at
FROM ML.MODEL_PROMOTIONS AS p
JOIN ML.MODEL_EVALUATIONS AS e ON e.model_version = p.model_version
QUALIFY ROW_NUMBER() OVER (ORDER BY p.promotion_number DESC) = 1;

-- Frozen feature snapshots: a Modeling Cohort Request's features-v1 values,
-- taken by the first Publication that finds it eligible for an Admission
-- Score (First Observed within 72 h of creation, still below the Seven-Day
-- Horizon) and never changed. First Observed and the eligibility decision are
-- frozen with it. snapshot_observed_through (the latest processed extraction
-- then) is the prediction cutoff: what the Current Request knew. Never
-- pruned; rows of a held Publication are removed.
CREATE TABLE IF NOT EXISTS ML.FEATURE_SNAPSHOTS (
  unique_key                 STRING         NOT NULL,
  feature_version            STRING         NOT NULL,
  created_at_utc             TIMESTAMP_NTZ  NOT NULL,
  created_date_local         DATE           NOT NULL,
  horizon_at_utc             TIMESTAMP_NTZ  NOT NULL,
  first_observed_at          TIMESTAMP_TZ   NOT NULL,
  snapshot_observed_through  TIMESTAMP_NTZ  NOT NULL,
  source_payload_hash        STRING         NOT NULL,
  problem                    STRING         NOT NULL,
  agency                     STRING         NOT NULL,
  borough                    STRING         NOT NULL,
  channel                    STRING         NOT NULL,
  created_hour               NUMBER         NOT NULL,
  created_weekday            NUMBER         NOT NULL,
  created_month              NUMBER         NOT NULL,
  publication_id             STRING         NOT NULL,
  recorded_at                TIMESTAMP_TZ   NOT NULL
);

-- Admission Scores: at most one per Request, computed once from its frozen
-- snapshot by the model approved then, while it is still below the horizon.
-- Never recomputed or pruned; rows of a held Publication are removed.
CREATE TABLE IF NOT EXISTS ML.REQUEST_PREDICTIONS (
  unique_key         STRING         NOT NULL,
  model_version      STRING         NOT NULL,
  feature_version    STRING         NOT NULL,
  prediction_cutoff  TIMESTAMP_NTZ  NOT NULL,
  scored_at          TIMESTAMP_TZ   NOT NULL,
  admission_score    FLOAT          NOT NULL,
  publication_id     STRING         NOT NULL
);

-- The Risk Queue per Publication: eligible Modeling Cohort Requests still open
-- and below the horizon, with their Admission Score, or NULL when none was
-- computed (no approved model then). Fields are the frozen snapshot's.
CREATE TABLE IF NOT EXISTS ANALYTICS.PUB_RISK_QUEUE (
  publication_id   STRING  NOT NULL,
  unique_key       STRING  NOT NULL,
  problem          STRING  NOT NULL,
  agency           STRING  NOT NULL,
  borough          STRING  NOT NULL,
  created_date     DATE    NOT NULL,  -- New York time
  admission_score  FLOAT
);

CREATE OR REPLACE SECURE VIEW APP.PUBLISHED_RISK_QUEUE COPY GRANTS AS
SELECT * FROM ANALYTICS.PUB_RISK_QUEUE
WHERE publication_id IN (SELECT publication_id FROM OPS.PUBLICATIONS WHERE state = 'published');

-- Published Admission Scores with their provenance and frozen inputs, for the
-- Request lookup.
CREATE OR REPLACE SECURE VIEW APP.ADMISSION_SCORES COPY GRANTS AS
SELECT
  p.unique_key, p.admission_score, p.model_version, p.feature_version, p.prediction_cutoff, p.scored_at, p.publication_id,
  s.first_observed_at, s.created_at_utc, s.problem, s.agency, s.borough, s.channel,
  s.created_hour, s.created_weekday, s.created_month
FROM ML.REQUEST_PREDICTIONS AS p
JOIN ML.FEATURE_SNAPSHOTS AS s ON s.unique_key = p.unique_key
WHERE p.publication_id IN (SELECT publication_id FROM OPS.PUBLICATIONS WHERE state = 'published');

-- Selected fields of every Request Version, for the Request lookup: no
-- address, location or free text. The lookup shows the version a Publication
-- saw: the latest with valid_from_extracted_at at or before its data time.
CREATE OR REPLACE SECURE VIEW APP.REQUEST_LOOKUP COPY GRANTS AS
SELECT
  unique_key,
  valid_from_extracted_at,
  last_seen_extracted_at,
  first_extracted_at,
  last_seen_updated_at                      AS source_updated_at,
  observation_count,
  payload:created_date::STRING              AS created_date,
  payload:closed_date::STRING               AS closed_date,
  payload:status::STRING                    AS status,
  payload:agency::STRING                    AS agency,
  payload:complaint_type::STRING            AS problem,
  payload:descriptor::STRING                AS problem_detail,
  payload:borough::STRING                   AS borough,
  payload:open_data_channel_type::STRING    AS channel
FROM CLEAN.REQUEST_VERSIONS;

GRANT SELECT ON VIEW APP.PUBLISHED_RISK_QUEUE TO ROLE {{dashboard_role}};
GRANT SELECT ON VIEW APP.ADMISSION_SCORES TO ROLE {{dashboard_role}};
GRANT SELECT ON VIEW APP.REQUEST_LOOKUP TO ROLE {{dashboard_role}};
