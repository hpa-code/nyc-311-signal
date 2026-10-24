-- Request Versions, Quarantined Observations, and the change stream that
-- feeds them. SHOW_INITIAL_ROWS makes the first run process RAW rows loaded
-- before this migration; that first read returns only those rows, so rows
-- loaded after the migration are processed one run later.

CREATE STREAM IF NOT EXISTS RAW.NEW_OBSERVATIONS
  ON TABLE RAW.RAW_311_OBSERVATIONS
  APPEND_ONLY = TRUE
  SHOW_INITIAL_ROWS = TRUE;

-- One Quarantined Observation: rejected by data-quality rules, kept whole with
-- every reason that applied, so it can be diagnosed and replayed.
CREATE TABLE IF NOT EXISTS CLEAN.QUARANTINED_RECORDS (
  source_file        STRING        NOT NULL,
  source_row_number  NUMBER        NOT NULL,
  loaded_at          TIMESTAMP_TZ  NOT NULL,
  run_id             STRING,
  unique_key         STRING,
  reasons            ARRAY         NOT NULL,
  observation        VARIANT       NOT NULL,
  quarantined_at     TIMESTAMP_TZ  NOT NULL
);

-- One Request Version: a run of consecutive Observations (ordered by source
-- :updated_at, extraction time, payload hash) with the same payload hash.
-- valid_from_* is the run's first point and last_seen_* its last, so a late
-- Observation that lands inside a run splits it correctly.
CREATE TABLE IF NOT EXISTS CLEAN.REQUEST_VERSIONS (
  unique_key               STRING        NOT NULL,
  payload_hash             STRING        NOT NULL,
  valid_from_updated_at    TIMESTAMP_TZ  NOT NULL,
  valid_from_extracted_at  TIMESTAMP_TZ  NOT NULL,
  last_seen_updated_at     TIMESTAMP_TZ  NOT NULL,
  last_seen_extracted_at   TIMESTAMP_TZ  NOT NULL,
  first_extracted_at       TIMESTAMP_TZ  NOT NULL,
  observations             NUMBER        NOT NULL,
  payload                  VARIANT       NOT NULL,
  run_id                   STRING,
  source_file              STRING
);

-- Work tables for one processing transaction (DML only inside it: DDL would
-- commit the transaction early). Emptied at the start of every run.
CREATE TABLE IF NOT EXISTS CLEAN.WORK_NEW_OBSERVATIONS (
  source_file        STRING,
  source_row_number  NUMBER,
  loaded_at          TIMESTAMP_TZ,
  run_id             STRING,
  extracted_at       TIMESTAMP_TZ,
  payload_hash       STRING,
  source_updated_at  TIMESTAMP_TZ,
  unique_key         STRING,
  reasons            ARRAY,
  observation        VARIANT
);

CREATE TABLE IF NOT EXISTS CLEAN.WORK_VERSION_CANDIDATES (
  unique_key    STRING,
  payload_hash  STRING,
  updated_at    TIMESTAMP_TZ,
  extracted_at  TIMESTAMP_TZ,
  first_extracted_at TIMESTAMP_TZ,
  weight        NUMBER,
  payload       VARIANT,
  run_id        STRING,
  source_file   STRING
);

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
SELECT * FROM raw, current_requests, versions, quarantine, manifests;

CREATE OR REPLACE SECURE VIEW APP.QUARANTINE_SUMMARY COPY GRANTS AS
SELECT r.value::STRING AS reason, COUNT(*) AS observations
FROM CLEAN.QUARANTINED_RECORDS AS q, LATERAL FLATTEN(input => q.reasons) AS r
GROUP BY 1;

GRANT SELECT ON ALL VIEWS IN SCHEMA APP TO ROLE {{dashboard_role}};
