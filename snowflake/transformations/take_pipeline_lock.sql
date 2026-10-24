-- First statement of every processing and publishing transaction. The update
-- holds OPS.PIPELINE_LOCK until COMMIT or ROLLBACK; a concurrent transaction
-- doing the same waits, so the two never interleave.
UPDATE OPS.PIPELINE_LOCK SET held_at = CURRENT_TIMESTAMP();
