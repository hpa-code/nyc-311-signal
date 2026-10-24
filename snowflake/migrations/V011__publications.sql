-- Publications (ticket 07). Every dashboard output is copied into an
-- ANALYTICS.PUB_* table under a publication_id, and APP views show only the
-- current Publication. OPS.PUBLISH() (R__publication.sql) builds the outputs,
-- checks them and moves the pointer; nothing else writes these tables.

CREATE TABLE IF NOT EXISTS OPS.PUBLICATIONS (
  publication_id  STRING        NOT NULL PRIMARY KEY,
  state           STRING        NOT NULL,  -- published | held | failed
  started_at      TIMESTAMP_TZ  NOT NULL,
  finished_at     TIMESTAMP_TZ,
  input_hash      NUMBER,                  -- what the outputs were built from
  checks          VARIANT,                 -- {check name: passed}
  error           STRING
);

-- The current-Publication pointer: at most one row.
CREATE TABLE IF NOT EXISTS OPS.CURRENT_PUBLICATION (
  publication_id  STRING        NOT NULL,
  moved_at        TIMESTAMP_TZ  NOT NULL
);

-- Publications whose outputs OPS.PUBLISH() keeps: the current one and the 7
-- latest attempts (held ones stay for diagnosis).
CREATE OR REPLACE VIEW OPS.PUBLICATION_KEEP AS
SELECT publication_id FROM OPS.CURRENT_PUBLICATION
UNION
SELECT publication_id FROM OPS.PUBLICATIONS
QUALIFY ROW_NUMBER() OVER (ORDER BY started_at DESC, publication_id DESC) <= 7;

-- Same columns as the views they replace, plus publication_id.
CREATE TABLE IF NOT EXISTS ANALYTICS.PUB_OVERVIEW_DAILY AS
SELECT NULL::STRING AS publication_id, * FROM APP.OVERVIEW_DAILY WHERE FALSE;
CREATE TABLE IF NOT EXISTS ANALYTICS.PUB_DATA_COVERAGE AS
SELECT NULL::STRING AS publication_id, * FROM APP.DATA_COVERAGE WHERE FALSE;
CREATE TABLE IF NOT EXISTS ANALYTICS.PUB_QUARANTINE_SUMMARY AS
SELECT NULL::STRING AS publication_id, * FROM APP.QUARANTINE_SUMMARY WHERE FALSE;
CREATE TABLE IF NOT EXISTS ANALYTICS.PUB_TABLEAU_DAILY AS
SELECT NULL::STRING AS publication_id, * FROM APP.TABLEAU_DAILY WHERE FALSE;

CREATE OR REPLACE SECURE VIEW APP.OVERVIEW_DAILY COPY GRANTS AS
SELECT * EXCLUDE publication_id FROM ANALYTICS.PUB_OVERVIEW_DAILY
WHERE publication_id = (SELECT publication_id FROM OPS.CURRENT_PUBLICATION);

CREATE OR REPLACE SECURE VIEW APP.DATA_COVERAGE COPY GRANTS AS
SELECT * EXCLUDE publication_id FROM ANALYTICS.PUB_DATA_COVERAGE
WHERE publication_id = (SELECT publication_id FROM OPS.CURRENT_PUBLICATION);

CREATE OR REPLACE SECURE VIEW APP.QUARANTINE_SUMMARY COPY GRANTS AS
SELECT * EXCLUDE publication_id FROM ANALYTICS.PUB_QUARANTINE_SUMMARY
WHERE publication_id = (SELECT publication_id FROM OPS.CURRENT_PUBLICATION);

CREATE OR REPLACE SECURE VIEW APP.TABLEAU_DAILY COPY GRANTS AS
SELECT * EXCLUDE publication_id FROM ANALYTICS.PUB_TABLEAU_DAILY
WHERE publication_id = (SELECT publication_id FROM OPS.CURRENT_PUBLICATION);

-- One row, always: which Publication the dashboard shows, and whether to warn
-- that it is stale. Failure detail stays in OPS.PUBLICATIONS (not public).
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
reasons AS (
  SELECT
    cur.publication_id, cur.published_at, cur.data_as_of,
    att.state      AS latest_attempt_state,
    att.started_at AS latest_attempt_at,
    ARRAY_CONSTRUCT_COMPACT(
      IFF(cur.publication_id IS NULL, 'no Publication yet', NULL),
      IFF(att.state IN ('held', 'failed'), 'the latest Publication attempt ' || IFF(att.state = 'held', 'failed its checks', 'failed'), NULL),
      IFF(s.stuck_runs > 0, 'a collector run has waited more than 6 hours to load', NULL),
      IFF(cur.data_as_of < DATEADD(hour, -36, CURRENT_TIMESTAMP()), 'the data is more than 36 hours old', NULL)
    ) AS stale_reasons
  FROM stuck AS s
  LEFT JOIN current_publication AS cur ON TRUE
  LEFT JOIN latest_attempt AS att ON TRUE
)
SELECT
  publication_id, published_at, data_as_of, latest_attempt_state, latest_attempt_at,
  ARRAY_SIZE(stale_reasons) > 0       AS stale,
  ARRAY_TO_STRING(stale_reasons, '; ') AS stale_reason
FROM reasons;

GRANT SELECT ON VIEW APP.PUBLICATION_STATUS TO ROLE {{dashboard_role}};
