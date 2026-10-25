-- Scoring coverage on the latest published Publication that froze eligible
-- Requests: most Publications (a sweep, a rebuild) freeze none, and then the
-- signal went missing. Otherwise as V022.
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
processed AS (
  SELECT MAX(consumed_at) AS consumed_at FROM OPS.PIPELINE_RUNS WHERE state = 'consumed'
),
stuck AS (
  SELECT COUNT(*) AS stuck_runs FROM OPS.PIPELINE_RUNS
  WHERE state = 'waiting' AND first_seen_at < DATEADD(hour, -6, CURRENT_TIMESTAMP())
),
cursor_run AS (
  SELECT * FROM APP.RUN_HEALTH WHERE kind = 'cursor' AND state = 'consumed'
  QUALIFY ROW_NUMBER() OVER (ORDER BY extracted_at DESC NULLS LAST, run_id DESC) = 1
),
newest_change AS (
  SELECT MAX(latest_source_updated_at) AS source_updated_at FROM APP.RUN_HEALTH WHERE kind = 'cursor'
),
reconcile_run AS (
  SELECT * FROM APP.RUN_HEALTH WHERE kind = 'reconcile' AND state = 'consumed'
  QUALIFY ROW_NUMBER() OVER (ORDER BY extracted_at DESC NULLS LAST, run_id DESC) = 1
),
metrics_began AS (
  SELECT MIN(processed_at) AS processed_at FROM OPS.RUN_METRICS
),
quarantine AS (
  SELECT COALESCE(SUM(quarantined_rows), 0) AS quarantined_rows_7d FROM OPS.RUN_METRICS
  WHERE processed_at >= DATEADD(day, -7, CURRENT_TIMESTAMP())
),
-- The newly eligible Requests of the latest published Publication that had
-- any: how many it scored, and how many of those had a Problem the scoring
-- model never saw.
scoring AS (
  SELECT
    COUNT_IF(s.eligible)                                                               AS newly_eligible,
    COUNT(p.unique_key)                                                                AS newly_scored,
    COUNT_IF(p.unique_key IS NOT NULL AND GET(e.model:logistic_regression:weights:problem, s.problem) IS NULL) AS unknown_problem
  FROM ML.FEATURE_SNAPSHOTS AS s
  LEFT JOIN ML.REQUEST_PREDICTIONS AS p ON p.unique_key = s.unique_key AND p.publication_id = s.publication_id
  LEFT JOIN ML.MODEL_EVALUATIONS AS e ON e.model_version = p.model_version
  WHERE s.eligible AND s.publication_id = (
    SELECT publication_id FROM OPS.PUBLICATIONS
    WHERE state = 'published'
      AND publication_id IN (SELECT publication_id FROM ML.FEATURE_SNAPSHOTS WHERE eligible)
    QUALIFY ROW_NUMBER() OVER (ORDER BY finished_at DESC, publication_id DESC) = 1
  )
)
SELECT
  (DATEDIFF(second, p.published_at, CURRENT_TIMESTAMP()) / 3600)::FLOAT           AS hours_since_publication,
  a.state                                                                         AS latest_attempt_state,
  IFF(a.state IN ('held', 'failed'), 1, 0)                                        AS latest_attempt_failed,
  (DATEDIFF(second, pr.consumed_at, CURRENT_TIMESTAMP()) / 3600)::FLOAT           AS hours_since_processing,
  IFF(COALESCE(rd.last_failure_at > rd.last_success_at, rd.last_failure_at IS NOT NULL), 1, 0) AS processing_failing,
  st.stuck_runs,
  c.run_id                                                                        AS cursor_run_id,
  (DATEDIFF(second, c.extracted_at, CURRENT_TIMESTAMP()) / 3600)::FLOAT           AS hours_since_cursor_run,
  (DATEDIFF(second, nc.source_updated_at, c.extracted_at) / 3600)::FLOAT          AS source_lag_hours,
  c.extracted_rows                                                                AS cursor_extracted_rows,
  c.loaded_rows                                                                   AS cursor_loaded_rows,
  c.distinct_requests                                                             AS cursor_distinct_requests,
  c.updated_requests                                                              AS cursor_updated_requests,
  c.collector_seconds                                                             AS cursor_collector_seconds,
  r.run_id                                                                        AS reconcile_run_id,
  (DATEDIFF(second, COALESCE(r.extracted_at, mb.processed_at), CURRENT_TIMESTAMP()) / 86400)::FLOAT AS days_since_reconciliation,
  r.missed_changes,
  q.quarantined_rows_7d,
  sc.newly_eligible,
  sc.newly_scored,
  IFF(sc.newly_eligible > 0, sc.newly_scored / sc.newly_eligible, NULL)::FLOAT    AS scoring_coverage,
  IFF(sc.newly_scored > 0, sc.unknown_problem / sc.newly_scored, NULL)::FLOAT     AS unknown_problem_rate
FROM stuck AS st
CROSS JOIN quarantine AS q
CROSS JOIN scoring AS sc
CROSS JOIN newest_change AS nc
CROSS JOIN metrics_began AS mb
CROSS JOIN processed AS pr
LEFT JOIN published AS p ON TRUE
LEFT JOIN latest_attempt AS a ON TRUE
LEFT JOIN readiness AS rd ON TRUE
LEFT JOIN cursor_run AS c ON TRUE
LEFT JOIN reconcile_run AS r ON TRUE;
