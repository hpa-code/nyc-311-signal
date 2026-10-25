"""Seam 2: what each collector run did, and the changes a reconciliation pass
finds that the change cursor missed, read from APP.RUN_HEALTH and
APP.MONITOR (the Pipeline Health page and the CloudWatch monitor)."""

from datetime import date, datetime, timezone

import pytest

from ingestion.collector import CursorPlan, Plan, creation_day_partitions
from warehouse.tests.fixtures import collect, source_row
from warehouse.tests.test_readiness import deliver, readiness
from warehouse.tests.test_warehouse import rows_of

CURSOR_PASS = datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc)
RECONCILED = datetime(2026, 10, 25, 8, 0, tzinfo=timezone.utc)
CREATED = "2026-10-12T08:00:00.000"
SEEN = "2026-10-19T01:33:00.000Z"  # the latest :updated_at the cursor pass saw


def run_health(conn) -> dict[str, dict]:
    return {r["run_id"]: r for r in rows_of(conn, "SELECT * FROM APP.RUN_HEALTH")}


def test_a_reconciliation_pass_counts_the_changes_the_cursor_missed(empty_db, tmp_path):
    bucket = tmp_path / "bucket"
    cursor = collect(bucket, [
        source_row("300", CREATED, updated_at=SEEN),
        source_row("301", CREATED, updated_at=SEEN),
        source_row("302", CREATED, updated_at="2026-10-18T09:00:00.000Z"),
    ], CursorPlan(initial_cursor="2026-10-18T00:00:00.000Z"), CURSOR_PASS)
    deliver(empty_db, bucket, cursor)
    readiness(empty_db)

    reconcile = Plan("reconcile-2026W43", creation_day_partitions(date(2026, 10, 12), 1), channel="daily")
    sweep = collect(bucket, [
        # Closed without its :updated_at moving: the cursor could never see it.
        source_row("300", CREATED, updated_at=SEEN, status="Closed", closed_date="2026-10-13T08:00:00.000"),
        # Changed after the cursor pass: the next pass would have it.
        source_row("301", CREATED, updated_at="2026-10-24T01:33:00.000Z", status="Closed", closed_date="2026-10-23T08:00:00.000"),
        source_row("302", CREATED, updated_at="2026-10-18T09:00:00.000Z"),  # unchanged
        # Last changed before the cursor began, and never collected.
        source_row("303", CREATED, updated_at="2026-10-10T09:00:00.000Z"),
    ], reconcile, RECONCILED)
    deliver(empty_db, bucket, sweep)
    readiness(empty_db)

    runs = run_health(empty_db)
    measured = ("kind", "state", "extracted_rows", "loaded_rows", "distinct_requests", "updated_requests", "quarantined_rows", "missed_changes")
    assert {k: runs[cursor["run_id"]][k] for k in measured} == {
        "kind": "cursor", "state": "consumed", "extracted_rows": 3, "loaded_rows": 3, "distinct_requests": 3,
        "updated_requests": 3, "quarantined_rows": 0, "missed_changes": None,
    }
    assert {k: runs["reconcile-2026W43"][k] for k in measured} == {
        "kind": "reconcile", "state": "consumed", "extracted_rows": 4, "loaded_rows": 4, "distinct_requests": 4,
        "updated_requests": 3, "quarantined_rows": 0, "missed_changes": 2,
    }
    # Source freshness, apart from whether the run succeeded: extracted
    # 2026-10-20 06:00, its newest change 2026-10-19 01:33.
    assert runs[cursor["run_id"]]["source_lag_hours"] == pytest.approx(28.45)
    assert runs[cursor["run_id"]]["collector_seconds"] == 0

    [monitor] = rows_of(empty_db, "SELECT * FROM APP.MONITOR")
    assert (monitor["reconcile_run_id"], monitor["missed_changes"]) == ("reconcile-2026W43", 2)
    assert monitor["source_lag_hours"] == pytest.approx(28.45)
    assert monitor["stuck_runs"] == 0
