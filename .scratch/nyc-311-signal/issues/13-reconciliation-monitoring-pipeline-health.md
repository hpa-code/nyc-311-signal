# 13: Weekly reconciliation, monitoring and Pipeline Health page

**What to build:** The operator learns quickly when anything breaks or goes stale, including the change cursor silently missing updates.

**Blocked by:** 07 (Publication gate)

**Status:** done

**Mirror:** GitHub #14

- [x] Weekly reconciliation sweeps older creation-date partitions and alerts if it finds changes the cursor missed
- [x] CloudWatch alarms: no complete Publication for 36h, failed collector runs, application health (moved to ticket 14, which deploys the app; written in modules/dashboard)
- [x] Tracked metrics: extracted, loaded, distinct and updated Requests, quarantined rows, runtime, scoring coverage, unknown-Problem rate; source freshness tracked separately from job success
- [x] Pipeline Health page shows the last successful run and Publication status; detailed logs stay restricted

## Comments

**2026-10-24 (agent, from 05):**
- The stream `RAW.NEW_OBSERVATIONS` goes stale if it is not consumed within the table's retention plus `MAX_DATA_EXTENSION_TIME_IN_DAYS` (14 days by default). Alert when processing has not run for several days, and document the recovery: recreate the stream, then reprocess from RAW or replay from S3.
- `APP.QUARANTINE_SUMMARY` (counts by reason) already exists for the Pipeline Health page.

**2026-10-24 (agent, from 07):**
- A held Publication fails `OPS.PUBLISH_TASK` (or `PUBLISH_AFTER_SWEEP_TASK`) with error `-20002`. Alert on that task failure, like the readiness `-20001`.
- "No complete Publication for 36h": `APP.PUBLICATION_STATUS` already flags data more than 36 hours old. The alarm can read `OPS.PUBLICATIONS` (latest `published` finished_at).
- The Pipeline Health page can show `APP.PUBLICATION_STATUS` directly. Held-check detail is in `OPS.PUBLICATIONS.checks` (restricted).

**2026-10-25 (agent): built.** Decided with the owner: CloudWatch alarms fed by a monitor Lambda, a sweep of 8 weeks plus a rotating month, and application health deferred to ticket 14 (noted there).
- **Reconciliation Pass** (`ingestion/reconcile.py`, new GLOSSARY term). Each ISO week re-fetches the 56 creation days before its Monday plus one older month in turn.
  - Run ID `reconcile-<year>W<week>`. The files go to `data/daily/`, so Snowpipe loads them, the readiness check processes them, and any change is taken in.
  - The `dev-nyc311-collector-reconcile` schedule invokes the collector Lambda daily at 08:00 UTC with `{"plan": "reconcile"}`. It starts the week's pass on Sundays, and resumes a pass left unfinished from this week or the last on any day. An unfinished pass must not wait a week: its loaded files hold up all processing until its manifest exists.
  - Manifests now record `started_at`.
- **Missed changes and run metrics.** When `OPS.PROCESS_READY_RUNS` processes a run, it records in `OPS.RUN_METRICS`: distinct Requests, Requests given a new Request Version (`updated_requests`), quarantined rows, the newest source `:updated_at`, and the extraction time.
  - For a Reconciliation Pass, `missed_changes` counts the Requests whose new version has a source `:updated_at` no later than the newest one any cursor run extracted before the pass.
  - V021 backfilled metrics for runs consumed earlier.
- **Views:**
  - `APP.RUN_HEALTH`: every run, plus collector runtime from the manifest and source lag.
  - `APP.MONITOR`: one row of signals.
  - `APP.PUBLICATION_HISTORY`: state and timing, without checks or errors.
  - `APP.PUBLISHED_QUARANTINE_SUMMARY`.
  - All are readable by the dashboard role. None shows error text.
- **Monitor** (`monitoring/lambda_handler.py`, a container image, because it needs the Snowflake connector; `modules/monitor`). Hourly, as the read-only dashboard role, it publishes 10 metrics under `NYC311/<env>`: only the alarmed signals plus the unknown-Problem rate, within CloudWatch's free tier.
  - Alarms go to the alerts topic: no Publication for 36 h (missing data counts as breaching), attempt held or failed (`-20002` included), readiness failing, nothing processed for 72 h (stream staleness), runs stuck over 6 h, source lag over 48 h, missed changes, no reconciliation for 8 days, scoring coverage below 1, and the monitor itself failing.
  - The collector's own failed and missed alarms already existed.
- **Pipeline Health page** (`dashboard/pages/health.py`): live state, not tied to a Publication, so failures show while an earlier Publication stays up. It shows Publication status, processing, cursor run and source freshness, stuck runs, the Reconciliation Pass, recent runs with their metrics, Publication attempts and quarantine reasons.
- **Runbook** (`docs/runbook.md`): what each alarm means, how to read missed changes, and stale-stream recovery.
- **Review fixes:**
  - The daily resume of unfinished passes.
  - V022: processing age counts from the last consumed run, not the last successful check (which idle checks also count). Source lag is measured against the newest change any cursor run has seen. Reconciliation age falls back to when metrics began. The held/failed flag moved into SQL.
  - Vocabulary on the page.
  - Named alarm fields.
- **Dev:** V020–V022 applied. The signals: Publication 0.4 h old, source lag 4.7 h, scoring coverage 1.0, unknown-Problem rate 0.
- **Tests:**
  - Seam 1 (`ingestion/tests/test_reconcile.py`): partitions and rotation, the same run all week, nothing started off-Sunday, an unfinished pass resumed the next day, and `started_at`.
  - Seam 2 (`warehouse/tests/test_monitoring.py`): two of four Requests missed (one changed without its `:updated_at` moving, one never collected), one changed after the cursor pass not counted, one unchanged; plus the per-run metrics and source lag.
  - Environment selection: `warehouse.environment.current()` reads `NYC311_ENV` (dev or prod, dev by default; PROD names the `NYC311` database and `prod-` objects). The monitor, the dashboard and every `python -m` entry point use it, so the monitor in prod reads prod. Migrate rewrites the CHECKSUMS ledger only for dev.
- **Left as is:**
  - The month rotation shifts as months are added, so a month can wait longer than the count of months.
  - Processing runtime isn't measured. Publication duration is on the page.
  - The concurrent cursor-and-pass case is untested; the watermark only counts cursor rows extracted before the pass.
