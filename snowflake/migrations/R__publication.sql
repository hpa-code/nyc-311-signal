-- Repeatable: re-applied whenever this file changes.
--
-- OPS.PUBLISH: the last step of the task graph (OPS.PUBLISH_TASK, after the
-- readiness check; see R__readiness.sql).
--   1. Wait (publish nothing) while a run is in flight: Observations not yet
--      processed, or a run still waiting to reconcile.
--   2. Skip if the latest attempt was published from the same inputs: the
--      data and this procedure's version. After a held or failed attempt it
--      always rebuilds, so a fix to the data or to this procedure is picked
--      up without waiting for new data.
--   3. In one transaction, holding the pipeline lock so no merge can run
--      meanwhile: build every output under a new publication_id (including
--      the day's backlog snapshot, ANALYTICS.DAILY_BACKLOG), run the checks,
--      and move the current-Publication pointer only if all pass. A failed
--      check records the attempt as held and keeps the pointer.
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
  snapshot_date DATE;
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

  -- Inputs include this procedure's own version (its migration checksum), so
  -- a change to the outputs rebuilds them without waiting for new data.
  SELECT HASH(r.n, r.loaded, c.n, c.merged, v.n, q.n, m.n, p.checksum) INTO :input_hash
  FROM (SELECT COUNT(*) AS n, MAX(loaded_at) AS loaded FROM RAW.RAW_311_OBSERVATIONS) AS r,
       (SELECT MAX(checksum) AS checksum FROM OPS.SCHEMA_MIGRATIONS WHERE version = 'R__publication') AS p,
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

  INSERT INTO ANALYTICS.PUB_DAILY_REQUEST_STATS
  SELECT :pid, stat_date, problem, agency, borough, SUM(submitted), SUM(closed)
  FROM (
    SELECT created_date_local AS stat_date, problem, agency, borough, 1 AS submitted, 0 AS closed
    FROM ANALYTICS.REQUEST_FACTS
    UNION ALL
    SELECT closed_at_local::DATE, problem, agency, borough, 0, 1
    FROM ANALYTICS.REQUEST_FACTS WHERE is_closed AND closed_at_local IS NOT NULL
  )
  GROUP BY 2, 3, 4, 5;

  -- Today's backlog snapshot, under this Publication's ID. Once the checks
  -- are done it replaces the day's earlier snapshot, or is dropped if held.
  SELECT CONVERT_TIMEZONE('America/New_York', MAX(source_updated_at))::DATE INTO :snapshot_date
  FROM CLEAN.SERVICE_REQUESTS;
  INSERT INTO ANALYTICS.DAILY_BACKLOG
  SELECT :snapshot_date, :pid, problem, agency, borough, COUNT_IF(NOT is_closed), CURRENT_TIMESTAMP()
  FROM ANALYTICS.REQUEST_FACTS
  WHERE :snapshot_date IS NOT NULL
  GROUP BY 3, 4, 5;

  INSERT INTO ANALYTICS.PUB_BACKLOG_DAILY
  SELECT :pid, snapshot_date, 'observed', problem, agency, borough, open_requests
  FROM ANALYTICS.DAILY_BACKLOG
  WHERE publication_id = :pid
     OR (snapshot_date <> :snapshot_date AND publication_id IN (SELECT publication_id FROM OPS.PUBLICATIONS WHERE state = 'published'));

  -- Retrospective: open at the end of day d = created by d and not Closed by d,
  -- as a running sum of +1 (created) and -1 (Closed) per group. Requests Closed
  -- without a usable Closure time are left out: when they left the backlog is unknown.
  INSERT INTO ANALYTICS.PUB_BACKLOG_DAILY
  WITH requests AS (
    SELECT problem, agency, borough, created_date_local, IFF(is_closed, closed_at_local::DATE, NULL) AS closed_date
    FROM ANALYTICS.REQUEST_FACTS
    WHERE NOT is_closed OR hours_to_close IS NOT NULL
  ),
  deltas AS (
    SELECT problem, agency, borough, day, SUM(delta) AS delta
    FROM (
      SELECT problem, agency, borough, created_date_local AS day, 1 AS delta FROM requests
      UNION ALL
      SELECT problem, agency, borough, closed_date, -1 FROM requests WHERE closed_date IS NOT NULL
    )
    GROUP BY 1, 2, 3, 4
  ),
  days AS (
    SELECT day FROM (
      SELECT DATEADD(day, ROW_NUMBER() OVER (ORDER BY SEQ4()) - 1, (SELECT MIN(created_date_local) FROM requests)) AS day
      FROM TABLE(GENERATOR(ROWCOUNT => 10000))
    )
    WHERE day <= :snapshot_date
  ),
  groups AS (
    SELECT DISTINCT problem, agency, borough FROM requests
  ),
  running AS (
    SELECT
      g.problem, g.agency, g.borough, d.day,
      SUM(COALESCE(x.delta, 0)) OVER (PARTITION BY g.problem, g.agency, g.borough ORDER BY d.day) AS open_requests
    FROM groups AS g
    CROSS JOIN days AS d
    LEFT JOIN deltas AS x ON x.problem = g.problem AND x.agency = g.agency AND x.borough = g.borough AND x.day = d.day
  )
  SELECT :pid, day, 'retrospective', problem, agency, borough, open_requests
  FROM running
  WHERE open_requests <> 0;

  INSERT INTO ANALYTICS.PUB_DURATION_STATS
  SELECT
    :pid,
    DATE_TRUNC('month', created_date_local), problem, agency, borough,
    COUNT(*),
    COUNT_IF(is_closed),
    COUNT_IF(NOT is_closed),
    COUNT(hours_to_close),
    MEDIAN(hours_to_close),
    PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY hours_to_close),
    COUNT_IF(horizon_passed),
    COUNT_IF(closed_within_horizon)
  FROM ANALYTICS.REQUEST_FACTS
  GROUP BY CUBE (DATE_TRUNC('month', created_date_local), problem, agency, borough);

  -- Per Problem only: an all-Problems row beside them would let a reader
  -- subtract one from the other and recover a cell's count under 20.
  -- A NULL h3_cell is always "no location": cells are never rolled up.
  INSERT INTO ANALYTICS.PUB_GEO_CELLS
  WITH cells AS (
    SELECT
      problem, h3_cell,
      COUNT(*)                        AS requests,
      COUNT_IF(NOT is_closed)         AS open_requests,
      COUNT_IF(horizon_passed)        AS horizon_passed,
      COUNT_IF(closed_within_horizon) AS closed_within_horizon,
      MEDIAN(hours_to_close)          AS median_hours
    FROM ANALYTICS.REQUEST_FACTS
    GROUP BY problem, h3_cell
  ),
  kinds AS (
    SELECT *, CASE WHEN h3_cell IS NULL THEN 'no_location' WHEN requests >= 20 THEN 'cell' ELSE 'hidden' END AS cell_kind
    FROM cells
  )
  SELECT
    :pid, problem, cell_kind, IFF(cell_kind = 'cell', h3_cell, NULL),
    SUM(requests), SUM(open_requests), SUM(horizon_passed), SUM(closed_within_horizon),
    IFF(cell_kind = 'cell', MAX(median_hours), NULL)
  FROM kinds
  GROUP BY problem, cell_kind, IFF(cell_kind = 'cell', h3_cell, NULL);

  INSERT INTO ANALYTICS.PUB_OVERDUE_QUEUE
  SELECT :pid, unique_key, problem, agency, borough, status, created_at_local, FLOOR(DATEDIFF(second, created_at_utc, as_of_utc) / 3600)
  FROM ANALYTICS.REQUEST_FACTS
  WHERE overdue;

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
    'tableau_matches_overview', t.requests = o.requests,
    'daily_stats_match_overview', ds.submitted = o.requests,
    'durations_count_every_request', COALESCE(du.requests, 0) = o.requests,
    'observed_backlog_matches_open_requests', b.open_requests = o.open_requests,
    'geo_cells_count_every_request', g.requests = o.requests,
    'geo_cells_have_at_least_20_requests', g.small_cells = 0,
    'overdue_requests_are_open', q.n <= o.open_requests
  ) INTO :checks
  FROM (
    SELECT
      COALESCE(SUM(requests), 0) AS requests,
      COALESCE(SUM(open_requests), 0) AS open_requests,
      COUNT_IF(closed_requests + open_requests <> requests) AS unbalanced_days
    FROM ANALYTICS.PUB_OVERVIEW_DAILY WHERE publication_id = :pid
  ) AS o,
  (
    SELECT COUNT(*) AS n, MAX(current_requests) AS current_requests
    FROM ANALYTICS.PUB_DATA_COVERAGE WHERE publication_id = :pid
  ) AS c,
  (SELECT COALESCE(SUM(requests), 0) AS requests FROM ANALYTICS.PUB_TABLEAU_DAILY WHERE publication_id = :pid) AS t,
  (SELECT COALESCE(SUM(submitted), 0) AS submitted FROM ANALYTICS.PUB_DAILY_REQUEST_STATS WHERE publication_id = :pid) AS ds,
  (
    SELECT MAX(requests) AS requests FROM ANALYTICS.PUB_DURATION_STATS
    WHERE publication_id = :pid AND created_month IS NULL AND problem IS NULL AND agency IS NULL AND borough IS NULL
  ) AS du,
  (
    SELECT COALESCE(SUM(open_requests), 0) AS open_requests FROM ANALYTICS.PUB_BACKLOG_DAILY
    WHERE publication_id = :pid AND kind = 'observed' AND backlog_date = :snapshot_date
  ) AS b,
  (
    SELECT COALESCE(SUM(requests), 0) AS requests, COUNT_IF(cell_kind = 'cell' AND requests < 20) AS small_cells
    FROM ANALYTICS.PUB_GEO_CELLS WHERE publication_id = :pid
  ) AS g,
  (SELECT COUNT(*) AS n FROM ANALYTICS.PUB_OVERDUE_QUEUE WHERE publication_id = :pid) AS q,
  (SELECT COUNT(*) AS n, COUNT(DISTINCT unique_key) AS distinct_keys FROM CLEAN.SERVICE_REQUESTS) AS cr;
  SELECT BOOLAND_AGG(value::BOOLEAN) INTO :passed FROM TABLE(FLATTEN(input => :checks));

  IF (passed) THEN
    DELETE FROM OPS.CURRENT_PUBLICATION;
    INSERT INTO OPS.CURRENT_PUBLICATION (publication_id, moved_at) VALUES (:pid, CURRENT_TIMESTAMP());
  END IF;
  -- One saved snapshot per day: this one, if it passed.
  IF (passed) THEN
    DELETE FROM ANALYTICS.DAILY_BACKLOG WHERE snapshot_date = :snapshot_date AND publication_id <> :pid;
  ELSE
    DELETE FROM ANALYTICS.DAILY_BACKLOG WHERE publication_id = :pid;
  END IF;
  UPDATE OPS.PUBLICATIONS
  SET state = IFF(:passed, 'published', 'held'), finished_at = CURRENT_TIMESTAMP(), checks = :checks
  WHERE publication_id = :pid;

  -- Keep outputs of the current Publication and the 7 latest attempts.
  DELETE FROM ANALYTICS.PUB_OVERVIEW_DAILY WHERE publication_id NOT IN (SELECT publication_id FROM OPS.PUBLICATION_KEEP);
  DELETE FROM ANALYTICS.PUB_DATA_COVERAGE WHERE publication_id NOT IN (SELECT publication_id FROM OPS.PUBLICATION_KEEP);
  DELETE FROM ANALYTICS.PUB_QUARANTINE_SUMMARY WHERE publication_id NOT IN (SELECT publication_id FROM OPS.PUBLICATION_KEEP);
  DELETE FROM ANALYTICS.PUB_TABLEAU_DAILY WHERE publication_id NOT IN (SELECT publication_id FROM OPS.PUBLICATION_KEEP);
  DELETE FROM ANALYTICS.PUB_DAILY_REQUEST_STATS WHERE publication_id NOT IN (SELECT publication_id FROM OPS.PUBLICATION_KEEP);
  DELETE FROM ANALYTICS.PUB_BACKLOG_DAILY WHERE publication_id NOT IN (SELECT publication_id FROM OPS.PUBLICATION_KEEP);
  DELETE FROM ANALYTICS.PUB_DURATION_STATS WHERE publication_id NOT IN (SELECT publication_id FROM OPS.PUBLICATION_KEEP);
  DELETE FROM ANALYTICS.PUB_GEO_CELLS WHERE publication_id NOT IN (SELECT publication_id FROM OPS.PUBLICATION_KEEP);
  DELETE FROM ANALYTICS.PUB_OVERDUE_QUEUE WHERE publication_id NOT IN (SELECT publication_id FROM OPS.PUBLICATION_KEEP);
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
