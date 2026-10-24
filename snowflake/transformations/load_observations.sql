-- Bulk load Observation files (backfill, tests) into RAW, coordinated with
-- Snowpipe. COPY's load history, kept for the landing table, skips files
-- already landed; a file Snowpipe loaded (its history is separate) lands
-- again but goes no further, because RAW already holds it. TRY casts: a
-- malformed value loads as NULL (and is quarantined downstream) instead of
-- failing the whole file; AS_* read a value only when it has the expected type.
COPY INTO RAW.BULK_LANDING (
  source_file, source_row_number, loaded_at, run_id, extracted_at, source_dataset,
  schema_version, payload_hash, source_created_at, source_updated_at, observation
)
FROM (
  SELECT
    METADATA$FILENAME,
    METADATA$FILE_ROW_NUMBER,
    CURRENT_TIMESTAMP(),
    AS_VARCHAR($1:run_id),
    TRY_TO_TIMESTAMP_TZ(AS_VARCHAR($1:extracted_at)),
    AS_VARCHAR($1:source_dataset),
    AS_INTEGER($1:schema_version),
    AS_VARCHAR($1:payload_hash),
    TRY_TO_TIMESTAMP_TZ(AS_VARCHAR($1:source_created_at)),
    TRY_TO_TIMESTAMP_TZ(AS_VARCHAR($1:source_updated_at)),
    $1
  FROM {{observations_stage}}
)
FILE_FORMAT = (FORMAT_NAME = 'RAW.OBSERVATION_JSON')
PATTERN = '.*[.]ndjson[.]gz';

INSERT INTO RAW.RAW_311_OBSERVATIONS
SELECT * FROM RAW.BULK_LANDING AS l
WHERE NOT EXISTS (SELECT 1 FROM RAW.RAW_311_OBSERVATIONS AS r WHERE r.source_file = l.source_file);

-- DELETE, not TRUNCATE: TRUNCATE would also clear the landing load history.
DELETE FROM RAW.BULK_LANDING;
