-- Bulk load Observation files into RAW. COPY's load history skips files
-- already loaded, so re-running is safe. TRY casts: a malformed value loads as
-- NULL (and is quarantined downstream) instead of failing the whole file.
COPY INTO RAW.RAW_311_OBSERVATIONS (
  source_file, source_row_number, loaded_at, run_id, extracted_at, source_dataset,
  schema_version, payload_hash, source_created_at, source_updated_at, observation
)
FROM (
  SELECT
    METADATA$FILENAME,
    METADATA$FILE_ROW_NUMBER,
    CURRENT_TIMESTAMP(),
    $1:run_id::STRING,
    TRY_TO_TIMESTAMP_TZ($1:extracted_at::STRING),
    $1:source_dataset::STRING,
    TRY_TO_NUMBER($1:schema_version::STRING),
    $1:payload_hash::STRING,
    TRY_TO_TIMESTAMP_TZ($1:source_created_at::STRING),
    TRY_TO_TIMESTAMP_TZ($1:source_updated_at::STRING),
    $1
  FROM {{observations_stage}}
)
FILE_FORMAT = (FORMAT_NAME = 'RAW.OBSERVATION_JSON')
PATTERN = '.*[.]ndjson[.]gz';
