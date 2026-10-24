INSERT INTO CLEAN.QUARANTINED_RECORDS
  (source_file, source_row_number, loaded_at, run_id, unique_key, reasons, observation, quarantined_at)
SELECT source_file, source_row_number, loaded_at, run_id, unique_key, reasons, observation, CURRENT_TIMESTAMP()
FROM CLEAN.WORK_NEW_OBSERVATIONS
WHERE ARRAY_SIZE(reasons) > 0;
