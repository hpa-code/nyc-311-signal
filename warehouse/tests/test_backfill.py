"""Seam 2: the historical backfill loaded month by month, then reconciled."""

from datetime import date

import pytest

from ingestion.api_client import SourceClient
from ingestion.backfill import backfill_plan
from ingestion.tests.fake_socrata import FakeSocrata
from warehouse.backfill import BackfillIncomplete, load, report
from warehouse.tests.fixtures import EXTRACTED, STAGES, collect, put_collected, source_row

PLAN = backfill_plan(through=date(2025, 2, 1))
ROWS = [
    source_row("1", "2025-01-01T00:00:00.000"),
    source_row("2", "2025-01-31T23:59:59.000", status="Closed", closed_date="2025-02-01T09:00:00.000"),
    source_row("3", "2025-02-01T00:00:00.000"),
]


def test_backfill_loads_each_month_publishes_and_reconciles(empty_db, tmp_path):
    collect(tmp_path, ROWS, PLAN, EXTRACTED, page_size=1)
    put_collected(empty_db, tmp_path, "data")
    put_collected(empty_db, tmp_path, "manifests")

    loaded = load(empty_db, PLAN, STAGES)

    assert [(m["month"], m["observations_loaded"]) for m in loaded["months"]] == [("2025-01", 2), ("2025-02", 1)]
    assert loaded["publication"] == "published"
    reconciled = report(empty_db, SourceClient(transport=FakeSocrata(ROWS), sleep=lambda s: None), PLAN)
    assert reconciled == [
        {"month": "2025-01", "source_rows": 2, "manifest_rows": 2, "raw_rows": 2, "current_requests": 2, "ok": True},
        {"month": "2025-02", "source_rows": 1, "manifest_rows": 1, "raw_rows": 1, "current_requests": 1, "ok": True},
    ]

    # A rerun loads nothing twice; a Request the source gained since shows as a difference.
    assert [m["observations_loaded"] for m in load(empty_db, PLAN, STAGES)["months"]] == [0, 0]
    grown = SourceClient(transport=FakeSocrata([*ROWS, source_row("4", "2025-02-20T10:00:00.000")]), sleep=lambda s: None)
    assert [(r["month"], r["source_rows"], r["ok"]) for r in report(empty_db, grown, PLAN)] == [
        ("2025-01", 2, True),
        ("2025-02", 2, False),
    ]


def test_backfill_waits_for_its_manifest_before_loading_anything(empty_db, tmp_path):
    collect(tmp_path, ROWS, PLAN, EXTRACTED)
    put_collected(empty_db, tmp_path, "data")

    with pytest.raises(BackfillIncomplete):
        load(empty_db, PLAN, STAGES)
    [[raw]] = empty_db.cursor().execute("SELECT COUNT(*) FROM RAW.RAW_311_OBSERVATIONS").fetchall()
    assert raw == 0
