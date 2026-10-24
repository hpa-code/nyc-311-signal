-- Labels and the Modeling Cohort (ticket 10). The Modeling Cohort excludes
-- NYPD (ADR 0001); analytics and the Overdue Queue keep it.

-- Current Requests eligible for training, evaluation and Admission Scores.
-- Membership follows the Current Request's agency.
CREATE OR REPLACE VIEW ML.MODELING_COHORT AS
SELECT *
FROM CLEAN.SERVICE_REQUESTS
WHERE agency IS DISTINCT FROM 'NYPD';

-- One row per Modeling Cohort Request: its Label (0 = Closed within the
-- Seven-Day Horizon, 1 = Delayed), or the reason it has none.
--
-- A Label is assigned only once the latest processed extraction
-- (observed_through) is at least the horizon plus the reporting allowance
-- after creation. The change cursor re-fetches every changed Request, so the
-- Current Request is the source's state as of that extraction; the allowance
-- covers the source's publication lag. Calendar age alone is not enough: an
-- old Request seen only by an old extraction stays not_matured.
--
-- Rules, on the Current Request:
-- - Closed at or before 168 h: 0; after: 1. Open (no Closure time): 1.
-- - A Date-Only Closure (exactly midnight) is judged by New York calendar day
--   against the day the horizon falls on: before 0, after 1, that day no Label.
-- - A Duplicate Closure (Closed as a duplicate of another Request) is an
--   ordinary Closure, labeled by the same rules.
-- No Label, first matching reason:
--   not_matured, reopened (any Request Version Closed and a later one not),
--   closure_before_creation, closed_without_closure_time,
--   closure_time_while_open, date_only_closure_on_horizon_day.
-- Reopenings before the system first observed a Request are invisible: the
-- backfill saw only each Request's state at the time.
CREATE OR REPLACE VIEW ML.TRAINING_LABELS AS
WITH policy AS (
  SELECT 168 AS horizon_hours, 48 AS reporting_allowance_hours
),
observed AS (
  SELECT CONVERT_TIMEZONE('UTC', MAX(extracted_at))::TIMESTAMP_NTZ AS observed_through FROM CLEAN.SERVICE_REQUESTS
),
reopened AS (
  SELECT DISTINCT unique_key
  FROM (
    SELECT
      unique_key,
      COALESCE(payload:status::STRING = 'Closed', FALSE) AS closed,
      MAX(IFF(COALESCE(payload:status::STRING = 'Closed', FALSE), 1, 0)) OVER (
        PARTITION BY unique_key ORDER BY valid_from_updated_at, valid_from_extracted_at, payload_hash
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS closed_before
    FROM CLEAN.REQUEST_VERSIONS
  )
  WHERE closed_before = 1 AND NOT closed
),
requests AS (
  SELECT
    c.unique_key,
    COALESCE(c.agency, 'Unspecified')                                                 AS agency,
    c.created_at_utc,
    c.created_date_local,
    DATEADD(hour, p.horizon_hours, c.created_at_utc)                                AS horizon_at_utc,
    CONVERT_TIMEZONE('UTC', 'America/New_York', horizon_at_utc)::DATE                 AS horizon_date,
    DATEADD(hour, p.reporting_allowance_hours, horizon_at_utc)                        AS matured_at_utc,
    o.observed_through,
    p.reporting_allowance_hours,
    COALESCE(c.status = 'Closed', FALSE)                                              AS is_closed,
    c.closed_at_local,
    CONVERT_TIMEZONE('America/New_York', 'UTC', c.closed_at_local)                    AS closed_at_utc,
    is_closed AND c.closed_at_local::TIME = '00:00:00'                                AS date_only_closure,
    is_closed AND COALESCE(v.payload:resolution_description::STRING ILIKE '%duplicate%', FALSE) AS duplicate_closure,
    r.unique_key IS NOT NULL                                                          AS reopened
  FROM ML.MODELING_COHORT AS c
  CROSS JOIN policy AS p
  CROSS JOIN observed AS o
  -- The Current Request's own version (its last Observation point), for the resolution text.
  LEFT JOIN CLEAN.REQUEST_VERSIONS AS v
    ON v.unique_key = c.unique_key AND v.payload_hash = c.payload_hash
   AND v.last_seen_updated_at = c.source_updated_at AND v.last_seen_extracted_at = c.extracted_at
  LEFT JOIN reopened AS r ON r.unique_key = c.unique_key
),
reasons AS (
  SELECT
    *,
    CASE
      WHEN observed_through IS NULL OR observed_through < matured_at_utc THEN 'not_matured'
      WHEN reopened THEN 'reopened'
      -- A Date-Only Closure on the creation day is not before creation.
      WHEN is_closed AND IFF(date_only_closure, closed_at_local::DATE < created_date_local, closed_at_utc < created_at_utc)
        THEN 'closure_before_creation'
      WHEN is_closed AND closed_at_local IS NULL THEN 'closed_without_closure_time'
      WHEN NOT is_closed AND closed_at_local IS NOT NULL THEN 'closure_time_while_open'
      WHEN date_only_closure AND closed_at_local::DATE = horizon_date THEN 'date_only_closure_on_horizon_day'
    END AS no_label_reason
  FROM requests
)
SELECT
  unique_key,
  agency,
  created_at_utc,
  horizon_at_utc,
  matured_at_utc,
  observed_through,
  reporting_allowance_hours,
  date_only_closure,
  duplicate_closure,
  CASE
    WHEN no_label_reason IS NOT NULL THEN NULL
    WHEN NOT is_closed THEN 1
    WHEN date_only_closure THEN IFF(closed_at_local::DATE < horizon_date, 0, 1)
    ELSE IFF(closed_at_utc <= horizon_at_utc, 0, 1)
  END AS label,
  no_label_reason
FROM reasons;

-- Every Current Request counted once per Publication: by agency, Label (NULL
-- = none) and the reason for none. NYPD Requests carry the reason
-- outside_modeling_cohort. duplicate_closures is the Duplicate Closures among
-- them, for the share reported beside the model.
CREATE TABLE IF NOT EXISTS ANALYTICS.PUB_LABEL_SUMMARY (
  publication_id      STRING  NOT NULL,
  agency              STRING  NOT NULL,
  label               NUMBER,
  no_label_reason     STRING,
  requests            NUMBER  NOT NULL,
  duplicate_closures  NUMBER  NOT NULL,
  date_only_closures  NUMBER  NOT NULL
);

CREATE OR REPLACE SECURE VIEW APP.PUBLISHED_LABEL_SUMMARY COPY GRANTS AS
SELECT * FROM ANALYTICS.PUB_LABEL_SUMMARY
WHERE publication_id IN (SELECT publication_id FROM OPS.PUBLICATIONS WHERE state = 'published');

GRANT SELECT ON VIEW APP.PUBLISHED_LABEL_SUMMARY TO ROLE {{dashboard_role}};
