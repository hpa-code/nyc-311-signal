-- Monitoring (ticket 13): what each collector run did, the changes a weekly
-- reconciliation pass finds that the change cursor missed, and one row of
-- health signals for the CloudWatch monitor.

-- Measured by OPS.PROCESS_READY_RUNS when it processes a run, from that
-- run's Observations. updated_requests counts Requests a run gave a new
-- Request Version. missed_changes, for reconciliation runs (run ID
-- reconcile-*), counts those whose new version the cursor should already
-- have collected: its source :updated_at is no later than the newest
-- :updated_at any cursor run extracted before this run. Never pruned.
CREATE TABLE IF NOT EXISTS OPS.RUN_METRICS (
  run_id                    STRING        NOT NULL,
  distinct_requests         NUMBER        NOT NULL,
  updated_requests          NUMBER        NOT NULL,
  quarantined_rows          NUMBER        NOT NULL,
  latest_source_updated_at  TIMESTAMP_TZ,
  extracted_at              TIMESTAMP_TZ,
  missed_changes            NUMBER,
  processed_at              TIMESTAMP_TZ  NOT NULL
);

-- Every collector run the warehouse knows of, newest first. No error text:
-- failure detail stays in OPS (restricted). source_lag_hours is how old the
-- newest change in the run was when it was extracted: source freshness,
-- apart from whether the run succeeded.
CREATE OR REPLACE SECURE VIEW APP.RUN_HEALTH COPY GRANTS AS
SELECT
  r.run_id,
  CASE
    WHEN r.run_id LIKE 'cursor-%' THEN 'cursor'
    WHEN r.run_id LIKE 'reconcile-%' THEN 'reconcile'
    ELSE 'bulk'
  END                                                                            AS kind,
  r.extract_date,
  r.state,
  r.first_seen_at,
  r.consumed_at,
  r.expected_rows                                                                AS extracted_rows,
  r.loaded_rows,
  x.distinct_requests,
  x.updated_requests,
  x.quarantined_rows,
  x.missed_changes,
  x.extracted_at,
  x.latest_source_updated_at,
  (DATEDIFF(second, x.latest_source_updated_at, x.extracted_at) / 3600)::FLOAT   AS source_lag_hours,
  DATEDIFF(
    second, TRY_TO_TIMESTAMP_TZ(m.manifest:started_at::STRING), TRY_TO_TIMESTAMP_TZ(m.manifest:completed_at::STRING)
  )                                                                              AS collector_seconds
FROM OPS.PIPELINE_RUNS AS r
LEFT JOIN OPS.RUN_METRICS AS x ON x.run_id = r.run_id
LEFT JOIN OPS.RUN_MANIFESTS AS m ON m.run_id = r.run_id;

-- One row of health signals, read hourly by the monitor Lambda and turned
-- into CloudWatch metrics and alarms (infrastructure/terraform/modules/monitor).
CREATE OR REPLACE SECURE VIEW APP.MONITOR COPY GRANTS AS
WITH published AS (
  SELECT MAX(finished_at) AS published_at FROM OPS.PUBLICATIONS WHERE state = 'published'
),
latest_attempt AS (
  SELECT state FROM OPS.PUBLICATIONS
  QUALIFY ROW_NUMBER() OVER (ORDER BY started_at DESC, publication_id DESC) = 1
),
readiness AS (
  SELECT last_success_at, last_failure_at FROM OPS.STEP_STATUS WHERE step = 'readiness'
),
stuck AS (
  SELECT COUNT(*) AS stuck_runs FROM OPS.PIPELINE_RUNS
  WHERE state = 'waiting' AND first_seen_at < DATEADD(hour, -6, CURRENT_TIMESTAMP())
),
cursor_run AS (
  SELECT * FROM APP.RUN_HEALTH WHERE kind = 'cursor' AND state = 'consumed'
  QUALIFY ROW_NUMBER() OVER (ORDER BY extracted_at DESC NULLS LAST, run_id DESC) = 1
),
reconcile_run AS (
  SELECT * FROM APP.RUN_HEALTH WHERE kind = 'reconcile' AND state = 'consumed'
  QUALIFY ROW_NUMBER() OVER (ORDER BY extracted_at DESC NULLS LAST, run_id DESC) = 1
),
quarantine AS (
  SELECT COALESCE(SUM(quarantined_rows), 0) AS quarantined_rows_7d FROM OPS.RUN_METRICS
  WHERE processed_at >= DATEADD(day, -7, CURRENT_TIMESTAMP())
),
-- The latest published Publication's newly eligible Requests: how many it
-- scored, and how many of those had a Problem the scoring model never saw.
scoring AS (
  SELECT
    COUNT_IF(s.eligible)                                                               AS newly_eligible,
    COUNT(p.unique_key)                                                                AS newly_scored,
    COUNT_IF(p.unique_key IS NOT NULL AND GET(e.model:logistic_regression:weights:problem, s.problem) IS NULL) AS unknown_problem
  FROM ML.FEATURE_SNAPSHOTS AS s
  LEFT JOIN ML.REQUEST_PREDICTIONS AS p ON p.unique_key = s.unique_key AND p.publication_id = s.publication_id
  LEFT JOIN ML.MODEL_EVALUATIONS AS e ON e.model_version = p.model_version
  WHERE s.publication_id = (
    SELECT publication_id FROM OPS.PUBLICATIONS WHERE state = 'published'
    QUALIFY ROW_NUMBER() OVER (ORDER BY finished_at DESC, publication_id DESC) = 1
  )
)
SELECT
  (DATEDIFF(second, p.published_at, CURRENT_TIMESTAMP()) / 3600)::FLOAT           AS hours_since_publication,
  a.state                                                                         AS latest_attempt_state,
  (DATEDIFF(second, rd.last_success_at, CURRENT_TIMESTAMP()) / 3600)::FLOAT       AS hours_since_processing,
  COALESCE(rd.last_failure_at > rd.last_success_at, rd.last_failure_at IS NOT NULL) AS processing_failing,
  st.stuck_runs,
  c.run_id                                                                        AS cursor_run_id,
  (DATEDIFF(second, c.extracted_at, CURRENT_TIMESTAMP()) / 3600)::FLOAT           AS hours_since_cursor_run,
  c.source_lag_hours,
  c.extracted_rows                                                                AS cursor_extracted_rows,
  c.loaded_rows                                                                   AS cursor_loaded_rows,
  c.distinct_requests                                                             AS cursor_distinct_requests,
  c.updated_requests                                                              AS cursor_updated_requests,
  c.collector_seconds                                                             AS cursor_collector_seconds,
  r.run_id                                                                        AS reconcile_run_id,
  (DATEDIFF(second, r.extracted_at, CURRENT_TIMESTAMP()) / 86400)::FLOAT          AS days_since_reconciliation,
  r.missed_changes,
  q.quarantined_rows_7d,
  sc.newly_eligible,
  sc.newly_scored,
  IFF(sc.newly_eligible > 0, sc.newly_scored / sc.newly_eligible, NULL)::FLOAT           AS scoring_coverage,
  IFF(sc.newly_scored > 0, sc.unknown_problem / sc.newly_scored, NULL)::FLOAT            AS unknown_problem_rate
FROM stuck AS st
CROSS JOIN quarantine AS q
CROSS JOIN scoring AS sc
LEFT JOIN published AS p ON TRUE
LEFT JOIN latest_attempt AS a ON TRUE
LEFT JOIN readiness AS rd ON TRUE
LEFT JOIN cursor_run AS c ON TRUE
LEFT JOIN reconcile_run AS r ON TRUE;

-- Publication attempts without their check detail or error text.
CREATE OR REPLACE SECURE VIEW APP.PUBLICATION_HISTORY COPY GRANTS AS
SELECT publication_id, state, started_at, finished_at
FROM OPS.PUBLICATIONS;

-- Quarantine reasons by Publication, read by ID like the other pages.
CREATE OR REPLACE SECURE VIEW APP.PUBLISHED_QUARANTINE_SUMMARY COPY GRANTS AS
SELECT * FROM ANALYTICS.PUB_QUARANTINE_SUMMARY
WHERE publication_id IN (SELECT publication_id FROM OPS.PUBLICATIONS WHERE state = 'published');

GRANT SELECT ON VIEW APP.RUN_HEALTH TO ROLE {{dashboard_role}};
GRANT SELECT ON VIEW APP.PUBLISHED_QUARANTINE_SUMMARY TO ROLE {{dashboard_role}};
GRANT SELECT ON VIEW APP.MONITOR TO ROLE {{dashboard_role}};
GRANT SELECT ON VIEW APP.PUBLICATION_HISTORY TO ROLE {{dashboard_role}};
