-- Repeatable: re-applied whenever this file or anything it includes changes.
--
-- OPS.PROCESS_READY_RUNS: the gate between loading and downstream work.
--   1. Load new run manifests (the control path).
--   2. Reconcile every waiting run, file by file, against RAW.
--   3. Process pending Observations only if every pending file belongs to a
--      reconciled run; otherwise wait and consume nothing.
--   4. Mark those runs consumed and record what each did (OPS.RUN_METRICS,
--      including the changes a reconciliation run found that the cursor
--      missed), in the same transaction as the processing.
--   5. Fail (after committing) if any run has waited more than 6 hours, or a
--      pending file has had no reconciled run for 6 hours, so the task's
--      failure surfaces a stuck pipeline instead of waiting silently.
--   6. Record success or failure in OPS.STEP_STATUS for the dashboard.
-- The transaction holds the pipeline lock, so OPS.PUBLISH never builds from
-- a half-merged state.
-- Inside the transaction the stream is read at one snapshot (repeatable
-- read), taken after RAW was counted, so the check and the processing see
-- the same rows.
CREATE OR REPLACE PROCEDURE OPS.PROCESS_READY_RUNS()
RETURNS VARIANT
LANGUAGE SQL
EXECUTE AS OWNER
AS
$$
DECLARE
  blocked_files NUMBER;
  stale_blocked_files NUMBER;
  stale_runs NUMBER;
  pending_rows NUMBER;
  ready_runs NUMBER;
  ready_run_ids ARRAY;
  result VARIANT;
  err STRING;
  stuck EXCEPTION (-20001, 'Readiness: a run has waited more than 6 hours. See OPS.PIPELINE_RUNS (state waiting) and RAW.NEW_OBSERVATIONS.');
BEGIN
  {{include:transformations/load_manifests.sql}}

  BEGIN TRANSACTION;
  {{include:transformations/take_pipeline_lock.sql}}

  MERGE INTO OPS.PIPELINE_RUNS AS r
  USING OPS.RUN_MANIFESTS AS m
  ON r.run_id = m.run_id
  WHEN NOT MATCHED THEN INSERT (
    run_id, extract_date, manifest_file, expected_files, expected_rows, loaded_files, loaded_rows,
    reconciled, state, first_seen_at
  ) VALUES (
    m.run_id, m.extract_date, m.source_file, m.file_count, m.row_count, 0, 0,
    FALSE, 'waiting', CURRENT_TIMESTAMP()
  );

  DELETE FROM OPS.WORK_RUN_FILES;
  INSERT INTO OPS.WORK_RUN_FILES
  WITH expected AS (
    SELECT m.run_id, f.value:key::STRING AS file_key, f.value:rows::NUMBER AS expected_rows
    FROM OPS.RUN_MANIFESTS AS m, LATERAL FLATTEN(input => m.manifest:files) AS f
    WHERE m.run_id IN (SELECT run_id FROM OPS.PIPELINE_RUNS WHERE state = 'waiting')
  ),
  loaded AS (
    -- Distinct row numbers: a file loaded twice still counts once; the extra
    -- copies are recorded as duplicate_rows.
    SELECT source_file, COUNT(DISTINCT source_row_number) AS loaded_rows, COUNT(*) AS raw_rows
    FROM RAW.RAW_311_OBSERVATIONS
    WHERE source_file IN (SELECT file_key FROM expected)
    GROUP BY source_file
  )
  SELECT e.run_id, e.file_key, e.expected_rows, COALESCE(l.loaded_rows, 0), COALESCE(l.raw_rows, 0)
  FROM expected AS e LEFT JOIN loaded AS l ON l.source_file = e.file_key;

  UPDATE OPS.PIPELINE_RUNS AS r
  SET loaded_files   = COALESCE(c.complete_files, 0),
      loaded_rows    = COALESCE(c.loaded_rows, 0),
      duplicate_rows = COALESCE(c.raw_rows, 0) - COALESCE(c.loaded_rows, 0),
      reconciled   = COALESCE(c.complete_files, 0) = r.expected_files
                     AND COALESCE(c.loaded_rows, 0) = r.expected_rows,
      checked_at   = CURRENT_TIMESTAMP()
  FROM (
    SELECT p.run_id,
           COUNT_IF(w.loaded_rows = w.expected_rows) AS complete_files,
           SUM(w.loaded_rows)                        AS loaded_rows,
           SUM(w.raw_rows)                           AS raw_rows
    FROM OPS.PIPELINE_RUNS AS p LEFT JOIN OPS.WORK_RUN_FILES AS w ON w.run_id = p.run_id
    WHERE p.state = 'waiting'
    GROUP BY p.run_id
  ) AS c
  WHERE r.run_id = c.run_id;

  SELECT COUNT(*), ARRAY_AGG(run_id) WITHIN GROUP (ORDER BY run_id)
    INTO :ready_runs, :ready_run_ids
  FROM OPS.PIPELINE_RUNS WHERE state = 'waiting' AND reconciled;

  -- First read of the stream in this transaction: its snapshot is fixed from here.
  SELECT COUNT(*),
         COUNT(DISTINCT IFF(ready.file_key IS NULL, n.source_file, NULL)),
         COUNT(DISTINCT IFF(ready.file_key IS NULL AND n.loaded_at < DATEADD(hour, -6, CURRENT_TIMESTAMP()),
                            n.source_file, NULL))
    INTO :pending_rows, :blocked_files, :stale_blocked_files
  FROM RAW.NEW_OBSERVATIONS AS n
  LEFT JOIN (
    SELECT DISTINCT file_key FROM OPS.WORK_RUN_FILES
    WHERE ARRAY_CONTAINS(run_id::VARIANT, :ready_run_ids)
  ) AS ready ON ready.file_key = n.source_file;

  IF (blocked_files > 0) THEN
    SELECT OBJECT_CONSTRUCT('outcome', 'waiting', 'blocked_files', :blocked_files,
                            'pending_rows', :pending_rows, 'ready_runs', :ready_run_ids)
      INTO :result;
  ELSEIF (pending_rows = 0 AND ready_runs = 0) THEN
    SELECT OBJECT_CONSTRUCT('outcome', 'idle') INTO :result;
  ELSE
    {{include:transformations/process/01_take_new_observations.sql}}
    {{include:transformations/process/02_quarantine.sql}}
    {{include:transformations/process/03_request_versions.sql}}
    {{include:transformations/process/04_merge_current_requests.sql}}

    -- A run with no files (an empty pass) is consumed here too.
    UPDATE OPS.PIPELINE_RUNS
    SET state = 'consumed', consumed_at = CURRENT_TIMESTAMP(), observations_processed = 0
    WHERE ARRAY_CONTAINS(run_id::VARIANT, :ready_run_ids);
    UPDATE OPS.PIPELINE_RUNS AS r
    SET observations_processed = c.observations
    FROM (
      SELECT w.run_id, COUNT(*) AS observations
      FROM OPS.WORK_RUN_FILES AS w JOIN CLEAN.WORK_NEW_OBSERVATIONS AS n ON n.source_file = w.file_key
      GROUP BY w.run_id
    ) AS c
    WHERE r.run_id = c.run_id AND ARRAY_CONTAINS(r.run_id::VARIANT, :ready_run_ids);

    INSERT INTO OPS.RUN_METRICS
    WITH runs AS (
      SELECT run_id FROM OPS.PIPELINE_RUNS WHERE ARRAY_CONTAINS(run_id::VARIANT, :ready_run_ids)
    ),
    observed AS (
      SELECT
        w.run_id,
        COUNT(DISTINCT n.unique_key)          AS distinct_requests,
        COUNT_IF(ARRAY_SIZE(n.reasons) > 0)   AS quarantined_rows,
        MAX(n.source_updated_at)              AS latest_source_updated_at,
        MAX(n.extracted_at)                   AS extracted_at,
        MIN(n.extracted_at)                   AS first_extracted_at
      FROM CLEAN.WORK_NEW_OBSERVATIONS AS n
      JOIN OPS.WORK_RUN_FILES AS w ON w.file_key = n.source_file
      GROUP BY w.run_id
    ),
    -- The newest change any cursor run extracted before each run started.
    watermarks AS (
      SELECT o.run_id, MAX(c.source_updated_at) AS cursor_watermark
      FROM observed AS o
      JOIN RAW.RAW_311_OBSERVATIONS AS c ON c.run_id LIKE 'cursor-%' AND c.extracted_at < o.first_extracted_at
      WHERE o.run_id LIKE 'reconcile-%'
      GROUP BY o.run_id
    ),
    versions AS (
      SELECT
        v.valid_from_run_id                   AS run_id,
        COUNT(DISTINCT v.unique_key)          AS updated_requests,
        COUNT(DISTINCT IFF(v.valid_from_updated_at <= wm.cursor_watermark, v.unique_key, NULL)) AS missed_changes
      FROM CLEAN.REQUEST_VERSIONS AS v
      LEFT JOIN watermarks AS wm ON wm.run_id = v.valid_from_run_id
      WHERE v.valid_from_run_id IN (SELECT run_id FROM runs)
      GROUP BY v.valid_from_run_id
    )
    SELECT
      r.run_id,
      COALESCE(o.distinct_requests, 0),
      COALESCE(v.updated_requests, 0),
      COALESCE(o.quarantined_rows, 0),
      o.latest_source_updated_at,
      o.extracted_at,
      IFF(r.run_id LIKE 'reconcile-%', COALESCE(v.missed_changes, 0), NULL),
      CURRENT_TIMESTAMP()
    FROM runs AS r
    LEFT JOIN observed AS o ON o.run_id = r.run_id
    LEFT JOIN versions AS v ON v.run_id = r.run_id;

    SELECT OBJECT_CONSTRUCT(
             'outcome', 'processed',
             'runs', :ready_run_ids,
             'observations', COUNT(*),
             'quarantined', COALESCE(COUNT_IF(ARRAY_SIZE(reasons) > 0), 0))
      INTO :result
    FROM CLEAN.WORK_NEW_OBSERVATIONS;
  END IF;

  SELECT COUNT(*) INTO :stale_runs
  FROM OPS.PIPELINE_RUNS
  WHERE state = 'waiting' AND first_seen_at < DATEADD(hour, -6, CURRENT_TIMESTAMP());
  COMMIT;

  IF (stale_runs > 0 OR stale_blocked_files > 0) THEN
    RAISE stuck;
  END IF;
  UPDATE OPS.STEP_STATUS SET last_success_at = CURRENT_TIMESTAMP() WHERE step = 'readiness';
  RETURN result;
EXCEPTION
  WHEN OTHER THEN
    ROLLBACK;
    err := SQLERRM;
    UPDATE OPS.STEP_STATUS SET last_failure_at = CURRENT_TIMESTAMP(), last_error = :err WHERE step = 'readiness';
    RAISE;
END;
$$;

-- Wakes only while the stream holds unprocessed Observations; each wake that
-- finds a run still loading costs one short warehouse resume.
CREATE OR REPLACE TASK OPS.READINESS_TASK
  WAREHOUSE = {{warehouse}}
  SCHEDULE = 'USING CRON */30 * * * * UTC'
  WHEN SYSTEM$STREAM_HAS_DATA('RAW.NEW_OBSERVATIONS')
AS
  CALL OPS.PROCESS_READY_RUNS();

-- Once a day whatever the stream holds: consumes runs that loaded no rows
-- (an empty pass never wakes READINESS_TASK) and fails if anything is stuck.
-- 07:45 UTC: after the 06:00 collection, away from READINESS_TASK's :00/:30.
CREATE OR REPLACE TASK OPS.READINESS_SWEEP_TASK
  WAREHOUSE = {{warehouse}}
  SCHEDULE = 'USING CRON 45 7 * * * UTC'
AS
  CALL OPS.PROCESS_READY_RUNS();

-- The rest of the task graph: publish (R__publication.sql) after each
-- successful readiness check. A check that raises (a stuck run) publishes
-- nothing, so the dashboard keeps its Publication and shows it as stale.
CREATE OR REPLACE TASK OPS.PUBLISH_TASK
  WAREHOUSE = {{warehouse}}
  AFTER OPS.READINESS_TASK
AS
  CALL OPS.PUBLISH();

CREATE OR REPLACE TASK OPS.PUBLISH_AFTER_SWEEP_TASK
  WAREHOUSE = {{warehouse}}
  AFTER OPS.READINESS_SWEEP_TASK
AS
  CALL OPS.PUBLISH();

-- Children before roots: a root must be suspended while its graph changes.
ALTER TASK OPS.PUBLISH_TASK {{task_state}};
ALTER TASK OPS.PUBLISH_AFTER_SWEEP_TASK {{task_state}};
ALTER TASK OPS.READINESS_TASK {{task_state}};
ALTER TASK OPS.READINESS_SWEEP_TASK {{task_state}};
