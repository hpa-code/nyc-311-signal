-- First dashboard views. The dashboard role reads APP only; these views run
-- with their owner's rights, so it never needs access to CLEAN, RAW or OPS.

CREATE OR REPLACE VIEW APP.OVERVIEW_DAILY COPY GRANTS AS
SELECT
  created_date_local                     AS created_date,
  COUNT(*)                               AS requests,
  COUNT_IF(status = 'Closed')            AS closed_requests,
  COUNT_IF(status IS DISTINCT FROM 'Closed') AS open_requests
FROM CLEAN.SERVICE_REQUESTS
GROUP BY created_date_local;

CREATE OR REPLACE VIEW APP.DATA_COVERAGE COPY GRANTS AS
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
manifests AS (
  SELECT
    COUNT(*)                     AS runs,
    COALESCE(SUM(file_count), 0) AS manifest_files,
    COALESCE(SUM(row_count), 0)  AS manifest_rows
  FROM OPS.RUN_MANIFESTS
)
SELECT * FROM raw, current_requests, manifests;

GRANT USAGE ON DATABASE {{database}} TO ROLE {{dashboard_role}};
GRANT USAGE ON SCHEMA APP TO ROLE {{dashboard_role}};
GRANT SELECT ON ALL VIEWS IN SCHEMA APP TO ROLE {{dashboard_role}};
