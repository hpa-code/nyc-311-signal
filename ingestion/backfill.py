"""Historical backfill: every Request created from 2025-01 through a fixed last
month, one partition per NYC-local creation month, written to data/bulk/ (bulk
COPY only). Runs as a one-off ECS Fargate task.

    python -m ingestion.backfill --through 2026-10 --store s3:dev-nyc311-observations-<account>
    python -m ingestion.backfill --through 2026-10 --store s3:... --status

Rerun with the same --through to resume: complete months are skipped and an
interrupted month continues from its page cursor. A task killed mid-run never
releases its lease, so the rerun waits for it to expire.

--store defaults to s3:$NYC311_BUCKET; the app token comes as for
ingestion.fetch_311.
"""

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta

from ingestion.api_client import CreationWindow, SourceClient
from ingestion.collector import (
    LEASE_TTL,
    SCOPE_START,
    Collector,
    Plan,
    creation_month_partitions,
    partition_checkpoint_key,
)
from ingestion.fetch_311 import app_token, open_store
from ingestion.lease import LeaseHeld
from ingestion.object_store import ObjectStore

FIRST_MONTH = SCOPE_START.date()
# Long enough for a killed task's lease to expire.
LEASE_WAIT = LEASE_TTL + timedelta(minutes=4)
LEASE_POLL = timedelta(minutes=1)


def backfill_plan(through: date) -> Plan:
    """The backfill run for months 2025-01 through `through`'s month. The run ID
    names its last month, so a resumed run always has the same partitions."""
    return Plan(
        run_id=f"backfill-{FIRST_MONTH:%Y%m}-{through:%Y%m}",
        partitions=creation_month_partitions(FIRST_MONTH, through),
    )


def parse_month(value: str) -> date:
    """A --through value, YYYY-MM."""
    return datetime.strptime(value, "%Y-%m").date()


def month_windows(plan: Plan) -> list[CreationWindow]:
    """The plan's partitions, which for a backfill are all creation months."""
    windows = [w for w in plan.partitions if isinstance(w, CreationWindow)]
    assert len(windows) == len(plan.partitions), "a backfill plan has only creation-month partitions"
    return windows


def run_backfill(
    collector: Collector, plan: Plan, sleep: Callable[[float], None] = time.sleep, wait: timedelta = LEASE_WAIT
) -> dict:
    """Collect (or resume) the plan and return its manifest, waiting up to
    `wait` for a lease left by a killed task to expire."""
    waited = timedelta(0)
    while True:
        try:
            return collector.run(plan)
        except LeaseHeld as held:
            if waited >= wait:
                raise
            print(f"waiting for the lease: {held}", flush=True)
            sleep(LEASE_POLL.total_seconds())
            waited += LEASE_POLL


def progress(store: ObjectStore, plan: Plan) -> list[dict]:
    """Each month's checkpoint: complete, in_progress, or not_started."""
    months = []
    for window in month_windows(plan):
        stored = store.get(partition_checkpoint_key(plan.run_id, window))
        if stored is None:
            months.append({"month": window.month, "state": "not_started", "files": 0, "rows": 0})
            continue
        checkpoint = json.loads(stored)
        months.append(
            {"month": window.month, "state": checkpoint["state"], "files": len(checkpoint["files"]), "rows": checkpoint["row_count"]}
        )
    return months


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--through", required=True, type=parse_month, help="last creation month, YYYY-MM")
    parser.add_argument("--store", default=f"s3:{os.environ.get('NYC311_BUCKET', '')}", help="local:<dir> or s3:<bucket>")
    parser.add_argument("--status", action="store_true", help="print each month's progress and exit")
    parser.add_argument("--page-size", type=int, default=10_000)
    args = parser.parse_args(argv)
    store = open_store(args.store)
    plan = backfill_plan(args.through)

    if not args.status:
        collector = Collector(source=SourceClient(app_token=app_token()), store=store, page_size=args.page_size)
        manifest = run_backfill(collector, plan)
        print(f"run {plan.run_id}: {manifest['file_count']} files, {manifest['row_count']} rows", flush=True)
    for month in progress(store, plan):
        print(f"  {month['month']}: {month['state']}, {month['files']} files, {month['rows']} rows", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
