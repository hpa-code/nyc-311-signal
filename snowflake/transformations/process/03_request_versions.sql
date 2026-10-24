-- Recompute the Request Versions of every Request with new accepted
-- Observations, from all of its distinct Observation points. A version is a
-- streak of consecutive points with the same payload hash, ordered by source
-- :updated_at, then extraction time, then payload hash. Never arrival time.
DELETE FROM CLEAN.WORK_AFFECTED_REQUESTS;
INSERT INTO CLEAN.WORK_AFFECTED_REQUESTS
SELECT DISTINCT unique_key FROM CLEAN.WORK_NEW_OBSERVATIONS WHERE ARRAY_SIZE(reasons) = 0;

-- New points only; a replayed or overlapping Observation is already there.
INSERT INTO CLEAN.OBSERVATION_POINTS
SELECT unique_key, payload_hash, source_updated_at, extracted_at, MIN(run_id), MIN(source_file)
FROM CLEAN.WORK_NEW_OBSERVATIONS AS n
WHERE ARRAY_SIZE(reasons) = 0
  AND NOT EXISTS (
    SELECT 1 FROM CLEAN.OBSERVATION_POINTS AS p
    WHERE p.unique_key = n.unique_key AND p.payload_hash = n.payload_hash
      AND p.source_updated_at = n.source_updated_at AND p.extracted_at = n.extracted_at
  )
GROUP BY 1, 2, 3, 4;

DELETE FROM CLEAN.WORK_NEW_VERSIONS;
INSERT INTO CLEAN.WORK_NEW_VERSIONS (
  unique_key, payload_hash, valid_from_updated_at, valid_from_extracted_at, last_seen_updated_at,
  last_seen_extracted_at, first_extracted_at, observation_count, payload, valid_from_run_id,
  valid_from_source_file, last_seen_run_id, last_seen_source_file
)
WITH payloads AS (
  -- One payload per (Request, hash): from this run's Observations or an existing version.
  SELECT unique_key, payload_hash, ANY_VALUE(payload) AS payload
  FROM (
    SELECT unique_key, payload_hash, observation:payload AS payload
    FROM CLEAN.WORK_NEW_OBSERVATIONS WHERE ARRAY_SIZE(reasons) = 0
    UNION ALL
    SELECT v.unique_key, v.payload_hash, v.payload
    FROM CLEAN.REQUEST_VERSIONS AS v JOIN CLEAN.WORK_AFFECTED_REQUESTS USING (unique_key)
  )
  GROUP BY 1, 2
),
ordered AS (
  SELECT
    p.*,
    LAG(p.payload_hash) OVER (
      PARTITION BY p.unique_key ORDER BY p.source_updated_at, p.extracted_at, p.payload_hash
    ) AS previous_hash
  FROM CLEAN.OBSERVATION_POINTS AS p JOIN CLEAN.WORK_AFFECTED_REQUESTS USING (unique_key)
),
streaks AS (
  SELECT
    o.*,
    SUM(IFF(previous_hash IS NULL OR previous_hash <> payload_hash, 1, 0)) OVER (
      PARTITION BY unique_key ORDER BY source_updated_at, extracted_at, payload_hash ROWS UNBOUNDED PRECEDING
    ) AS streak
  FROM ordered AS o
),
versions AS (
  SELECT
    unique_key,
    ANY_VALUE(payload_hash)                                                                   AS payload_hash,
    ARRAY_AGG(source_updated_at) WITHIN GROUP (ORDER BY source_updated_at, extracted_at)[0]   AS valid_from_updated_at,
    ARRAY_AGG(extracted_at) WITHIN GROUP (ORDER BY source_updated_at, extracted_at)[0]        AS valid_from_extracted_at,
    ARRAY_AGG(run_id) WITHIN GROUP (ORDER BY source_updated_at, extracted_at)[0]              AS valid_from_run_id,
    ARRAY_AGG(source_file) WITHIN GROUP (ORDER BY source_updated_at, extracted_at)[0]         AS valid_from_source_file,
    ARRAY_AGG(source_updated_at) WITHIN GROUP (ORDER BY source_updated_at DESC, extracted_at DESC)[0] AS last_seen_updated_at,
    ARRAY_AGG(extracted_at) WITHIN GROUP (ORDER BY source_updated_at DESC, extracted_at DESC)[0]      AS last_seen_extracted_at,
    ARRAY_AGG(run_id) WITHIN GROUP (ORDER BY source_updated_at DESC, extracted_at DESC)[0]            AS last_seen_run_id,
    ARRAY_AGG(source_file) WITHIN GROUP (ORDER BY source_updated_at DESC, extracted_at DESC)[0]       AS last_seen_source_file,
    MIN(extracted_at)                                                                         AS first_extracted_at,
    COUNT(*)                                                                                  AS observation_count
  FROM streaks
  GROUP BY unique_key, streak
)
SELECT
  v.unique_key, v.payload_hash, v.valid_from_updated_at::TIMESTAMP_TZ, v.valid_from_extracted_at::TIMESTAMP_TZ,
  v.last_seen_updated_at::TIMESTAMP_TZ, v.last_seen_extracted_at::TIMESTAMP_TZ, v.first_extracted_at,
  v.observation_count, p.payload, v.valid_from_run_id::STRING, v.valid_from_source_file::STRING,
  v.last_seen_run_id::STRING, v.last_seen_source_file::STRING
FROM versions AS v JOIN payloads AS p USING (unique_key, payload_hash);

DELETE FROM CLEAN.REQUEST_VERSIONS WHERE unique_key IN (SELECT unique_key FROM CLEAN.WORK_AFFECTED_REQUESTS);
INSERT INTO CLEAN.REQUEST_VERSIONS SELECT * FROM CLEAN.WORK_NEW_VERSIONS;
