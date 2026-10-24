-- Rebuild the Request Versions of every Request with new accepted
-- Observations, from its existing versions (first and last point of each)
-- plus the new Observations. Ordering: source :updated_at, then extraction
-- time, then payload hash. Never arrival time.
DELETE FROM CLEAN.WORK_VERSION_CANDIDATES;

INSERT INTO CLEAN.WORK_VERSION_CANDIDATES
WITH affected AS (
  SELECT DISTINCT unique_key FROM CLEAN.WORK_NEW_OBSERVATIONS WHERE ARRAY_SIZE(reasons) = 0
)
SELECT unique_key, payload_hash, source_updated_at, extracted_at, extracted_at, 1,
       observation:payload, run_id, source_file
FROM CLEAN.WORK_NEW_OBSERVATIONS
WHERE ARRAY_SIZE(reasons) = 0
UNION ALL
SELECT v.unique_key, v.payload_hash, v.valid_from_updated_at, v.valid_from_extracted_at, v.first_extracted_at,
       v.observations, v.payload, v.run_id, v.source_file
FROM CLEAN.REQUEST_VERSIONS AS v JOIN affected USING (unique_key)
UNION ALL
SELECT v.unique_key, v.payload_hash, v.last_seen_updated_at, v.last_seen_extracted_at, v.first_extracted_at,
       0, v.payload, v.run_id, v.source_file
FROM CLEAN.REQUEST_VERSIONS AS v JOIN affected USING (unique_key);

DELETE FROM CLEAN.REQUEST_VERSIONS
WHERE unique_key IN (SELECT unique_key FROM CLEAN.WORK_VERSION_CANDIDATES);

INSERT INTO CLEAN.REQUEST_VERSIONS
WITH ordered AS (
  SELECT
    c.*,
    LAG(payload_hash) OVER (PARTITION BY unique_key ORDER BY updated_at, extracted_at, payload_hash) AS previous_hash
  FROM CLEAN.WORK_VERSION_CANDIDATES AS c
),
runs AS (
  SELECT
    o.*,
    SUM(IFF(previous_hash IS NULL OR previous_hash <> payload_hash, 1, 0)) OVER (
      PARTITION BY unique_key ORDER BY updated_at, extracted_at, payload_hash ROWS UNBOUNDED PRECEDING
    ) AS run_number
  FROM ordered AS o
),
bounded AS (
  SELECT
    r.*,
    FIRST_VALUE(updated_at)   OVER (PARTITION BY unique_key, run_number ORDER BY updated_at, extracted_at, payload_hash ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS first_updated_at,
    FIRST_VALUE(extracted_at) OVER (PARTITION BY unique_key, run_number ORDER BY updated_at, extracted_at, payload_hash ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS first_point_extracted_at,
    LAST_VALUE(updated_at)    OVER (PARTITION BY unique_key, run_number ORDER BY updated_at, extracted_at, payload_hash ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS last_updated_at,
    LAST_VALUE(extracted_at)  OVER (PARTITION BY unique_key, run_number ORDER BY updated_at, extracted_at, payload_hash ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS last_point_extracted_at,
    FIRST_VALUE(payload)      OVER (PARTITION BY unique_key, run_number ORDER BY updated_at, extracted_at, payload_hash ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS first_payload,
    FIRST_VALUE(run_id)       OVER (PARTITION BY unique_key, run_number ORDER BY updated_at, extracted_at, payload_hash ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS first_run_id,
    FIRST_VALUE(source_file)  OVER (PARTITION BY unique_key, run_number ORDER BY updated_at, extracted_at, payload_hash ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS first_source_file
  FROM runs AS r
)
SELECT
  unique_key,
  ANY_VALUE(payload_hash),
  ANY_VALUE(first_updated_at),
  ANY_VALUE(first_point_extracted_at),
  ANY_VALUE(last_updated_at),
  ANY_VALUE(last_point_extracted_at),
  MIN(first_extracted_at),
  SUM(weight),
  ANY_VALUE(first_payload),
  ANY_VALUE(first_run_id),
  ANY_VALUE(first_source_file)
FROM bounded
GROUP BY unique_key, run_number;
