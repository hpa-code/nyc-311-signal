# Runbook

What each alert means and what to do. Alerts reach the `dev-nyc311-alerts` SNS topic. The Pipeline Health page shows the same signals live; failure detail is in the restricted `OPS` schema.

## Alarms

| Alarm | Fires when | First look |
|---|---|---|
| `dev-nyc311-collector-failed` | A collector invocation failed (cursor or reconciliation pass) | Lambda logs `/aws/lambda/dev-nyc311-collector`. The pass resumes from its checkpoint on the next run. |
| `dev-nyc311-collector-missed` | No collector invocation for 24 h | EventBridge Scheduler `dev-nyc311-collector-daily`. |
| `dev-nyc311-monitor-publication-stale` | No complete Publication for 36 h, or the monitor reports nothing | `OPS.PUBLICATIONS`, then the alarms below. The dashboard shows a stale-data warning meanwhile. |
| `dev-nyc311-monitor-publication-held` | The latest Publication attempt was held or failed | `SELECT checks, error FROM OPS.PUBLICATIONS ORDER BY started_at DESC LIMIT 1`. Fix the data or the procedure; the next `OPS.PUBLISH()` rebuilds. |
| `dev-nyc311-monitor-processing-failing` | The readiness check failed since it last succeeded | `SELECT * FROM OPS.STEP_STATUS`, `OPS.PIPELINE_RUNS WHERE state = 'waiting'`. |
| `dev-nyc311-monitor-stuck-runs` | A run has waited over 6 h to reconcile with its manifest | Compare the manifest's files with `RAW.RAW_311_OBSERVATIONS` (`OPS.WORK_RUN_FILES`); check Snowpipe (`SYSTEM$PIPE_STATUS('RAW.OBSERVATIONS_PIPE')`). |
| `dev-nyc311-monitor-processing-stalled` | No collector run processed for 3 days | See *Stale stream* below before 14 days pass. |
| `dev-nyc311-monitor-source-stale` | At the latest cursor run, the newest change any cursor run has found was over 48 h old | The source may have stopped publishing (check the dataset's page); the pipeline itself is fine. |
| `dev-nyc311-monitor-missed-changes` | The latest Reconciliation Pass found changes the cursor missed | See *Missed changes* below. |
| `dev-nyc311-monitor-reconciliation-stale` | No Reconciliation Pass processed for 8 days (counted from when run metrics began, before the first) | Scheduler `dev-nyc311-collector-reconcile`, collector logs. An unfinished pass is resumed daily; one older than a week is not: rerun it by hand with its date, or remove its files from the stage so they stop holding up processing. |
| `dev-nyc311-monitor-scoring-incomplete` | The latest Publication left eligible Requests unscored | `SELECT * FROM ML.APPROVED_MODEL` (none approved?). Missing scores are never filled in later. |
| `dev-nyc311-monitor-failed` | The monitor Lambda failed | Its logs `/aws/lambda/dev-nyc311-monitor`; usually Snowflake credentials. |

The unknown-Problem rate (`UnknownProblemRate`) is published without an alarm: a rising share of scored Requests whose Problem the model never saw is a reason to retrain (`python -m ml.train`).

## Missed changes

A reconciliation run re-fetches older creation dates through the daily prefix, so whatever it finds is loaded and processed like a cursor pass: the Current Requests are already corrected. The alarm says the change cursor's assumption broke for those Requests. List them:

```sql
SELECT v.unique_key, v.valid_from_updated_at, v.payload:status::STRING AS status
FROM CLEAN.REQUEST_VERSIONS AS v
WHERE v.valid_from_run_id = '<reconcile run id>';
```

If their source `:updated_at` did not move when their content changed, the source edits rows without bumping it; a widening count week on week means the cursor alone is not enough. The first passes may also find changes made between the backfill's extraction and the cursor's start (ticket 10).

## Stale stream

`RAW.NEW_OBSERVATIONS` becomes stale if it is not consumed for longer than the table's data retention plus `MAX_DATA_EXTENSION_TIME_IN_DAYS` (14 days by default); a stale stream can no longer be read. Before that, fix whatever stops the readiness check. After it:

1. Recreate the stream: `CREATE OR REPLACE STREAM RAW.NEW_OBSERVATIONS ON TABLE RAW.RAW_311_OBSERVATIONS APPEND_ONLY = TRUE SHOW_INITIAL_ROWS = TRUE;` (re-reads every RAW row; processing is idempotent, so Observations already processed collapse into their Request Versions).
2. Process: `uv run --env-file .env python -m warehouse.pipeline --ready`.
3. Observations older than RAW's 90-day retention are only in S3: replay their files with bulk COPY (`python -m warehouse.pipeline`).
