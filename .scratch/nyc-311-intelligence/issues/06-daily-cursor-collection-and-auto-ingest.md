# 06: Daily cursor collection and auto-ingest

**What to build:** Collection runs daily without anyone touching it, capturing new Requests and changes to old ones through the `:updated_at` change cursor.

**Blocked by:** 05 (Request Versions, stale-overwrite guard and quarantine)

**Status:** ready-for-agent

**Mirror:** GitHub #7

- [ ] Lambda runs the collector in cursor mode; EventBridge Scheduler triggers it daily at 06:00 UTC with an explicit timezone
- [ ] Cursor = last stored `:updated_at` minus 6 hours; it advances only after files are durably written
- [ ] A lease prevents overlapping collector runs; failed invocations raise an alert
- [ ] Snowpipe auto-ingests only the data prefix; manifests load through a separate control path
- [ ] A readiness task starts downstream work only when the manifest reconciles with loaded files and counts, and tracks consumed runs
- [ ] Seam 1 tests for the cursor, the overlap and the lease
- [ ] Demo: a Request closed at the source appears closed in the Current Request after the next run
