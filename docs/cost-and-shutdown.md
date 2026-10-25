# Costs and shutdown

## What costs money

| Piece | When it runs | Expected cost |
|---|---|---|
| Snowflake warehouse `NYC311_WH` (X-Small, 60 s auto-suspend) | The readiness checks every 30 min (only while data waits), the daily sweep, Publications (about 2 min each), dashboard queries, training by hand | Roughly $10–20 a month after the trial (instruction.md §16) |
| Snowpipe | Each daily or reconciliation file | Small: per file, plus serverless compute. Monitor it apart from the warehouse |
| S3 | Observation files (about 1 GB compressed for 7M rows), kept; moved to cheaper storage after 90 days | Cents a month |
| Lambda | The collector (daily, plus the Reconciliation Pass) and the monitor (hourly) | Within the free tier |
| CloudWatch | 10 custom metrics, 12 alarms, 30-day logs | About $0.20 a month: the first 10 metrics and 10 alarms are free |
| ECS Fargate backfill | One-off, by hand | Cents per run |
| Public dashboard (prod, not yet deployed) | Always on, once deployed | About $41 a month: load balancer, 0.25 vCPU / 0.5 GB task, three public IPv4 addresses |

**Measured so far (AWS Cost Explorer, October 2026 to the 25th):** effectively $0. That covers the ECS backfill, the Lambdas, S3, Snowpipe's notification queue and ECR, within credits and the free tier. Costs show in Cost Explorer about a day late.

**Snowflake credits:** run this as ACCOUNTADMIN (the project roles can't read account usage):

```sql
SELECT warehouse_name, ROUND(SUM(credits_used), 2) AS credits
FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
WHERE start_time >= DATE_TRUNC('month', CURRENT_DATE)
GROUP BY 1;

SELECT pipe_name, ROUND(SUM(credits_used), 3) AS credits     -- Snowpipe, billed apart from warehouses
FROM SNOWFLAKE.ACCOUNT_USAGE.PIPE_USAGE_HISTORY
WHERE start_time >= DATE_TRUNC('month', CURRENT_DATE)
GROUP BY 1;
```

## Controls in place

- **AWS budget alert** at $10 a month (ticket 01). It warns; it does not cap spending.
- **Warehouse limits:** X-Small, 60-second auto-suspend, 600-second statement timeout, and 60 s for dashboard queries.
- **Task schedules:**
  - The readiness task wakes only when the stream has data.
  - Publishing skips when its inputs are unchanged.
  - Dashboard aggregates are cached for the life of a Publication.
- **Retention:** logs 30 days, RAW 90 days, and an S3 lifecycle rule.
- **Deliberate runs only:** training and backfills are never scheduled.

## Shutdown

Stop the paid pieces without losing data. The S3 bucket is protected (`prevent_destroy`), and Snowflake keeps its tables.

1. **Dashboard (prod):** set `dashboard_image_tag = null` in `environments/prod` and apply. The load balancer, service and cluster go; the image repository and secret stay.
2. **Daily collection and monitoring:**
   - In the EventBridge Scheduler console, disable `<env>-nyc311-collector-daily` and `<env>-nyc311-collector-reconcile`. Expect `collector-missed` to alarm once.
   - Set the monitor's `image_tag = null` and apply. The Lambda, its schedule and its alarms go; the image repository stays.
3. **Snowflake tasks:** run `ALTER TASK OPS.READINESS_TASK SUSPEND; ALTER TASK OPS.READINESS_SWEEP_TASK SUSPEND;` and `ALTER PIPE RAW.OBSERVATIONS_PIPE SET PIPE_EXECUTION_PAUSED = TRUE;`. The warehouse then stays suspended and costs nothing.
4. **Leave or remove storage:** S3 and Snowflake storage cost cents. To delete everything, remove `prevent_destroy` from the bucket in `modules/core` (a deliberate code change), run `terraform destroy`, then `DROP DATABASE NYC311_DEV` (and `NYC311`) as ACCOUNTADMIN.

To resume: re-enable the schedules, resume the tasks and the pipe. The cursor continues from `checkpoints/cursor/311.json`, and the 6-hour overlap means nothing between the last pass and now is lost.

## Accounts

- **Snowflake:** the trial ends around late November 2026; add a payment method before then.
- **AWS:** the Free plan closes after six months or when credits run out; upgrade to the Paid plan before that to keep the dashboard up.
