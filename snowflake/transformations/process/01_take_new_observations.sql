-- Consume the stream: every Observation loaded since the last run, with the
-- data-quality reasons that apply to it (empty array = accepted).
-- A JSON null counts as missing.
DELETE FROM CLEAN.WORK_NEW_OBSERVATIONS;

INSERT INTO CLEAN.WORK_NEW_OBSERVATIONS
SELECT
  n.source_file,
  n.source_row_number,
  n.loaded_at,
  n.run_id,
  n.extracted_at,
  n.payload_hash,
  n.source_updated_at,
  IFF(IS_VARCHAR(n.observation:payload:unique_key), n.observation:payload:unique_key::STRING, NULL),
  ARRAY_CONSTRUCT_COMPACT(
    IFF(TYPEOF(n.observation:payload) <> 'OBJECT', 'incompatible_payload', NULL),
    IFF(n.observation:payload:unique_key IS NULL OR IS_NULL_VALUE(n.observation:payload:unique_key)
        OR n.observation:payload:unique_key::STRING = '', 'missing_unique_key', NULL),
    IFF(n.observation:payload:unique_key IS NOT NULL AND NOT IS_NULL_VALUE(n.observation:payload:unique_key)
        AND NOT IS_VARCHAR(n.observation:payload:unique_key), 'incompatible_unique_key', NULL),
    IFF(n.observation:payload:created_date IS NULL OR IS_NULL_VALUE(n.observation:payload:created_date),
        'missing_created_date', NULL),
    IFF(NOT (n.observation:payload:created_date IS NULL OR IS_NULL_VALUE(n.observation:payload:created_date))
        AND TRY_TO_TIMESTAMP_NTZ(n.observation:payload:created_date::STRING) IS NULL, 'invalid_created_date', NULL),
    IFF(NOT (n.observation:payload:closed_date IS NULL OR IS_NULL_VALUE(n.observation:payload:closed_date))
        AND TRY_TO_TIMESTAMP_NTZ(n.observation:payload:closed_date::STRING) IS NULL, 'invalid_closed_date', NULL),
    IFF(n.source_updated_at IS NULL,
        IFF(n.observation:source_updated_at IS NULL OR IS_NULL_VALUE(n.observation:source_updated_at),
            'missing_source_updated_at', 'invalid_source_updated_at'), NULL),
    IFF(n.extracted_at IS NULL,
        IFF(n.observation:extracted_at IS NULL OR IS_NULL_VALUE(n.observation:extracted_at),
            'missing_extracted_at', 'invalid_extracted_at'), NULL),
    IFF(n.payload_hash IS NULL, 'missing_payload_hash', NULL)
  ),
  n.observation
FROM RAW.NEW_OBSERVATIONS AS n;
