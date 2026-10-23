# 13: Weekly reconciliation, monitoring and Pipeline Health page

**What to build:** The operator learns quickly when anything breaks or goes stale, including the change cursor silently missing updates.

**Blocked by:** 07 (Publication gate)

**Status:** ready-for-agent

**Mirror:** GitHub #14

- [ ] Weekly reconciliation sweeps older creation-date partitions and alerts if it finds changes the cursor missed
- [ ] CloudWatch alarms: no complete Publication for 36h, failed collector runs, application health
- [ ] Tracked metrics: extracted, loaded, distinct and updated Requests, quarantined rows, runtime, scoring coverage, unknown-Problem rate; source freshness tracked separately from job success
- [ ] Pipeline Health page shows the last successful run and Publication status; detailed logs stay restricted
