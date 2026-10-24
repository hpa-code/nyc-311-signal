-- Current Request = the latest Request Version, with the provenance of its
-- last Observation point. The guard means an older version can never replace
-- a newer Current Request, whatever arrives when.
MERGE INTO CLEAN.SERVICE_REQUESTS AS t
USING (
  SELECT
    v.unique_key,
    TRY_TO_TIMESTAMP_NTZ(v.payload:created_date::STRING)                    AS created_at_local,
    CONVERT_TIMEZONE('America/New_York', 'UTC', created_at_local)           AS created_at_utc,
    created_at_local::DATE                                                  AS created_date_local,
    TRY_TO_TIMESTAMP_NTZ(v.payload:closed_date::STRING)                     AS closed_at_local,
    v.payload:status::STRING                                                AS status,
    v.payload:agency::STRING                                                AS agency,
    v.payload:complaint_type::STRING                                        AS problem,
    v.payload:descriptor::STRING                                            AS problem_detail,
    v.payload:borough::STRING                                               AS borough,
    v.payload:open_data_channel_type::STRING                                AS open_data_channel_type,
    v.payload:location_type::STRING                                         AS location_type,
    CLEAN.NYC_H3_CELL(v.payload)                                            AS h3_cell,
    v.last_seen_updated_at                                                  AS source_updated_at,
    v.last_seen_extracted_at                                                AS extracted_at,
    v.payload_hash,
    v.last_seen_run_id                                                      AS run_id,
    v.last_seen_source_file                                                 AS source_file,
    MIN(v.first_extracted_at) OVER (PARTITION BY v.unique_key)              AS first_observed_at
  FROM CLEAN.REQUEST_VERSIONS AS v
  WHERE v.unique_key IN (SELECT unique_key FROM CLEAN.WORK_AFFECTED_REQUESTS)
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY v.unique_key
    ORDER BY v.last_seen_updated_at DESC, v.last_seen_extracted_at DESC, v.payload_hash DESC
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
  h3_cell = s.h3_cell,
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
  problem, problem_detail, borough, open_data_channel_type, location_type, h3_cell, source_updated_at,
  extracted_at, payload_hash, run_id, source_file, first_observed_at, merged_at
) VALUES (
  s.unique_key, s.created_at_local, s.created_at_utc, s.created_date_local, s.closed_at_local, s.status, s.agency,
  s.problem, s.problem_detail, s.borough, s.open_data_channel_type, s.location_type, s.h3_cell, s.source_updated_at,
  s.extracted_at, s.payload_hash, s.run_id, s.source_file, s.first_observed_at, CURRENT_TIMESTAMP()
);
