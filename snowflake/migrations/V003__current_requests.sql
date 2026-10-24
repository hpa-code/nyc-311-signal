-- One Current Request per unique_key: the latest observed state.
-- Source dates are NYC local floating timestamps, kept as *_local (NTZ);
-- *_utc columns are the same instants converted from America/New_York.
CREATE TABLE IF NOT EXISTS CLEAN.SERVICE_REQUESTS (
  unique_key              STRING        NOT NULL PRIMARY KEY,
  created_at_local        TIMESTAMP_NTZ,
  created_at_utc          TIMESTAMP_NTZ,
  created_date_local      DATE,
  closed_at_local         TIMESTAMP_NTZ,
  status                  STRING,
  agency                  STRING,
  problem                 STRING,
  problem_detail          STRING,
  borough                 STRING,
  open_data_channel_type  STRING,
  location_type           STRING,
  source_updated_at       TIMESTAMP_TZ,
  extracted_at            TIMESTAMP_TZ,
  payload_hash            STRING,
  run_id                  STRING,
  source_file             STRING,
  first_observed_at       TIMESTAMP_TZ,
  merged_at               TIMESTAMP_TZ
);
