# 13: Weekly reconciliation, monitoring and Pipeline Health page

**What to build:** The operator learns quickly when anything breaks or goes stale, including the change cursor silently missing updates.

**Blocked by:** 07 (Publication gate)

**Status:** ready-for-agent

**Mirror:** GitHub #14

- [ ] Weekly reconciliation sweeps older creation-date partitions and alerts if it finds changes the cursor missed
- [ ] CloudWatch alarms: no complete Publication for 36h, failed collector runs, application health
- [ ] Tracked metrics: extracted, loaded, distinct and updated Requests, quarantined rows, runtime, scoring coverage, unknown-Problem rate; source freshness tracked separately from job success
- [ ] Pipeline Health page shows the last successful run and Publication status; detailed logs stay restricted

## Comments

**2026-10-24 (agent, from 05):**
- The stream `RAW.NEW_OBSERVATIONS` goes stale if it is not consumed within the table's retention plus `MAX_DATA_EXTENSION_TIME_IN_DAYS` (14 days by default). Alert when processing has not run for several days, and document the recovery: recreate the stream, then reprocess from RAW or replay from S3.
- `APP.QUARANTINE_SUMMARY` (counts by reason) already exists for the Pipeline Health page.

**2026-10-24 (agent, from 07):**
- A held Publication fails `OPS.PUBLISH_TASK` (or `PUBLISH_AFTER_SWEEP_TASK`) with error `-20002`. Alert on that task failure, like the readiness `-20001`.
- "No complete Publication for 36h": `APP.PUBLICATION_STATUS` already flags data more than 36 hours old. The alarm can read `OPS.PUBLICATIONS` (latest `published` finished_at).
- The Pipeline Health page can show `APP.PUBLICATION_STATUS` directly. Held-check detail is in `OPS.PUBLICATIONS.checks` (restricted).
