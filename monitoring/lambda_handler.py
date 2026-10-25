"""Monitor Lambda: reads APP.MONITOR (one row of pipeline health signals)
through the read-only dashboard role and publishes it as CloudWatch metrics,
which the alarms in infrastructure/terraform/modules/monitor watch.

EventBridge Scheduler invokes it hourly. A missing signal (no Publication
yet, no reconciliation run yet) publishes nothing, and the alarms treat that
as they are configured to.

Environment:
    NYC311_ENV                    dev or prod: the database read and the metric namespace NYC311/<env>
    NYC311_SNOWFLAKE_SECRET_NAME  Secrets Manager entry with the key-pair credentials
"""

import json
import os
from decimal import Decimal

from warehouse.connection import REGION, connect
from warehouse.environment import current

# APP.MONITOR column -> (CloudWatch metric, unit). Only the signals the
# alarms watch, plus the unknown-Problem rate, so the metrics stay within
# CloudWatch's 10 free custom metrics. Run volumes (extracted, loaded,
# distinct and updated Requests, runtime) are tracked in OPS.RUN_METRICS and
# shown on the Pipeline Health page.
METRICS = {
    "hours_since_publication": ("PublicationAgeHours", "None"),
    "latest_attempt_failed": ("PublicationAttemptFailed", "Count"),
    "hours_since_processing": ("ProcessingAgeHours", "None"),
    "processing_failing": ("ProcessingFailing", "Count"),
    "stuck_runs": ("StuckRuns", "Count"),
    "source_lag_hours": ("SourceLagHours", "None"),
    "days_since_reconciliation": ("ReconciliationAgeDays", "None"),
    "missed_changes": ("MissedChanges", "Count"),
    "scoring_coverage": ("ScoringCoverage", "None"),
    "unknown_problem_rate": ("UnknownProblemRate", "None"),
}


def read_signals() -> dict:
    env = current()
    with connect(env.dashboard_role, env.warehouse, env.database, statement_timeout_seconds=120) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM APP.MONITOR")
        row = cur.fetchone()
        assert isinstance(row, tuple)
        return dict(zip((c[0].lower() for c in cur.description or []), row))


def metric_data(signals: dict) -> list[dict]:
    return [
        {"MetricName": name, "Value": float(value), "Unit": unit}
        for column, (name, unit) in METRICS.items()
        if (value := signals.get(column)) is not None and isinstance(value, (int, float, Decimal))
    ]


def handler(event: dict, context) -> dict:
    import boto3

    signals = read_signals()
    data = metric_data(signals)
    cloudwatch = boto3.client("cloudwatch", region_name=REGION)
    namespace = f"NYC311/{os.environ.get('NYC311_ENV', 'dev')}"
    cloudwatch.put_metric_data(Namespace=namespace, MetricData=data)
    print(json.dumps({"published": len(data), **{k: str(v) for k, v in signals.items()}}))
    return {"published": len(data)}
