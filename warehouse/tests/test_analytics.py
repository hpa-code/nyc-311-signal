"""Seam 2: analytics outputs behind the Overview, Geographic and Overdue Queue
pages, read by publication_id as the dashboard reads them."""

from datetime import date, datetime, timezone

import pytest
from snowflake.connector.errors import ProgrammingError

from warehouse.pipeline import publish, run
from warehouse.tests.fixtures import EXTRACTED, STAGES, source_row, stage_collector_run
from warehouse.tests.test_publication import status
from warehouse.tests.test_warehouse import rows_of


def published(conn, view: str, where: str = "TRUE", order: str = "1") -> list[dict]:
    """Rows of APP.PUBLISHED_<view> for the current Publication, without its ID."""
    pid = status(conn)["publication_id"]
    return rows_of(
        conn, f"SELECT * EXCLUDE publication_id FROM APP.PUBLISHED_{view} WHERE publication_id = '{pid}' AND {where} ORDER BY {order}"
    )


def backlog(conn, kind: str) -> dict[date, int]:
    pid = status(conn)["publication_id"]
    rows = rows_of(
        conn,
        "SELECT backlog_date, SUM(open_requests) AS n FROM APP.PUBLISHED_BACKLOG_DAILY"
        f" WHERE publication_id = '{pid}' AND kind = '{kind}' GROUP BY 1 ORDER BY 1",
    )
    return {r["backlog_date"]: r["n"] for r in rows}


def test_overdue_queue_lists_requests_open_past_the_horizon_including_nypd(empty_db, tmp_path):
    # As of the extraction, 2026-09-10 06:00 UTC = 02:00 New York time.
    rows = [
        source_row("500", "2026-09-01T08:00:00.000", agency="NYPD", complaint_type="Noise - Residential"),
        source_row("501", "2026-09-03T01:59:59.000"),  # one second past 168 hours
        source_row("502", "2026-09-03T02:00:00.000"),  # exactly 168 hours: not yet past
        source_row("503", "2026-09-01T08:00:00.000", status="Closed", closed_date="2026-09-02T08:00:00.000"),
        source_row("504", "2026-09-05T08:00:00.000", agency="NYPD", complaint_type="Noise - Residential"),
    ]
    stage_collector_run(empty_db, tmp_path, "overdue", rows, date(2026, 9, 1), 5, EXTRACTED)
    run(empty_db, STAGES)

    queue = published(empty_db, "OVERDUE_QUEUE", order="created_at_local")
    assert [(r["unique_key"], r["agency"], r["status"], r["hours_open"]) for r in queue] == [
        ("500", "NYPD", "Open", 210),
        ("501", "DSNY", "Open", 168),
    ]
    assert queue[0]["created_at_local"] == datetime(2026, 9, 1, 8, 0)
    assert set(queue[0]) == {
        "unique_key", "problem", "agency", "borough", "status", "created_at_local", "hours_open",
    }
    # NYPD is in the analytics too, not only the queue.
    agencies = rows_of(
        empty_db,
        "SELECT agency, SUM(submitted) AS n FROM APP.PUBLISHED_DAILY_REQUEST_STATS GROUP BY 1 ORDER BY 1",
    )
    assert agencies == [{"agency": "DSNY", "n": 3}, {"agency": "NYPD", "n": 2}]


def test_durations_come_with_denominators_closure_rates_and_unresolved_counts(empty_db, tmp_path):
    rows = [
        source_row("600", "2026-09-01T08:00:00.000", status="Closed", closed_date="2026-09-01T10:00:00.000"),  # 2 h
        source_row("601", "2026-09-01T08:00:00.000", status="Closed", closed_date="2026-09-09T08:00:00.000"),  # 192 h
        source_row("602", "2026-09-01T08:00:00.000"),  # open, Overdue
        source_row("603", "2026-09-01T08:00:00.000", status="Closed"),  # no Closure time
        source_row("604", "2026-09-08T08:00:00.000", status="Closed", closed_date="2026-09-08T12:00:00.000"),  # 4 h, young
        # Date-Only Closures, judged by calendar day: the creation day (0 h, within
        # the horizon), and the horizon day (09-08, 160 h to midnight: no outcome).
        source_row("606", "2026-09-01T08:00:00.000", status="Closed", closed_date="2026-09-01T00:00:00.000"),
        source_row("607", "2026-09-01T08:00:00.000", status="Closed", closed_date="2026-09-08T00:00:00.000"),
        source_row("605", "2026-09-02T08:00:00.000", agency="NYPD", complaint_type="Noise - Residential"),
    ]
    stage_collector_run(empty_db, tmp_path, "durations", rows, date(2026, 9, 1), 8, EXTRACTED)
    run(empty_db, STAGES)

    [dirty] = published(
        empty_db, "DURATION_STATS",
        "problem = 'Dirty Condition' AND created_month IS NULL AND agency IS NULL AND borough IS NULL",
    )
    assert dirty == {
        "created_month": None, "problem": "Dirty Condition", "agency": None, "borough": None,
        "requests": 7, "closed_requests": 6, "open_requests": 1,
        "timed_closures": 5, "median_hours": 4.0, "p90_hours": pytest.approx(179.2),
        "horizon_passed": 4, "closed_within_horizon": 2,
    }
    [everything] = published(
        empty_db, "DURATION_STATS", "created_month IS NULL AND problem IS NULL AND agency IS NULL AND borough IS NULL"
    )
    assert (everything["requests"], everything["open_requests"]) == (8, 2)
    [september] = published(
        empty_db, "DURATION_STATS", "created_month IS NOT NULL AND problem IS NULL AND agency IS NULL AND borough IS NULL"
    )
    assert (september["created_month"], september["requests"]) == (date(2026, 9, 1), 8)

    daily = published(empty_db, "DAILY_REQUEST_STATS", "problem = 'Dirty Condition'", "stat_date")
    assert [(r["stat_date"], r["submitted"], r["closed"]) for r in daily] == [
        (date(2026, 9, 1), 6, 2),
        (date(2026, 9, 8), 1, 2),
        (date(2026, 9, 9), 0, 1),
    ]


def test_backlog_keeps_one_observed_snapshot_per_day_beside_the_retrospective(empty_db, tmp_path):
    # Source updated 2026-09-08 01:33 UTC: the snapshot is for New York day 2026-09-07.
    first = [
        source_row("700", "2026-09-01T08:00:00.000"),
        source_row("701", "2026-09-02T08:00:00.000"),
        source_row("702", "2026-09-01T09:00:00.000", status="Closed", closed_date="2026-09-03T10:00:00.000"),
    ]
    stage_collector_run(empty_db, tmp_path, "day-1", first, date(2026, 9, 1), 2, EXTRACTED)
    run(empty_db, STAGES)
    assert backlog(empty_db, "observed") == {date(2026, 9, 7): 2}

    # Updated 2026-09-11 01:33 UTC (New York 2026-09-10): 701 Closed that morning.
    closed = source_row(
        "701", "2026-09-02T08:00:00.000", updated_at="2026-09-11T01:33:00.000Z", status="Closed", closed_date="2026-09-10T09:00:00.000"
    )
    stage_collector_run(empty_db, tmp_path, "day-2", [closed], date(2026, 9, 2), 1, datetime(2026, 9, 11, 6, 0, tzinfo=timezone.utc))
    run(empty_db, STAGES)

    assert backlog(empty_db, "observed") == {date(2026, 9, 7): 2, date(2026, 9, 10): 1}
    retrospective = backlog(empty_db, "retrospective")
    assert [retrospective[date(2026, 9, d)] for d in (1, 2, 3, 9, 10)] == [2, 3, 2, 2, 1]
    assert max(retrospective) == date(2026, 9, 10)

    # A held attempt (every Current Request doubled) neither adds nor replaces
    # a snapshot: once fixed, the next day's Publication shows 09-10 as it was.
    empty_db.cursor().execute("INSERT INTO CLEAN.SERVICE_REQUESTS SELECT * FROM CLEAN.SERVICE_REQUESTS")
    with pytest.raises(ProgrammingError, match="Publication held"):
        publish(empty_db)
    empty_db.cursor().execute("INSERT OVERWRITE INTO CLEAN.SERVICE_REQUESTS SELECT DISTINCT * FROM CLEAN.SERVICE_REQUESTS")
    changed = source_row("700", "2026-09-01T08:00:00.000", updated_at="2026-09-12T01:33:00.000Z", descriptor="Litter")
    stage_collector_run(empty_db, tmp_path, "day-3", [changed], date(2026, 9, 1), 1, datetime(2026, 9, 12, 6, 0, tzinfo=timezone.utc))
    run(empty_db, STAGES)
    assert backlog(empty_db, "observed") == {date(2026, 9, 7): 2, date(2026, 9, 10): 1, date(2026, 9, 11): 1}


def test_geographic_cells_hide_small_cells_and_carry_no_coordinates(empty_db, tmp_path):
    # Cells are published per Problem only, never beside an all-Problems total
    # that would let a reader subtract its way to a cell under 20.
    big = [source_row(f"8{i:02}", f"2026-09-01T{i:02}:00:00.000", latitude="40.70", longitude="-73.95") for i in range(20)]
    small = [source_row(f"82{i}", "2026-09-01T21:00:00.000", latitude="40.80", longitude="-73.90") for i in range(3)]
    unlocated = [
        source_row("830", "2026-09-01T22:00:00.000"),
        source_row("831", "2026-09-01T22:00:00.000", latitude="0", longitude="0"),  # outside NYC
    ]
    stage_collector_run(empty_db, tmp_path, "geo", big + small + unlocated, date(2026, 9, 1), 1, EXTRACTED)
    run(empty_db, STAGES)

    [[expected_cell]] = empty_db.cursor().execute("SELECT H3_LATLNG_TO_CELL_STRING(40.70, -73.95, 8)").fetchall()
    cells = published(empty_db, "GEO_CELLS", order="cell_kind")
    assert [(r["problem"], r["cell_kind"], r["h3_cell"], r["requests"]) for r in cells] == [
        ("Dirty Condition", "cell", expected_cell, 20),
        ("Dirty Condition", "hidden", None, 3),
        ("Dirty Condition", "no_location", None, 2),
    ]
    columns = rows_of(empty_db, "SELECT * FROM APP.PUBLISHED_GEO_CELLS LIMIT 1")[0].keys()
    assert not {"latitude", "longitude", "incident_address", "incident_zip"} & set(columns)
