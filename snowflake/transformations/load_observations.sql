-- Bulk load Observation files into RAW. COPY's load history skips files
-- already loaded, so re-running is safe.
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
    $1:extracted_at::TIMESTAMP_TZ,
    $1:source_dataset::STRING,
    $1:schema_version::NUMBER,
    $1:payload_hash::STRING,
    $1:source_created_at::TIMESTAMP_TZ,
    $1:source_updated_at::TIMESTAMP_TZ,
    $1
  FROM {{observations_stage}}
)
FILE_FORMAT = (FORMAT_NAME = 'RAW.OBSERVATION_JSON')
PATTERN = '.*[.]ndjson[.]gz';
