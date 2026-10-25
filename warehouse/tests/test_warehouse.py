"""Seam 2: fixture Observations into a dev database; assertions on APP views."""

import csv
from datetime import date, datetime, timezone

import pytest
from snowflake.connector.errors import ProgrammingError

from tableau.export import export
from warehouse.connection import connect
from warehouse.pipeline import run
from warehouse.tests.fixtures import EXTRACTED, STAGES, source_row, stage_collector_run


def rows_of(conn, sql: str) -> list[dict]:
    cur = conn.cursor().execute(sql)
    names = [c[0].lower() for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def test_collected_slice_reaches_the_overview_and_reconciles(empty_db, tmp_path):
    rows = [
        source_row("100", "2026-09-01T08:00:00.000"),
        source_row("101", "2026-09-01T09:30:00.000", status="Closed", closed_date="2026-09-01T12:00:00.000"),
        source_row("102", "2026-09-01T23:59:59.000"),
        source_row("200", "2026-09-02T00:00:00.000", status="Closed", closed_date="2026-09-03T00:00:00.000"),
    ]
    manifest = stage_collector_run(empty_db, tmp_path, "slice", rows, date(2026, 9, 1), 2, EXTRACTED)

    run(empty_db, STAGES)

    daily = rows_of(empty_db, "SELECT * FROM APP.OVERVIEW_DAILY ORDER BY created_date")
    assert daily == [
        {"created_date": date(2026, 9, 1), "requests": 3, "closed_requests": 1, "open_requests": 2},
        {"created_date": date(2026, 9, 2), "requests": 1, "closed_requests": 1, "open_requests": 0},
    ]
    [coverage] = rows_of(empty_db, "SELECT * FROM APP.DATA_COVERAGE")
    assert manifest["row_count"] == 4
    assert coverage["manifest_rows"] == 4
    assert coverage["observations_loaded"] == 4
    assert coverage["distinct_requests_observed"] == 4
    assert coverage["current_requests"] == 4
    assert coverage["files_loaded"] == manifest["file_count"] == coverage["manifest_files"]
    assert (coverage["first_created_date"], coverage["last_created_date"]) == (date(2026, 9, 1), date(2026, 9, 2))


def test_newer_observation_wins_and_reloading_changes_nothing(empty_db, tmp_path):
    later = datetime(2026, 9, 11, 6, 0, tzinfo=timezone.utc)
    stage_collector_run(empty_db, tmp_path, "day-1", [source_row("300", "2026-09-01T08:00:00.000")], date(2026, 9, 1), 1, EXTRACTED)
    stage_collector_run(
        empty_db, tmp_path, "day-2",
        [source_row("300", "2026-09-01T08:00:00.000", updated_at="2026-09-11T01:33:00.000Z", status="Closed", closed_date="2026-09-10T10:00:00.000")],
        date(2026, 9, 1), 1, later,
    )

    run(empty_db, STAGES)
    second = run(empty_db, STAGES)

    # The later Observation (Closed) wins; one Current Request, counted once.
    daily = rows_of(empty_db, "SELECT * FROM APP.OVERVIEW_DAILY")
    assert daily == [{"created_date": date(2026, 9, 1), "requests": 1, "closed_requests": 1, "open_requests": 0}]
    [coverage] = rows_of(
        empty_db, "SELECT observations_loaded, manifest_rows, current_requests, latest_extracted_at FROM APP.DATA_COVERAGE"
    )
    assert coverage == {"observations_loaded": 2, "manifest_rows": 2, "current_requests": 1, "latest_extracted_at": later}
    assert second == {
        "manifests_loaded": 0, "observations_loaded": 0, "observations_quarantined": 0, "current_requests_merged": 0,
        "publication": "unchanged",
    }


def test_dashboard_role_reads_app_views_and_nothing_else(test_env):
    _, env = test_env
    with connect(env.dashboard_role, env.warehouse, env.database) as dashboard:
        cur = dashboard.cursor()
        cur.execute("SELECT COUNT(*) FROM APP.OVERVIEW_DAILY")
        cur.execute("SELECT COUNT(*) FROM APP.DATA_COVERAGE")
        cur.execute("SELECT COUNT(*) FROM APP.TABLEAU_DAILY")
        cur.execute("SELECT COUNT(*) FROM APP.PUBLICATION_STATUS")
        for view in (
            "PUBLISHED_OVERVIEW_DAILY", "PUBLISHED_DATA_COVERAGE", "PUBLISHED_TABLEAU_DAILY",
            "PUBLISHED_DAILY_REQUEST_STATS", "PUBLISHED_BACKLOG_DAILY", "PUBLISHED_DURATION_STATS",
            "PUBLISHED_GEO_CELLS", "PUBLISHED_OVERDUE_QUEUE", "PUBLISHED_LABEL_SUMMARY", "MODEL_EVALUATIONS", "MODEL_METRICS",
            "PUBLISHED_RISK_QUEUE", "ADMISSION_SCORES", "REQUEST_LOOKUP",
        ):
            cur.execute(f"SELECT COUNT(*) FROM APP.{view}")
        for forbidden in (
            "SELECT COUNT(*) FROM CLEAN.SERVICE_REQUESTS",
            "SELECT COUNT(*) FROM RAW.RAW_311_OBSERVATIONS",
            "SELECT COUNT(*) FROM OPS.RUN_MANIFESTS",
            "SELECT COUNT(*) FROM OPS.PUBLICATIONS",
            "SELECT COUNT(*) FROM OPS.STEP_STATUS",
            "SELECT COUNT(*) FROM ANALYTICS.PUB_OVERVIEW_DAILY",
            "SELECT COUNT(*) FROM ANALYTICS.PUB_OVERDUE_QUEUE",
            "SELECT COUNT(*) FROM ANALYTICS.REQUEST_FACTS",
            "SELECT COUNT(*) FROM ML.TRAINING_LABELS",
            "SELECT COUNT(*) FROM ML.CURRENT_LABELS",
            "SELECT COUNT(*) FROM ML.FEATURES",
            "SELECT COUNT(*) FROM ML.MODEL_EXAMPLES",
            "SELECT COUNT(*) FROM ML.MODEL_EVALUATIONS",
            "SELECT COUNT(*) FROM ML.MODEL_METRICS",
            "SELECT COUNT(*) FROM ML.MODEL_PROMOTIONS",
            "SELECT COUNT(*) FROM ML.FEATURE_SNAPSHOTS",
            "SELECT COUNT(*) FROM ML.REQUEST_PREDICTIONS",
            "SELECT COUNT(*) FROM ANALYTICS.PUB_RISK_QUEUE",
            "CREATE TABLE APP.SNEAKY (x INT)",
            "CREATE OR REPLACE VIEW APP.OVERVIEW_DAILY AS SELECT 1 AS x",
        ):
            with pytest.raises(ProgrammingError):
                cur.execute(forbidden)


def test_tableau_export_writes_counts_without_request_detail(empty_db, test_env, tmp_path):
    _, env = test_env
    rows = [
        source_row("400", "2026-09-01T08:00:00.000"),
        source_row("401", "2026-09-01T09:00:00.000", status="Closed", closed_date="2026-09-02T09:00:00.000"),
        source_row("402", "2026-09-01T10:00:00.000", borough="QUEENS", incident_address="1 MAIN ST"),
    ]
    stage_collector_run(empty_db, tmp_path, "tableau", rows, date(2026, 9, 1), 1, EXTRACTED)
    run(empty_db, STAGES)

    with connect(env.dashboard_role, env.warehouse, env.database) as dashboard:
        written = export(dashboard, tmp_path / "out")

    with open(tmp_path / "out" / "nyc311_daily.csv", newline="") as f:
        daily = list(csv.DictReader(f))
    assert written == {"nyc311_daily.csv": 2, "nyc311_coverage.csv": 1}
    assert [(r["borough"], r["requests"], r["closed_requests"], r["open_requests"]) for r in daily] == [
        ("BROOKLYN", "2", "1", "1"),
        ("QUEENS", "1", "0", "1"),
    ]
    assert set(daily[0]) == {
        "created_date", "borough", "agency", "problem", "channel", "requests", "closed_requests", "open_requests",
    }
