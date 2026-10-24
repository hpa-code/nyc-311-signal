-- Repeatable: re-applied whenever this file changes.
--
-- OPS.PUBLISH: the last step of the task graph (OPS.PUBLISH_TASK, after the
-- readiness check; see R__readiness.sql).
--   1. Wait (publish nothing) while a run is in flight: Observations not yet
--      processed, or a run still waiting to reconcile.
--   2. Skip if the latest attempt was published from the same inputs. After a
--      held or failed attempt it always rebuilds, so a fix to the data or to
--      this procedure is picked up without waiting for new data.
--   3. In one transaction, holding the pipeline lock so no merge can run
--      meanwhile: build every output under a new publication_id, run the
--      checks, and move the current-Publication pointer only if all pass. A
--      failed check records the attempt as held and keeps the pointer.
--   4. Fail (after committing) when held, so the task's failure is the alert.
-- An error mid-build rolls everything back and records the attempt as failed.
CREATE OR REPLACE PROCEDURE OPS.PUBLISH()
RETURNS VARIANT
LANGUAGE SQL
EXECUTE AS OWNER
AS
$$
DECLARE
  pid STRING;
  pending_rows NUMBER;
  waiting_runs NUMBER;
  input_hash NUMBER;
  latest_hash NUMBER;
  latest_state STRING;
  checks VARIANT;
  passed BOOLEAN;
  err STRING;
  held EXCEPTION (-20002, 'Publication held: a check failed. See OPS.PUBLICATIONS.checks; the dashboard keeps the previous Publication.');
BEGIN
  -- Cheap early exit; the stream is checked again, inside the transaction, after the build.
  SELECT COUNT(*) INTO :pending_rows FROM (SELECT 1 FROM RAW.NEW_OBSERVATIONS LIMIT 1);
  IF (pending_rows > 0) THEN
    RETURN OBJECT_CONSTRUCT('outcome', 'waiting');
  END IF;

  SELECT TO_CHAR(SYSDATE(), 'YYYYMMDD"T"HH24MISS') || '-' || LEFT(UUID_STRING(), 8) INTO :pid;

  BEGIN TRANSACTION;
  {{include:transformations/take_pipeline_lock.sql}}

  SELECT COUNT(*) INTO :waiting_runs FROM OPS.PIPELINE_RUNS WHERE state = 'waiting';
  IF (waiting_runs > 0) THEN
    ROLLBACK;
    RETURN OBJECT_CONSTRUCT('outcome', 'waiting');
  END IF;

  SELECT HASH(r.n, r.loaded, c.n, c.merged, v.n, q.n, m.n) INTO :input_hash
  FROM (SELECT COUNT(*) AS n, MAX(loaded_at) AS loaded FROM RAW.RAW_311_OBSERVATIONS) AS r,
       (SELECT COUNT(*) AS n, MAX(merged_at) AS merged FROM CLEAN.SERVICE_REQUESTS) AS c,
       (SELECT COUNT(*) AS n FROM CLEAN.REQUEST_VERSIONS) AS v,
       (SELECT COUNT(*) AS n FROM CLEAN.QUARANTINED_RECORDS) AS q,
       (SELECT COUNT(*) AS n FROM OPS.RUN_MANIFESTS) AS m;
  SELECT MAX_BY(input_hash, started_at), MAX_BY(state, started_at) INTO :latest_hash, :latest_state
  FROM OPS.PUBLICATIONS;
  IF (latest_hash = input_hash AND latest_state = 'published') THEN
    ROLLBACK;
    RETURN OBJECT_CONSTRUCT('outcome', 'unchanged');
  END IF;

  INSERT INTO OPS.PUBLICATIONS (publication_id, state, started_at, input_hash)
  VALUES (:pid, 'building', CURRENT_TIMESTAMP(), :input_hash);

  INSERT INTO ANALYTICS.PUB_OVERVIEW_DAILY
  SELECT
    :pid,
    created_date_local                         AS created_date,
    COUNT(*)                                   AS requests,
    COUNT_IF(status = 'Closed')                AS closed_requests,
    COUNT_IF(status IS DISTINCT FROM 'Closed') AS open_requests
  FROM CLEAN.SERVICE_REQUESTS
  GROUP BY created_date_local;

  INSERT INTO ANALYTICS.PUB_DATA_COVERAGE
  WITH raw AS (
    SELECT
      COUNT(*)                                       AS observations_loaded,
      COUNT(DISTINCT source_file)                    AS files_loaded,
      COUNT(DISTINCT observation:payload:unique_key) AS distinct_requests_observed,
      MAX(extracted_at)                              AS latest_extracted_at,
      MAX(loaded_at)                                 AS latest_loaded_at
    FROM RAW.RAW_311_OBSERVATIONS
  ),
  current_requests AS (
    SELECT
      COUNT(*)                AS current_requests,
      MIN(created_date_local) AS first_created_date,
      MAX(created_date_local) AS last_created_date
    FROM CLEAN.SERVICE_REQUESTS
  ),
  points AS (
    SELECT COUNT(*) AS distinct_observations FROM CLEAN.OBSERVATION_POINTS
  ),
  versions AS (
    SELECT COUNT(*) AS request_versions FROM CLEAN.REQUEST_VERSIONS
  ),
  quarantine AS (
    SELECT COUNT(*) AS quarantined_observations FROM CLEAN.QUARANTINED_RECORDS
  ),
  manifests AS (
    SELECT
      COUNT(*)                     AS runs,
      COALESCE(SUM(file_count), 0) AS manifest_files,
      COALESCE(SUM(row_count), 0)  AS manifest_rows
    FROM OPS.RUN_MANIFESTS
  )
  SELECT :pid, * FROM raw, current_requests, points, versions, quarantine, manifests;

  INSERT INTO ANALYTICS.PUB_QUARANTINE_SUMMARY
  SELECT :pid, r.value::STRING AS reason, COUNT(*) AS observations
  FROM CLEAN.QUARANTINED_RECORDS AS q, LATERAL FLATTEN(input => q.reasons) AS r
  GROUP BY 2;

  INSERT INTO ANALYTICS.PUB_TABLEAU_DAILY
  SELECT
    :pid,
    created_date_local                          AS created_date,
    COALESCE(borough, 'Unspecified')            AS borough,
    COALESCE(agency, 'Unspecified')             AS agency,
    COALESCE(problem, 'Unspecified')            AS problem,
    COALESCE(open_data_channel_type, 'UNKNOWN') AS channel,
    COUNT(*)                                    AS requests,
    COUNT_IF(status = 'Closed')                 AS closed_requests,
    COUNT_IF(status IS DISTINCT FROM 'Closed')  AS open_requests
  FROM CLEAN.SERVICE_REQUESTS
  GROUP BY 2, 3, 4, 5, 6;

  -- First stream read in this transaction: if Snowpipe loaded files mid-build,
  -- the coverage counts may include part of a run, so drop them and wait.
  SELECT COUNT(*) INTO :pending_rows FROM (SELECT 1 FROM RAW.NEW_OBSERVATIONS LIMIT 1);
  IF (pending_rows > 0) THEN
    ROLLBACK;
    RETURN OBJECT_CONSTRUCT('outcome', 'waiting');
  END IF;

  SELECT OBJECT_CONSTRUCT(
    'one_current_request_per_unique_key', cr.n = cr.distinct_keys,
    'overview_counts_every_current_request', o.requests = cr.n,
    'closed_plus_open_equals_requests', o.unbalanced_days = 0,
    'coverage_has_one_row', c.n = 1,
    'coverage_matches_overview', c.current_requests = o.requests,
    'tableau_matches_overview', t.requests = o.requests
  ) INTO :checks
  FROM (
    SELECT COALESCE(SUM(requests), 0) AS requests, COUNT_IF(closed_requests + open_requests <> requests) AS unbalanced_days
    FROM ANALYTICS.PUB_OVERVIEW_DAILY WHERE publication_id = :pid
  ) AS o,
  (
    SELECT COUNT(*) AS n, MAX(current_requests) AS current_requests
    FROM ANALYTICS.PUB_DATA_COVERAGE WHERE publication_id = :pid
  ) AS c,
  (SELECT COALESCE(SUM(requests), 0) AS requests FROM ANALYTICS.PUB_TABLEAU_DAILY WHERE publication_id = :pid) AS t,
  (SELECT COUNT(*) AS n, COUNT(DISTINCT unique_key) AS distinct_keys FROM CLEAN.SERVICE_REQUESTS) AS cr;
  SELECT BOOLAND_AGG(value::BOOLEAN) INTO :passed FROM TABLE(FLATTEN(input => :checks));

  IF (passed) THEN
    DELETE FROM OPS.CURRENT_PUBLICATION;
    INSERT INTO OPS.CURRENT_PUBLICATION (publication_id, moved_at) VALUES (:pid, CURRENT_TIMESTAMP());
  END IF;
  UPDATE OPS.PUBLICATIONS
  SET state = IFF(:passed, 'published', 'held'), finished_at = CURRENT_TIMESTAMP(), checks = :checks
  WHERE publication_id = :pid;

  -- Keep outputs of the current Publication and the 7 latest attempts.
  DELETE FROM ANALYTICS.PUB_OVERVIEW_DAILY WHERE publication_id NOT IN (SELECT publication_id FROM OPS.PUBLICATION_KEEP);
  DELETE FROM ANALYTICS.PUB_DATA_COVERAGE WHERE publication_id NOT IN (SELECT publication_id FROM OPS.PUBLICATION_KEEP);
  DELETE FROM ANALYTICS.PUB_QUARANTINE_SUMMARY WHERE publication_id NOT IN (SELECT publication_id FROM OPS.PUBLICATION_KEEP);
  DELETE FROM ANALYTICS.PUB_TABLEAU_DAILY WHERE publication_id NOT IN (SELECT publication_id FROM OPS.PUBLICATION_KEEP);
  COMMIT;

  IF (NOT passed) THEN
    RAISE held;
  END IF;
  RETURN OBJECT_CONSTRUCT('outcome', 'published', 'publication_id', :pid);
EXCEPTION
  WHEN held THEN
    RAISE;
  WHEN OTHER THEN
    ROLLBACK;
    err := SQLERRM;
    INSERT INTO OPS.PUBLICATIONS (publication_id, state, started_at, finished_at, error)
    VALUES (COALESCE(:pid, 'unassigned-' || UUID_STRING()), 'failed', CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(), :err);
    RAISE;
END;
$$;
