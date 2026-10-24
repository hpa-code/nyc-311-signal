-- Latest observed state wins: ordered by source :updated_at, then extraction
-- time, then payload hash as a deterministic tie-breaker. Never by load time.
-- Observations without :updated_at cannot be ordered and are skipped (the
-- collector refuses them; ticket 05 quarantines them).
MERGE INTO CLEAN.SERVICE_REQUESTS AS t
USING (
  SELECT
    o.observation:payload:unique_key::STRING                                AS unique_key,
    TRY_TO_TIMESTAMP_NTZ(o.observation:payload:created_date::STRING)        AS created_at_local,
    CONVERT_TIMEZONE('America/New_York', 'UTC', created_at_local)           AS created_at_utc,
    created_at_local::DATE                                                  AS created_date_local,
    TRY_TO_TIMESTAMP_NTZ(o.observation:payload:closed_date::STRING)         AS closed_at_local,
    o.observation:payload:status::STRING                                    AS status,
    o.observation:payload:agency::STRING                                    AS agency,
    o.observation:payload:complaint_type::STRING                            AS problem,
    o.observation:payload:descriptor::STRING                                AS problem_detail,
    o.observation:payload:borough::STRING                                   AS borough,
    o.observation:payload:open_data_channel_type::STRING                    AS open_data_channel_type,
    o.observation:payload:location_type::STRING                             AS location_type,
    o.source_updated_at,
    o.extracted_at,
    o.payload_hash,
    o.run_id,
    o.source_file,
    MIN(o.extracted_at) OVER (PARTITION BY unique_key)                      AS first_observed_at
  FROM RAW.RAW_311_OBSERVATIONS AS o
  WHERE o.observation:payload:unique_key IS NOT NULL
    AND o.source_updated_at IS NOT NULL
    AND o.extracted_at IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY unique_key
    ORDER BY o.source_updated_at DESC, o.extracted_at DESC, o.payload_hash DESC
  ) = 1
) AS s
ON t.unique_key = s.unique_key
WHEN MATCHED AND (
     s.source_updated_at > t.source_updated_at
  OR (s.source_updated_at = t.source_updated_at AND s.extracted_at > t.extracted_at)
  OR (s.source_updated_at = t.source_updated_at AND s.extracted_at = t.extracted_at AND s.payload_hash > t.payload_hash)
) THEN UPDATE SET
  created_at_local = s.created_at_local,
  created_at_utc = s.created_at_utc,
  created_date_local = s.created_date_local,
  closed_at_local = s.closed_at_local,
  status = s.status,
  agency = s.agency,
  problem = s.problem,
  problem_detail = s.problem_detail,
  borough = s.borough,
  open_data_channel_type = s.open_data_channel_type,
  location_type = s.location_type,
  source_updated_at = s.source_updated_at,
  extracted_at = s.extracted_at,
  payload_hash = s.payload_hash,
  run_id = s.run_id,
  source_file = s.source_file,
  first_observed_at = LEAST(t.first_observed_at, s.first_observed_at),
  merged_at = CURRENT_TIMESTAMP()
WHEN MATCHED AND s.first_observed_at < t.first_observed_at THEN UPDATE SET
  first_observed_at = s.first_observed_at,
  merged_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
  unique_key, created_at_local, created_at_utc, created_date_local, closed_at_local, status, agency,
  problem, problem_detail, borough, open_data_channel_type, location_type, source_updated_at,
  extracted_at, payload_hash, run_id, source_file, first_observed_at, merged_at
) VALUES (
  s.unique_key, s.created_at_local, s.created_at_utc, s.created_date_local, s.closed_at_local, s.status, s.agency,
  s.problem, s.problem_detail, s.borough, s.open_data_channel_type, s.location_type, s.source_updated_at,
  s.extracted_at, s.payload_hash, s.run_id, s.source_file, s.first_observed_at, CURRENT_TIMESTAMP()
);
