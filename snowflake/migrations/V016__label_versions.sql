-- Ticket 10 review fixes:
-- - CLEAN.CLOSURE_FACTS holds the Closure and horizon rules that
--   ANALYTICS.REQUEST_FACTS and the Labels share, so the two cannot drift.
-- - ML.TRAINING_LABELS becomes a table of label versions: a row is added
--   whenever a Request's Label or reason changes, so a later correction never
--   rewrites what was knowable earlier. ML.CURRENT_LABELS (the V015 rules)
--   computes today's Labels; OPS.PUBLISH() records them.
-- - Reporting allowance 72 h (measured: about 1% of Requests reach the
--   source more than 72 h after creation; docs/source-inspection.md).
-- - A Request with a Quarantined Observation newer than its Current Request
--   has no Label: its latest state is unknown.
-- - Flags are never NULL; "not a duplicate" is not a Duplicate Closure.

-- One row per Current Request: when it was Closed and how that compares with
-- its Seven-Day Horizon. A Date-Only Closure (exactly midnight) is judged by
-- New York calendar day: one on the creation day is not before creation; one
-- on the horizon day has no known outcome. observed_through is the latest
-- processed extraction: what "now" means for the horizon.
CREATE OR REPLACE VIEW CLEAN.CLOSURE_FACTS AS
WITH observed AS (
  SELECT CONVERT_TIMEZONE('UTC', MAX(extracted_at))::TIMESTAMP_NTZ AS observed_through FROM CLEAN.SERVICE_REQUESTS
),
requests AS (
  SELECT
    r.*,
    COALESCE(r.status = 'Closed', FALSE)                                          AS is_closed,
    CONVERT_TIMEZONE('America/New_York', 'UTC', r.closed_at_local)                AS closed_at_utc,
    is_closed AND COALESCE(r.closed_at_local::TIME = '00:00:00', FALSE)           AS date_only_closure,
    DATEADD(hour, 168, r.created_at_utc)                                          AS horizon_at_utc,
    CONVERT_TIMEZONE('UTC', 'America/New_York', horizon_at_utc)::DATE             AS horizon_date,
    o.observed_through
  FROM CLEAN.SERVICE_REQUESTS AS r, observed AS o
)
SELECT
  *,
  -- Closed with a usable time: present and not before creation.
  is_closed AND COALESCE(
    IFF(date_only_closure, closed_at_local::DATE >= created_date_local, closed_at_utc >= created_at_utc), FALSE
  ) AS usable_closure,
  date_only_closure AND closed_at_local::DATE = horizon_date AS closure_on_horizon_day,
  -- Closed within the horizon; NULL when the outcome is not known from the Closure.
  IFF(usable_closure AND NOT closure_on_horizon_day,
      IFF(date_only_closure, closed_at_local::DATE < horizon_date, closed_at_utc <= horizon_at_utc), NULL) AS closed_by_horizon
FROM requests;

-- As V014, on CLEAN.CLOSURE_FACTS. as_of_utc is now the latest processed
-- extraction, the same "now" as the Labels (OPS.PUBLISH() waits for in-flight
-- runs, so a Publication sees no difference).
CREATE OR REPLACE VIEW ANALYTICS.REQUEST_FACTS AS
SELECT
  unique_key,
  COALESCE(problem, 'Unspecified')  AS problem,
  COALESCE(agency, 'Unspecified')   AS agency,
  COALESCE(borough, 'Unspecified')  AS borough,
  status,
  created_at_local,
  created_at_utc,
  created_date_local,
  closed_at_local,
  h3_cell,
  is_closed,
  closed_at_utc,
  date_only_closure,
  horizon_date,
  observed_through                  AS as_of_utc,
  horizon_at_utc < as_of_utc        AS past_horizon,
  -- Hours from creation to Closure; NULL when open or without a usable Closure.
  IFF(usable_closure, GREATEST(0, DATEDIFF(second, created_at_utc, closed_at_utc) / 3600), NULL) AS hours_to_close,
  -- Past the horizon with a knowable outcome.
  past_horizon AND (NOT is_closed OR closed_by_horizon IS NOT NULL) AS horizon_passed,
  horizon_passed AND closed_by_horizon AS closed_within_horizon,
  NOT is_closed AND past_horizon AS overdue
FROM CLEAN.CLOSURE_FACTS;

-- Today's Label for every Modeling Cohort Request, or the first reason it has
-- none. Rules as V015 (Duplicate Closures by timing, ADR 0003), with the
-- reason quarantined_observation added after reopened. source_* name the
-- Current Request's last Observation point: the Label's provenance.
CREATE OR REPLACE VIEW ML.CURRENT_LABELS AS
WITH policy AS (
  SELECT 72 AS reporting_allowance_hours
),
reopened AS (
  SELECT DISTINCT unique_key
  FROM (
    SELECT
      unique_key,
      closed,
      MAX(IFF(closed, 1, 0)) OVER (
        PARTITION BY unique_key ORDER BY valid_from_updated_at, valid_from_extracted_at, payload_hash
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS closed_before
    FROM (
      SELECT *, COALESCE(payload:status::STRING = 'Closed', FALSE) AS closed FROM CLEAN.REQUEST_VERSIONS
    )
  )
  WHERE closed_before = 1 AND NOT closed
),
-- Each Request's newest Quarantined Observation. One at least as new as the
-- Current Request means the Request may have changed since; an unreadable
-- extraction time counts as newest.
quarantined AS (
  SELECT
    unique_key,
    MAX(COALESCE(TRY_TO_TIMESTAMP_TZ(AS_VARCHAR(observation:extracted_at)), '9999-12-31'::TIMESTAMP_TZ)) AS latest_extracted_at
  FROM CLEAN.QUARANTINED_RECORDS
  WHERE unique_key IS NOT NULL
  GROUP BY 1
),
requests AS (
  SELECT
    f.*,
    DATEADD(hour, p.reporting_allowance_hours, f.horizon_at_utc) AS matured_at_utc,
    p.reporting_allowance_hours,
    v.unique_key IS NOT NULL AS version_found,
    f.is_closed
      AND COALESCE(v.payload:resolution_description::STRING ILIKE '%duplicate%', FALSE)
      AND NOT COALESCE(v.payload:resolution_description::STRING ILIKE '%not a duplicate%', FALSE) AS duplicate_closure,
    r.unique_key IS NOT NULL AS reopened,
    COALESCE(q.latest_extracted_at >= f.extracted_at, FALSE) AS quarantined_later
  FROM CLEAN.CLOSURE_FACTS AS f
  CROSS JOIN policy AS p
  -- The Current Request's own version (its last Observation point).
  LEFT JOIN CLEAN.REQUEST_VERSIONS AS v
    ON v.unique_key = f.unique_key AND v.payload_hash = f.payload_hash
   AND v.last_seen_updated_at = f.source_updated_at AND v.last_seen_extracted_at = f.extracted_at
  LEFT JOIN reopened AS r ON r.unique_key = f.unique_key
  LEFT JOIN quarantined AS q ON q.unique_key = f.unique_key
  WHERE f.unique_key IN (SELECT unique_key FROM ML.MODELING_COHORT)
),
reasons AS (
  SELECT
    *,
    CASE
      WHEN observed_through IS NULL OR observed_through < matured_at_utc THEN 'not_matured'
      WHEN reopened THEN 'reopened'
      WHEN quarantined_later THEN 'quarantined_observation'
      WHEN is_closed AND closed_at_local IS NOT NULL AND NOT usable_closure THEN 'closure_before_creation'
      WHEN is_closed AND closed_at_local IS NULL THEN 'closed_without_closure_time'
      WHEN NOT is_closed AND closed_at_local IS NOT NULL THEN 'closure_time_while_open'
      WHEN closure_on_horizon_day THEN 'date_only_closure_on_horizon_day'
    END AS no_label_reason
  FROM requests
)
SELECT
  unique_key,
  COALESCE(agency, 'Unspecified') AS agency,
  created_at_utc,
  horizon_at_utc,
  matured_at_utc,
  observed_through,
  reporting_allowance_hours,
  date_only_closure,
  duplicate_closure,
  version_found,
  IFF(no_label_reason IS NULL, IFF(is_closed, IFF(closed_by_horizon, 0, 1), 1), NULL) AS label,
  no_label_reason,
  payload_hash AS source_payload_hash,
  extracted_at AS source_extracted_at
FROM reasons;

-- Today's Labels, taken once per Publication (DML only inside OPS.PUBLISH()).
CREATE TABLE IF NOT EXISTS ML.WORK_CURRENT_LABELS (
  unique_key                 STRING,
  agency                     STRING,
  created_at_utc             TIMESTAMP_NTZ,
  horizon_at_utc             TIMESTAMP_NTZ,
  matured_at_utc             TIMESTAMP_NTZ,
  observed_through           TIMESTAMP_NTZ,
  reporting_allowance_hours  NUMBER,
  date_only_closure          BOOLEAN,
  duplicate_closure          BOOLEAN,
  version_found              BOOLEAN,
  label                      NUMBER,
  no_label_reason            STRING,
  source_payload_hash        STRING,
  source_extracted_at        TIMESTAMP_TZ
);

-- One label version per Request and change: what a Publication found when
-- the Label, reason or allowance changed. observed_through is when it became
-- knowable, so the Matured Labels known at time T are, per Request, the
-- latest version with observed_through <= T and a Label. Never pruned; rows
-- of a held Publication are removed.
DROP VIEW IF EXISTS ML.TRAINING_LABELS;
CREATE TABLE IF NOT EXISTS ML.TRAINING_LABELS (
  unique_key                 STRING         NOT NULL,
  label                      NUMBER,
  no_label_reason            STRING,
  created_at_utc             TIMESTAMP_NTZ  NOT NULL,
  horizon_at_utc             TIMESTAMP_NTZ  NOT NULL,
  matured_at_utc             TIMESTAMP_NTZ  NOT NULL,
  observed_through           TIMESTAMP_NTZ  NOT NULL,
  reporting_allowance_hours  NUMBER         NOT NULL,
  date_only_closure          BOOLEAN        NOT NULL,
  duplicate_closure          BOOLEAN        NOT NULL,
  source_payload_hash        STRING         NOT NULL,
  source_extracted_at        TIMESTAMP_TZ   NOT NULL,
  publication_id             STRING         NOT NULL,
  recorded_at                TIMESTAMP_TZ   NOT NULL
);

-- Date-Only Closures are counted by the reason date_only_closure_on_horizon_day.
ALTER TABLE ANALYTICS.PUB_LABEL_SUMMARY DROP COLUMN date_only_closures;

CREATE OR REPLACE SECURE VIEW APP.PUBLISHED_LABEL_SUMMARY COPY GRANTS AS
SELECT * FROM ANALYTICS.PUB_LABEL_SUMMARY
WHERE publication_id IN (SELECT publication_id FROM OPS.PUBLICATIONS WHERE state = 'published');
