-- Observation files (gzip NDJSON, one Observation per line) and run manifests
-- arrive from S3 through two separate stages: data/ and manifests/.

CREATE FILE FORMAT IF NOT EXISTS RAW.OBSERVATION_JSON
  TYPE = JSON
  COMPRESSION = AUTO;

CREATE STAGE IF NOT EXISTS RAW.OBSERVATIONS_STAGE
  URL = 's3://{{bucket}}/data/'
  STORAGE_INTEGRATION = {{storage_integration}}
  FILE_FORMAT = RAW.OBSERVATION_JSON;

-- One row per loaded Observation. `observation` keeps the whole line as the
-- collector wrote it, so nothing from the source is lost.
CREATE TABLE IF NOT EXISTS RAW.RAW_311_OBSERVATIONS (
  source_file        STRING        NOT NULL,
  source_row_number  NUMBER        NOT NULL,
  loaded_at          TIMESTAMP_TZ  NOT NULL,
  run_id             STRING,
  extracted_at       TIMESTAMP_TZ,
  source_dataset     STRING,
  schema_version     NUMBER,
  payload_hash       STRING,
  source_created_at  TIMESTAMP_TZ,
  source_updated_at  TIMESTAMP_TZ,
  observation        VARIANT       NOT NULL
);

CREATE FILE FORMAT IF NOT EXISTS OPS.MANIFEST_JSON
  TYPE = JSON;

CREATE STAGE IF NOT EXISTS OPS.MANIFESTS_STAGE
  URL = 's3://{{bucket}}/manifests/'
  STORAGE_INTEGRATION = {{storage_integration}}
  FILE_FORMAT = OPS.MANIFEST_JSON;

-- Control path: one row per run manifest.
CREATE TABLE IF NOT EXISTS OPS.RUN_MANIFESTS (
  source_file   STRING        NOT NULL,
  loaded_at     TIMESTAMP_TZ  NOT NULL,
  run_id        STRING        NOT NULL,
  extract_date  DATE          NOT NULL,
  file_count    NUMBER        NOT NULL,
  row_count     NUMBER        NOT NULL,
  manifest      VARIANT       NOT NULL
);
