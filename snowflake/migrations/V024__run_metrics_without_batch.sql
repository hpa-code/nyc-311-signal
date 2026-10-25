-- A run consumed with none of its Observations in the batch (they were
-- processed earlier through the bulk path) gave no Request Versions then;
-- R__readiness now counts only the batch's Requests. Correct earlier rows.
UPDATE OPS.RUN_METRICS SET updated_requests = 0 WHERE distinct_requests = 0;
