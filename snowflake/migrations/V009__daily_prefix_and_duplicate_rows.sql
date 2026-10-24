-- One prefix per load path: Snowpipe reads only data/daily/ (cursor passes),
-- bulk COPY owns data/bulk/ (slices, backfill), so a backfill file can never
-- reach RAW through both. The S3 notification is narrowed to data/daily/ too.
CREATE OR REPLACE PIPE RAW.OBSERVATIONS_PIPE
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
  FROM @RAW.OBSERVATIONS_STAGE/daily/
)
FILE_FORMAT = (FORMAT_NAME = 'RAW.OBSERVATION_JSON')
PATTERN = '.*[.]ndjson[.]gz';

-- Rows of a run's files that RAW holds more than once. Reconciliation still
-- counts each row once (downstream deduplicates), but a double load is
-- recorded instead of hidden.
ALTER TABLE OPS.PIPELINE_RUNS ADD COLUMN duplicate_rows NUMBER;
ALTER TABLE OPS.WORK_RUN_FILES ADD COLUMN raw_rows NUMBER;
