-- Run metrics for runs consumed before OPS.RUN_METRICS existed, measured once
-- from what remains: RAW (90-day retention), Request Versions and Quarantined
-- Observations. missed_changes stays NULL: no reconciliation ran before.
INSERT INTO OPS.RUN_METRICS
WITH runs AS (
  SELECT run_id FROM OPS.PIPELINE_RUNS
  WHERE state = 'consumed' AND run_id NOT IN (SELECT run_id FROM OPS.RUN_METRICS)
),
observed AS (
  SELECT
    run_id,
    COUNT(DISTINCT observation:payload:unique_key::STRING) AS distinct_requests,
    MAX(source_updated_at)                                  AS latest_source_updated_at,
    MAX(extracted_at)                                       AS extracted_at
  FROM RAW.RAW_311_OBSERVATIONS
  WHERE run_id IN (SELECT run_id FROM runs)
  GROUP BY run_id
),
versions AS (
  SELECT valid_from_run_id AS run_id, COUNT(DISTINCT unique_key) AS updated_requests
  FROM CLEAN.REQUEST_VERSIONS
  WHERE valid_from_run_id IN (SELECT run_id FROM runs)
  GROUP BY valid_from_run_id
),
quarantined AS (
  SELECT run_id, COUNT(*) AS quarantined_rows
  FROM CLEAN.QUARANTINED_RECORDS
  WHERE run_id IN (SELECT run_id FROM runs)
  GROUP BY run_id
)
SELECT
  r.run_id,
  COALESCE(o.distinct_requests, 0),
  COALESCE(v.updated_requests, 0),
  COALESCE(q.quarantined_rows, 0),
  o.latest_source_updated_at,
  o.extracted_at,
  NULL,
  CURRENT_TIMESTAMP()
FROM runs AS r
LEFT JOIN observed AS o ON o.run_id = r.run_id
LEFT JOIN versions AS v ON v.run_id = r.run_id
LEFT JOIN quarantined AS q ON q.run_id = r.run_id;
