"""Daily collector Lambda: one change-cursor pass into the observation bucket.

EventBridge Scheduler invokes it once a day. A pass that cannot finish inside
one invocation stops cleanly before the timeout and invokes the function again
to resume it; the cursor advances only when the pass completes.

Environment:
    NYC311_BUCKET                 observation bucket
    NYC311_SOCRATA_SECRET_NAME    Secrets Manager entry with {"app_token": ...}
    NYC311_INITIAL_CURSOR         optional; seeds the cursor if none is stored
    NYC311_PAGE_SIZE              optional; rows per page and file (default 10000)
"""

import json
import os
from collections.abc import Callable
from datetime import datetime, timedelta

from ingestion.api_client import SourceClient
from ingestion.collector import Collector, CursorPlan, RunPaused, utc_now
from ingestion.fetch_311 import REGION, app_token
from ingestion.lease import LeaseHeld
from ingestion.object_store import S3ObjectStore

# Stop starting new pages this long before the Lambda timeout: one page fetch
# plus its file write must fit in it.
SAFETY_MARGIN = timedelta(seconds=150)
# A pass resumed this many times without finishing is failing, not slow.
MAX_CONTINUATIONS = 12


def run_pass(
    event: dict,
    collector: Collector,
    plan: CursorPlan,
    stop_at: datetime,
    continue_later: Callable[[dict], None],
) -> dict:
    continuation = int(event.get("continuation", 0))
    try:
        manifest = collector.run(plan, stop_at=stop_at)
    except LeaseHeld as held:
        # Another invocation is collecting; it owns this pass.
        return {"outcome": "skipped", "reason": str(held)}
    except RunPaused as paused:
        if continuation >= MAX_CONTINUATIONS:
            raise RuntimeError(f"pass not finished after {continuation} continuations: {paused}") from paused
        continue_later({"continuation": continuation + 1})
        return {"outcome": "paused", "reason": str(paused), "continuation": continuation + 1}
    return {
        "outcome": "complete",
        "run_id": manifest["run_id"],
        "files": manifest["file_count"],
        "rows": manifest["row_count"],
        "last_position": manifest["partitions"][0]["last_position"],
    }


def handler(event: dict, context) -> dict:
    import boto3

    started = utc_now()
    stop_at = started + timedelta(milliseconds=context.get_remaining_time_in_millis()) - SAFETY_MARGIN
    collector = Collector(
        source=SourceClient(app_token=app_token()),
        store=S3ObjectStore(boto3.client("s3", region_name=REGION), os.environ["NYC311_BUCKET"]),
        page_size=int(os.environ.get("NYC311_PAGE_SIZE", "10000")),
    )
    plan = CursorPlan(initial_cursor=os.environ.get("NYC311_INITIAL_CURSOR") or None)

    def continue_later(payload: dict) -> None:
        boto3.client("lambda", region_name=REGION).invoke(
            FunctionName=context.invoked_function_arn, InvocationType="Event", Payload=json.dumps(payload).encode()
        )

    result = run_pass(event, collector, plan, stop_at, continue_later)
    print(json.dumps({**result, "started_at": started.isoformat(), "seconds": (utc_now() - started).total_seconds()}))
    return result
