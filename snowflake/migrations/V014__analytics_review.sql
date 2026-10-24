-- Ticket 09 review fixes. Also: OPS.PUBLISH() no longer writes all-Problems
-- rows to ANALYTICS.PUB_GEO_CELLS, so its problem is never NULL.

-- The H3 cell (resolution 8) of an Observation payload's location; NULL
-- outside a box around NYC. merge_current_requests.sql calls it.
CREATE OR REPLACE FUNCTION CLEAN.NYC_H3_CELL(payload VARIANT)
RETURNS STRING
AS
$$
  H3_LATLNG_TO_CELL_STRING(
    IFF(TRY_TO_DOUBLE(payload:latitude::STRING) BETWEEN 40.4 AND 41.0
        AND TRY_TO_DOUBLE(payload:longitude::STRING) BETWEEN -74.3 AND -73.6,
        TRY_TO_DOUBLE(payload:latitude::STRING), NULL),
    TRY_TO_DOUBLE(payload:longitude::STRING),
    8
  )
$$;

-- As V013, with:
-- - Date-Only Closures (recorded at exactly midnight) judged by New York
--   calendar day: one on the creation day counts as 0 hours, not as a
--   Closure before creation; one on the horizon day has no known outcome.
-- - Past the Seven-Day Horizon means strictly more than 168 hours old.
CREATE OR REPLACE VIEW ANALYTICS.REQUEST_FACTS AS
WITH as_of AS (
  SELECT CONVERT_TIMEZONE('UTC', MAX(extracted_at))::TIMESTAMP_NTZ AS as_of_utc FROM RAW.RAW_311_OBSERVATIONS
),
requests AS (
  SELECT
    r.unique_key,
    COALESCE(r.problem, 'Unspecified')              AS problem,
    COALESCE(r.agency, 'Unspecified')               AS agency,
    COALESCE(r.borough, 'Unspecified')              AS borough,
    r.status,
    r.created_at_local,
    r.created_at_utc,
    r.created_date_local,
    r.closed_at_local,
    r.h3_cell,
    COALESCE(r.status = 'Closed', FALSE)            AS is_closed,
    CONVERT_TIMEZONE('America/New_York', 'UTC', r.closed_at_local) AS closed_at_utc,
    is_closed AND r.closed_at_local::TIME = '00:00:00' AS date_only_closure,
    -- New York day the Seven-Day Horizon falls on.
    CONVERT_TIMEZONE('UTC', 'America/New_York', DATEADD(hour, 168, r.created_at_utc))::DATE AS horizon_date,
    a.as_of_utc
  FROM CLEAN.SERVICE_REQUESTS AS r, as_of AS a
),
closures AS (
  SELECT
    *,
    -- A Closure with a usable time: not missing, not before creation.
    is_closed AND (
      closed_at_utc >= created_at_utc
      OR (date_only_closure AND closed_at_local::DATE >= created_date_local)
    ) AS usable_closure,
    created_at_utc < DATEADD(hour, -168, as_of_utc) AS past_horizon
  FROM requests
)
SELECT
  * EXCLUDE (usable_closure),
  -- Hours from creation to Closure; NULL when open or without a usable Closure.
  IFF(usable_closure, GREATEST(0, DATEDIFF(second, created_at_utc, closed_at_utc) / 3600), NULL) AS hours_to_close,
  -- Past the horizon with a knowable outcome.
  past_horizon AND (
    NOT is_closed
    OR (usable_closure AND NOT (date_only_closure AND closed_at_local::DATE = horizon_date))
  ) AS horizon_passed,
  horizon_passed AND IFF(date_only_closure, closed_at_local::DATE < horizon_date, hours_to_close <= 168) AS closed_within_horizon,
  NOT is_closed AND past_horizon AS overdue
FROM closures;
