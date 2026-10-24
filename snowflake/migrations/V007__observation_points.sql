-- Exact Request Versions. Every distinct accepted Observation point is kept
-- (narrow columns, deduplicated, so replays add nothing), and versions are
-- recomputed from all points of a Request instead of from each version's
-- first and last point.

CREATE TABLE IF NOT EXISTS CLEAN.OBSERVATION_POINTS (
  unique_key         STRING        NOT NULL,
  payload_hash       STRING        NOT NULL,
  source_updated_at  TIMESTAMP_TZ  NOT NULL,
  extracted_at       TIMESTAMP_TZ  NOT NULL,
  run_id             STRING,
  source_file        STRING
);

-- Points for Observations already processed (RAW still holds them all).
INSERT INTO CLEAN.OBSERVATION_POINTS
SELECT
  r.observation:payload:unique_key::STRING,
  r.payload_hash,
  r.source_updated_at,
  r.extracted_at,
  MIN(r.run_id),
  MIN(r.source_file)
FROM RAW.RAW_311_OBSERVATIONS AS r
JOIN CLEAN.REQUEST_VERSIONS AS v
  ON v.unique_key = r.observation:payload:unique_key::STRING AND v.payload_hash = r.payload_hash
WHERE r.source_updated_at IS NOT NULL AND r.extracted_at IS NOT NULL
GROUP BY 1, 2, 3, 4;

ALTER TABLE CLEAN.REQUEST_VERSIONS RENAME COLUMN observations TO observation_count;
ALTER TABLE CLEAN.REQUEST_VERSIONS RENAME COLUMN run_id TO valid_from_run_id;
ALTER TABLE CLEAN.REQUEST_VERSIONS RENAME COLUMN source_file TO valid_from_source_file;
ALTER TABLE CLEAN.REQUEST_VERSIONS ADD COLUMN last_seen_run_id STRING;
ALTER TABLE CLEAN.REQUEST_VERSIONS ADD COLUMN last_seen_source_file STRING;

DROP TABLE IF EXISTS CLEAN.WORK_VERSION_CANDIDATES;
CREATE TABLE IF NOT EXISTS CLEAN.WORK_AFFECTED_REQUESTS (unique_key STRING);
CREATE TABLE IF NOT EXISTS CLEAN.WORK_NEW_VERSIONS LIKE CLEAN.REQUEST_VERSIONS;

CREATE OR REPLACE SECURE VIEW APP.DATA_COVERAGE COPY GRANTS AS
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
SELECT * FROM raw, current_requests, points, versions, quarantine, manifests;
