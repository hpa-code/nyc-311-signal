-- Ticket 07 review fixes.

-- One row. Processing and publishing both update it first thing in their
-- transactions (transformations/take_pipeline_lock.sql), so a Publication is
-- never built while Current Requests are being merged.
CREATE TABLE IF NOT EXISTS OPS.PIPELINE_LOCK (held_at TIMESTAMP_TZ);
INSERT INTO OPS.PIPELINE_LOCK (held_at)
SELECT NULL WHERE NOT EXISTS (SELECT 1 FROM OPS.PIPELINE_LOCK);

-- Last success and failure of each task-graph step that has no attempt table
-- of its own (publishing has OPS.PUBLICATIONS), so the dashboard can say the
-- latest run failed.
CREATE TABLE IF NOT EXISTS OPS.STEP_STATUS (
  step             STRING        NOT NULL PRIMARY KEY,
  last_success_at  TIMESTAMP_TZ,
  last_failure_at  TIMESTAMP_TZ,
  last_error       STRING
);
INSERT INTO OPS.STEP_STATUS (step)
SELECT 'readiness' WHERE NOT EXISTS (SELECT 1 FROM OPS.STEP_STATUS WHERE step = 'readiness');

-- Any kept, published Publication by ID. Readers that make several queries
-- (dashboard, Tableau export) read one ID from APP.PUBLICATION_STATUS, then
-- these, so their results never mix two Publications when the pointer moves.
CREATE OR REPLACE SECURE VIEW APP.PUBLISHED_OVERVIEW_DAILY COPY GRANTS AS
SELECT * FROM ANALYTICS.PUB_OVERVIEW_DAILY
WHERE publication_id IN (SELECT publication_id FROM OPS.PUBLICATIONS WHERE state = 'published');

CREATE OR REPLACE SECURE VIEW APP.PUBLISHED_DATA_COVERAGE COPY GRANTS AS
SELECT * FROM ANALYTICS.PUB_DATA_COVERAGE
WHERE publication_id IN (SELECT publication_id FROM OPS.PUBLICATIONS WHERE state = 'published');

CREATE OR REPLACE SECURE VIEW APP.PUBLISHED_TABLEAU_DAILY COPY GRANTS AS
SELECT * FROM ANALYTICS.PUB_TABLEAU_DAILY
WHERE publication_id IN (SELECT publication_id FROM OPS.PUBLICATIONS WHERE state = 'published');

-- As V011, plus a failed readiness check as a stale reason.
CREATE OR REPLACE SECURE VIEW APP.PUBLICATION_STATUS COPY GRANTS AS
WITH current_publication AS (
  SELECT p.publication_id, p.finished_at AS published_at, c.latest_extracted_at AS data_as_of
  FROM OPS.CURRENT_PUBLICATION AS cp
  JOIN OPS.PUBLICATIONS AS p ON p.publication_id = cp.publication_id
  LEFT JOIN ANALYTICS.PUB_DATA_COVERAGE AS c ON c.publication_id = cp.publication_id
),
latest_attempt AS (
  SELECT state, started_at FROM OPS.PUBLICATIONS
  QUALIFY ROW_NUMBER() OVER (ORDER BY started_at DESC, publication_id DESC) = 1
),
stuck AS (
  SELECT COUNT(*) AS stuck_runs FROM OPS.PIPELINE_RUNS
  WHERE state = 'waiting' AND first_seen_at < DATEADD(hour, -6, CURRENT_TIMESTAMP())
),
readiness AS (
  SELECT last_success_at, last_failure_at FROM OPS.STEP_STATUS WHERE step = 'readiness'
),
reasons AS (
  SELECT
    cur.publication_id, cur.published_at, cur.data_as_of,
    att.state      AS latest_attempt_state,
    att.started_at AS latest_attempt_at,
    ARRAY_CONSTRUCT_COMPACT(
      IFF(cur.publication_id IS NULL, 'no Publication yet', NULL),
      IFF(att.state IN ('held', 'failed'), 'the latest Publication attempt ' || IFF(att.state = 'held', 'failed its checks', 'failed'), NULL),
      IFF(rd.last_failure_at > COALESCE(rd.last_success_at, '1970-01-01'::TIMESTAMP_TZ), 'the latest readiness check failed', NULL),
      IFF(s.stuck_runs > 0, 'a collector run has waited more than 6 hours to load', NULL),
      IFF(cur.data_as_of < DATEADD(hour, -36, CURRENT_TIMESTAMP()), 'the data is more than 36 hours old', NULL)
    ) AS stale_reasons
  FROM stuck AS s
  LEFT JOIN readiness AS rd ON TRUE
  LEFT JOIN current_publication AS cur ON TRUE
  LEFT JOIN latest_attempt AS att ON TRUE
)
SELECT
  publication_id, published_at, data_as_of, latest_attempt_state, latest_attempt_at,
  ARRAY_SIZE(stale_reasons) > 0       AS stale,
  ARRAY_TO_STRING(stale_reasons, '; ') AS stale_reason
FROM reasons;

GRANT SELECT ON VIEW APP.PUBLISHED_OVERVIEW_DAILY TO ROLE {{dashboard_role}};
GRANT SELECT ON VIEW APP.PUBLISHED_DATA_COVERAGE TO ROLE {{dashboard_role}};
GRANT SELECT ON VIEW APP.PUBLISHED_TABLEAU_DAILY TO ROLE {{dashboard_role}};
