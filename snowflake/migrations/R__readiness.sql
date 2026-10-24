-- Repeatable: re-applied whenever this file or anything it includes changes.
--
-- OPS.PROCESS_READY_RUNS: the gate between loading and downstream work.
--   1. Load new run manifests (the control path).
--   2. Reconcile every waiting run, file by file, against RAW.
--   3. Process pending Observations only if every pending file belongs to a
--      reconciled run; otherwise wait and consume nothing.
--   4. Mark those runs consumed, in the same transaction as the processing.
--   5. Fail (after committing) if any run has waited more than 6 hours, or a
--      pending file has had no reconciled run for 6 hours, so the task's
--      failure surfaces a stuck pipeline instead of waiting silently.
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
  stuck EXCEPTION (-20001, 'Readiness: a run has waited more than 6 hours. See OPS.PIPELINE_RUNS (state waiting) and RAW.NEW_OBSERVATIONS.');
BEGIN
  {{include:transformations/load_manifests.sql}}

  BEGIN TRANSACTION;

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
  RETURN result;
EXCEPTION
  WHEN OTHER THEN
    ROLLBACK;
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

ALTER TASK OPS.READINESS_TASK {{task_state}};

-- Once a day whatever the stream holds: consumes runs that loaded no rows
-- (an empty pass never wakes READINESS_TASK) and fails if anything is stuck.
-- 07:45 UTC: after the 06:00 collection, away from READINESS_TASK's :00/:30.
CREATE OR REPLACE TASK OPS.READINESS_SWEEP_TASK
  WAREHOUSE = {{warehouse}}
  SCHEDULE = 'USING CRON 45 7 * * * UTC'
AS
  CALL OPS.PROCESS_READY_RUNS();

ALTER TASK OPS.READINESS_SWEEP_TASK {{task_state}};
