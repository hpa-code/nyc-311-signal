# 06: Daily cursor collection and auto-ingest

**What to build:** Collection runs daily without anyone touching it, capturing new Requests and changes to old ones through the `:updated_at` change cursor.

**Blocked by:** 05 (Request Versions, stale-overwrite guard and quarantine)

**Status:** ready-for-agent

**Mirror:** GitHub #7

- [x] Lambda runs the collector in cursor mode; EventBridge Scheduler triggers it daily at 06:00 UTC with an explicit timezone
- [x] Cursor = last stored `:updated_at` minus 6 hours; it advances only after files are durably written
- [x] A lease prevents overlapping collector runs; failed invocations raise an alert
- [x] Snowpipe auto-ingests only the data prefix; manifests load through a separate control path
- [x] A readiness task starts downstream work only when the manifest reconciles with loaded files and counts, and tracks consumed runs
- [x] Seam 1 tests for the cursor, the overlap and the lease
- [ ] Demo: a Request closed at the source appears closed in the Current Request after the next run

## Comments

**2026-10-23 (agent, from 04):**
- Bulk COPY (`snowflake/transformations/load_observations.sql`) reads the whole `data/` prefix, and only COPY load history (64 days) stops reloads. Before Snowpipe reads the same prefix, add coordinated tracking per story 38: separate backfill and incremental prefixes, or skip files already in `RAW.RAW_311_OBSERVATIONS.source_file`.
- The ingestion role's policy (If-None-Match puts) has not yet run as a real workload; the first Lambda run tests it.

**2026-10-24 (agent): built and deployed to dev; demo pending.**
- Collector `CursorPlan`: rows with `:updated_at` > cursor − 6h, created from 2025-01-01, keyset `(:updated_at, unique_key)`. Cursor in `checkpoints/cursor/311.json`, advanced only after the manifest is stored. An unfinished pass is resumed (same run ID, bounds and files) before a new one starts.
- Lease `checkpoints/leases/<name>.json`: conditional S3 writes (If-None-Match / If-Match), 16-minute TTL, renewed every page, released on exit.
- Lambda `dev-nyc311-collector` (`ingestion/lambda_handler.py`) pauses 150s before its timeout and invokes itself to continue (the 2026-10-23 batch was 538k rows, about 11s per 10k page). EventBridge Scheduler runs `cron(0 6 * * ? *)` UTC. Alarms (on any Errors, and on no invocation for 24h) go to SNS `dev-nyc311-alerts`.
- V008: `RAW.OBSERVATIONS_PIPE` (auto-ingests `data/`, S3 notifications on the `data/` prefix only). Bulk COPY now lands in `RAW.BULK_LANDING` and skips files RAW already holds (story 38). `OPS.PIPELINE_RUNS` tracks runs.
- `R__readiness.sql` (repeatable migration, includes the process step files): `OPS.PROCESS_READY_RUNS()` loads manifests by COPY and reconciles each run file by file. It processes only if every pending stream file belongs to a reconciled run, and marks runs consumed in the same transaction. `OPS.READINESS_TASK` runs every 30 minutes, only while `RAW.NEW_OBSERVATIONS` has data.
- Seam 1: 42 pass (16 new). Seam 2: 15 pass (6 new). A gate mutation failed 2 of them.
- Smoke test (2026-10-24 02:40 UTC): a manual invoke completed with 0 rows, because the source had not published its 2026-10-24 batch yet. The cursor stayed at the seed (2026-10-23T20:16:00.000Z).
- Remaining: the demo after the 06:00 UTC scheduled run. Snapshot table: `OPS.DEMO_06_OPEN_BEFORE` (4,706 Requests not Closed).

