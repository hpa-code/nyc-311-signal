-- Control path: copy run manifests into OPS.RUN_MANIFESTS.
-- COPY's load history skips files already loaded, so re-running is safe.
COPY INTO OPS.RUN_MANIFESTS (source_file, loaded_at, run_id, extract_date, file_count, row_count, manifest)
FROM (
  SELECT
    METADATA$FILENAME,
    CURRENT_TIMESTAMP(),
    $1:run_id::STRING,
    $1:extract_date::DATE,
    $1:file_count::NUMBER,
    $1:row_count::NUMBER,
    $1
  FROM {{manifests_stage}}
)
FILE_FORMAT = (FORMAT_NAME = 'OPS.MANIFEST_JSON')
PATTERN = '.*manifest[.]json';
