-- Daily loading: Snowpipe auto-ingests new Observation files from data/ only
-- (the stage's URL); manifests reach OPS.RUN_MANIFESTS through the readiness
-- procedure's own COPY (R__readiness.sql), never through a pipe.
-- In test databases AUTO_INGEST is FALSE, so they never load the dev bucket.
-- METADATA$START_SCAN_TIME stands in for CURRENT_TIMESTAMP(), which pipes do not take.
CREATE PIPE IF NOT EXISTS RAW.OBSERVATIONS_PIPE
  AUTO_INGEST = {{auto_ingest}}
AS
COPY INTO RAW.RAW_311_OBSERVATIONS (
  source_file, source_row_number, loaded_at, run_id, extracted_at, source_dataset,
  schema_version, payload_hash, source_created_at, source_updated_at, observation
)
FROM (
  SELECT
    METADATA$FILENAME,
    METADATA$FILE_ROW_NUMBER,
    METADATA$START_SCAN_TIME,
    AS_VARCHAR($1:run_id),
    TRY_TO_TIMESTAMP_TZ(AS_VARCHAR($1:extracted_at)),
    AS_VARCHAR($1:source_dataset),
    AS_INTEGER($1:schema_version),
    AS_VARCHAR($1:payload_hash),
    TRY_TO_TIMESTAMP_TZ(AS_VARCHAR($1:source_created_at)),
    TRY_TO_TIMESTAMP_TZ(AS_VARCHAR($1:source_updated_at)),
    $1
  FROM @RAW.OBSERVATIONS_STAGE
)
FILE_FORMAT = (FORMAT_NAME = 'RAW.OBSERVATION_JSON')
PATTERN = '.*[.]ndjson[.]gz';

-- Bulk COPY (backfill, tests) lands here first. The pipe and bulk COPY keep
-- separate load histories, so a file the pipe loaded would load again; rows
-- move on to RAW only for files RAW does not hold yet.
CREATE TABLE IF NOT EXISTS RAW.BULK_LANDING LIKE RAW.RAW_311_OBSERVATIONS;

-- One collector run as the warehouse sees it: what its manifest promises,
-- what has loaded, and whether downstream processing has consumed it.
CREATE TABLE IF NOT EXISTS OPS.PIPELINE_RUNS (
  run_id                  STRING        NOT NULL PRIMARY KEY,
  extract_date            DATE          NOT NULL,
  manifest_file           STRING        NOT NULL,
  expected_files          NUMBER        NOT NULL,
  expected_rows           NUMBER        NOT NULL,
  loaded_files            NUMBER        NOT NULL,
  loaded_rows             NUMBER        NOT NULL,
  reconciled              BOOLEAN       NOT NULL,
  state                   STRING        NOT NULL,  -- waiting | consumed
  first_seen_at           TIMESTAMP_TZ  NOT NULL,
  checked_at              TIMESTAMP_TZ,
  consumed_at             TIMESTAMP_TZ,
  observations_processed  NUMBER
);

-- Work table for one readiness check: each waiting run's files, expected
-- against loaded rows.
CREATE TABLE IF NOT EXISTS OPS.WORK_RUN_FILES (
  run_id         STRING,
  file_key       STRING,
  expected_rows  NUMBER,
  loaded_rows    NUMBER
);

-- Runs already processed before this migration (by the manual pipeline).
INSERT INTO OPS.PIPELINE_RUNS (
  run_id, extract_date, manifest_file, expected_files, expected_rows, loaded_files, loaded_rows,
  reconciled, state, first_seen_at, checked_at, consumed_at
)
SELECT run_id, extract_date, source_file, file_count, row_count, file_count, row_count,
       TRUE, 'consumed', loaded_at, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()
FROM OPS.RUN_MANIFESTS;
