-- Analytics pages (ticket 09): daily stats, backlog, durations, geography and
-- the Overdue Queue. OPS.PUBLISH() builds each under a publication_id, like
-- the V011 outputs; the dashboard reads them by ID through APP.PUBLISHED_*.

-- The H3 cell (resolution 8, about 0.7 km²) of a Request's location, for
-- the geographic view. Raw coordinates never leave CLEAN; locations outside a
-- box around NYC get no cell. Same expression as merge_current_requests.sql.
ALTER TABLE CLEAN.SERVICE_REQUESTS ADD COLUMN h3_cell STRING;

UPDATE CLEAN.SERVICE_REQUESTS AS t
SET h3_cell = s.h3_cell
FROM (
  SELECT
    unique_key,
    payload_hash,
    ANY_VALUE(H3_LATLNG_TO_CELL_STRING(
      IFF(TRY_TO_DOUBLE(payload:latitude::STRING) BETWEEN 40.4 AND 41.0
          AND TRY_TO_DOUBLE(payload:longitude::STRING) BETWEEN -74.3 AND -73.6,
          TRY_TO_DOUBLE(payload:latitude::STRING), NULL),
      TRY_TO_DOUBLE(payload:longitude::STRING),
      8
    )) AS h3_cell
  FROM CLEAN.REQUEST_VERSIONS
  GROUP BY 1, 2
) AS s
WHERE t.unique_key = s.unique_key AND t.payload_hash = s.payload_hash;

-- One row per Current Request with the derived facts every analytics output
-- shares. Missing categories become 'Unspecified' (kept, never dropped), so a
-- NULL in a rollup always means "all". as_of_utc is the latest extraction:
-- what "now" means for the Seven-Day Horizon and the Overdue Queue.
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
    a.as_of_utc
  FROM CLEAN.SERVICE_REQUESTS AS r, as_of AS a
)
SELECT
  *,
  -- Hours from creation to Closure; NULL when open, or Closed without a usable
  -- Closure time (missing, or before creation).
  IFF(is_closed AND closed_at_utc >= created_at_utc, DATEDIFF(second, created_at_utc, closed_at_utc) / 3600, NULL) AS hours_to_close,
  -- Past its Seven-Day Horizon at as_of, with a knowable outcome.
  created_at_utc <= DATEADD(hour, -168, as_of_utc) AND (NOT is_closed OR hours_to_close IS NOT NULL) AS horizon_passed,
  horizon_passed AND hours_to_close <= 168        AS closed_within_horizon,
  NOT is_closed AND created_at_utc <= DATEADD(hour, -168, as_of_utc) AS overdue
FROM requests;

-- Submissions (by creation date) and Closures (by Closure date) per day.
CREATE TABLE IF NOT EXISTS ANALYTICS.PUB_DAILY_REQUEST_STATS (
  publication_id  STRING  NOT NULL,
  stat_date       DATE    NOT NULL,
  problem         STRING  NOT NULL,
  agency          STRING  NOT NULL,
  borough         STRING  NOT NULL,
  submitted       NUMBER  NOT NULL,
  closed          NUMBER  NOT NULL
);

-- Saved daily backlog snapshots: open Requests per group as observed on
-- snapshot_date (the New York day of the source's latest update). History
-- that cannot be rebuilt later, so OPS.PUBLISH() keeps one snapshot per day,
-- from a Publication that passed its checks, and never prunes them.
CREATE TABLE IF NOT EXISTS ANALYTICS.DAILY_BACKLOG (
  snapshot_date   DATE          NOT NULL,
  publication_id  STRING        NOT NULL,
  problem         STRING        NOT NULL,
  agency          STRING        NOT NULL,
  borough         STRING        NOT NULL,
  open_requests   NUMBER        NOT NULL,
  taken_at        TIMESTAMP_TZ  NOT NULL
);

-- Open Requests at the end of each day, per group. kind = 'observed' (from
-- DAILY_BACKLOG) or 'retrospective' (reconstructed from today's Current
-- Requests, which knows Closures that were not known on the day).
CREATE TABLE IF NOT EXISTS ANALYTICS.PUB_BACKLOG_DAILY (
  publication_id  STRING  NOT NULL,
  backlog_date    DATE    NOT NULL,
  kind            STRING  NOT NULL,
  problem         STRING  NOT NULL,
  agency          STRING  NOT NULL,
  borough         STRING  NOT NULL,
  open_requests   NUMBER  NOT NULL
);

-- Durations with their denominators, for every combination of creation month,
-- Problem, agency and borough; NULL in any of them means all.
CREATE TABLE IF NOT EXISTS ANALYTICS.PUB_DURATION_STATS (
  publication_id         STRING  NOT NULL,
  created_month          DATE,
  problem                STRING,
  agency                 STRING,
  borough                STRING,
  requests               NUMBER  NOT NULL,
  closed_requests        NUMBER  NOT NULL,
  open_requests          NUMBER  NOT NULL,  -- unresolved
  timed_closures         NUMBER  NOT NULL,  -- denominator of the median and p90
  median_hours           FLOAT,
  p90_hours              FLOAT,
  horizon_passed         NUMBER  NOT NULL,  -- denominator of the closure rate
  closed_within_horizon  NUMBER  NOT NULL
);

-- Requests per H3 cell and Problem (NULL problem = all Problems). Cells with
-- fewer than 20 Requests are folded into one 'hidden' row per Problem, and
-- Requests without a location into one 'no_location' row; neither has a cell.
CREATE TABLE IF NOT EXISTS ANALYTICS.PUB_GEO_CELLS (
  publication_id         STRING  NOT NULL,
  problem                STRING,
  cell_kind              STRING  NOT NULL,  -- cell | hidden | no_location
  h3_cell                STRING,
  requests               NUMBER  NOT NULL,
  open_requests          NUMBER  NOT NULL,
  horizon_passed         NUMBER  NOT NULL,
  closed_within_horizon  NUMBER  NOT NULL,
  median_hours           FLOAT
);

-- Overdue Requests (open past the Seven-Day Horizon), every agency including
-- NYPD. Same public fields as the Risk Queue, plus status and hours open.
CREATE TABLE IF NOT EXISTS ANALYTICS.PUB_OVERDUE_QUEUE (
  publication_id    STRING         NOT NULL,
  unique_key        STRING         NOT NULL,
  problem           STRING         NOT NULL,
  agency            STRING         NOT NULL,
  borough           STRING         NOT NULL,
  status            STRING,
  created_at_local  TIMESTAMP_NTZ  NOT NULL,  -- New York time
  hours_open        NUMBER         NOT NULL
);

CREATE OR REPLACE SECURE VIEW APP.PUBLISHED_DAILY_REQUEST_STATS COPY GRANTS AS
SELECT * FROM ANALYTICS.PUB_DAILY_REQUEST_STATS
WHERE publication_id IN (SELECT publication_id FROM OPS.PUBLICATIONS WHERE state = 'published');

CREATE OR REPLACE SECURE VIEW APP.PUBLISHED_BACKLOG_DAILY COPY GRANTS AS
SELECT * FROM ANALYTICS.PUB_BACKLOG_DAILY
WHERE publication_id IN (SELECT publication_id FROM OPS.PUBLICATIONS WHERE state = 'published');

CREATE OR REPLACE SECURE VIEW APP.PUBLISHED_DURATION_STATS COPY GRANTS AS
SELECT * FROM ANALYTICS.PUB_DURATION_STATS
WHERE publication_id IN (SELECT publication_id FROM OPS.PUBLICATIONS WHERE state = 'published');

CREATE OR REPLACE SECURE VIEW APP.PUBLISHED_GEO_CELLS COPY GRANTS AS
SELECT * FROM ANALYTICS.PUB_GEO_CELLS
WHERE publication_id IN (SELECT publication_id FROM OPS.PUBLICATIONS WHERE state = 'published');

CREATE OR REPLACE SECURE VIEW APP.PUBLISHED_OVERDUE_QUEUE COPY GRANTS AS
SELECT * FROM ANALYTICS.PUB_OVERDUE_QUEUE
WHERE publication_id IN (SELECT publication_id FROM OPS.PUBLICATIONS WHERE state = 'published');

GRANT SELECT ON VIEW APP.PUBLISHED_DAILY_REQUEST_STATS TO ROLE {{dashboard_role}};
GRANT SELECT ON VIEW APP.PUBLISHED_BACKLOG_DAILY TO ROLE {{dashboard_role}};
GRANT SELECT ON VIEW APP.PUBLISHED_DURATION_STATS TO ROLE {{dashboard_role}};
GRANT SELECT ON VIEW APP.PUBLISHED_GEO_CELLS TO ROLE {{dashboard_role}};
GRANT SELECT ON VIEW APP.PUBLISHED_OVERDUE_QUEUE TO ROLE {{dashboard_role}};
