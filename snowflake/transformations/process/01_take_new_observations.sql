-- Consume the stream: every Observation loaded since the last run, with the
-- data-quality reasons that apply to it (empty array = accepted). JSON nulls
-- are stripped to SQL NULLs first, and values are only read as text when they
-- are text (AS_VARCHAR), so no Observation can make this statement fail.
-- Dates must look like the source's floating timestamps (2026-09-01T08:00:00.000);
-- REGEXP_LIKE matches the whole string.
DELETE FROM CLEAN.WORK_NEW_OBSERVATIONS;

INSERT INTO CLEAN.WORK_NEW_OBSERVATIONS
WITH fields AS (
  SELECT
    n.*,
    STRIP_NULL_VALUE(n.observation:payload)                   AS payload,
    STRIP_NULL_VALUE(n.observation:payload:unique_key)        AS key_value,
    STRIP_NULL_VALUE(n.observation:payload:created_date)      AS created_value,
    STRIP_NULL_VALUE(n.observation:payload:closed_date)       AS closed_value,
    STRIP_NULL_VALUE(n.observation:source_updated_at)         AS updated_value,
    STRIP_NULL_VALUE(n.observation:extracted_at)              AS extracted_value
  FROM RAW.NEW_OBSERVATIONS AS n
)
SELECT
  source_file,
  source_row_number,
  loaded_at,
  run_id,
  extracted_at,
  payload_hash,
  source_updated_at,
  AS_VARCHAR(key_value),
  ARRAY_CONSTRUCT_COMPACT(
    IFF(payload IS NOT NULL AND TYPEOF(payload) <> 'OBJECT', 'incompatible_payload', NULL),
    IFF(key_value IS NULL OR AS_VARCHAR(key_value) = '', 'missing_unique_key', NULL),
    IFF(key_value IS NOT NULL AND NOT IS_VARCHAR(key_value), 'incompatible_unique_key', NULL),
    IFF(created_value IS NULL, 'missing_created_date', NULL),
    IFF(created_value IS NOT NULL AND NOT IS_VARCHAR(created_value), 'incompatible_created_date', NULL),
    IFF(IS_VARCHAR(created_value)
        AND NOT REGEXP_LIKE(AS_VARCHAR(created_value), '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,3})?'), 'invalid_created_date', NULL),
    IFF(closed_value IS NOT NULL AND NOT IS_VARCHAR(closed_value), 'incompatible_closed_date', NULL),
    IFF(IS_VARCHAR(closed_value)
        AND NOT REGEXP_LIKE(AS_VARCHAR(closed_value), '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,3})?'), 'invalid_closed_date', NULL),
    IFF(source_updated_at IS NULL,
        IFF(updated_value IS NULL, 'missing_source_updated_at', 'invalid_source_updated_at'), NULL),
    IFF(extracted_at IS NULL,
        IFF(extracted_value IS NULL, 'missing_extracted_at', 'invalid_extracted_at'), NULL),
    IFF(payload_hash IS NULL, 'missing_payload_hash', NULL)
  ),
  observation
FROM fields;
