"""Seam 2: the readiness gate. Collector runs flow downstream only once their
manifest reconciles with the files and rows loaded into RAW, and only once."""

import re
from datetime import date, datetime, timezone

import pytest
from snowflake.connector.errors import ProgrammingError

from ingestion.collector import CursorPlan, RunLayout
from warehouse.pipeline import process_ready_runs, publish, run
from warehouse.tests.fixtures import STAGES, collect, load_observation_files, put_collected, source_row
from warehouse.tests.test_warehouse import rows_of

DAY_1 = datetime(2026, 10, 24, 6, 0, tzinfo=timezone.utc)
DAY_2 = datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc)
PLAN = CursorPlan(initial_cursor="2026-10-23T20:00:00.000Z")
CREATED = "2026-10-20T08:00:00.000"


def readiness(conn) -> dict:
    """What the task graph does: the readiness check, then (if it succeeds) publish."""
    result = process_ready_runs(conn)
    publish(conn)
    return result


def manifest_key(manifest: dict) -> str:
    return RunLayout(manifest["run_id"], manifest["extract_date"], "daily").manifest_key


def data_keys(manifest: dict) -> list[str]:
    return [f["key"] for f in manifest["files"]]


def overview(conn) -> list[tuple]:
    """(created date, Requests, closed) as the dashboard sees them."""
    return [
        (r["created_date"], r["requests"], r["closed_requests"])
        for r in rows_of(conn, "SELECT * FROM APP.OVERVIEW_DAILY ORDER BY created_date")
    ]


def observations_loaded(conn) -> int:
    [row] = rows_of(conn, "SELECT observations_loaded FROM APP.DATA_COVERAGE")
    return row["observations_loaded"]


def deliver(conn, bucket, manifest: dict) -> None:
    """What happens after a collector run: its files load, then its manifest is on the stage."""
    put_collected(conn, bucket, "data", data_keys(manifest))
    load_observation_files(conn)
    put_collected(conn, bucket, "manifests", [manifest_key(manifest)])


def pipeline_runs(conn) -> list[tuple]:
    return [
        (r["run_id"], r["state"], r["expected_rows"], r["loaded_rows"], r["observations_processed"])
        for r in rows_of(conn, "SELECT * FROM OPS.PIPELINE_RUNS ORDER BY run_id")
    ]


def test_a_request_closed_at_the_source_is_closed_after_the_next_run(empty_db, tmp_path):
    bucket = tmp_path / "bucket"
    day_1 = collect(bucket, [source_row("700", CREATED, updated_at="2026-10-24T01:33:15.966Z")], PLAN, DAY_1)
    deliver(empty_db, bucket, day_1)

    assert readiness(empty_db)["outcome"] == "processed"
    assert overview(empty_db) == [(date(2026, 10, 20), 1, 0)]

    closed = source_row("700", CREATED, updated_at="2026-10-25T01:33:10.000Z", status="Closed", closed_date="2026-10-24T16:00:00.000")
    day_2 = collect(bucket, [closed, source_row("701", "2026-10-24T09:00:00.000", updated_at="2026-10-25T01:33:10.000Z")], PLAN, DAY_2)
    deliver(empty_db, bucket, day_2)

    result = readiness(empty_db)

    assert result == {"outcome": "processed", "runs": [day_2["run_id"]], "observations": 2, "quarantined": 0}
    assert overview(empty_db) == [(date(2026, 10, 20), 1, 1), (date(2026, 10, 24), 1, 0)]
    assert pipeline_runs(empty_db) == [(day_1["run_id"], "consumed", 1, 1, 1), (day_2["run_id"], "consumed", 2, 2, 2)]


def two_file_run(tmp_path) -> tuple:
    bucket = tmp_path / "bucket"
    rows = [source_row(str(800 + n), CREATED, updated_at=f"2026-10-24T01:3{n}:00.000Z") for n in range(3)]
    manifest = collect(bucket, rows, PLAN, DAY_1, page_size=2)
    assert len(manifest["files"]) == 2
    return bucket, manifest


def test_files_without_their_manifest_wait_and_nothing_flows_downstream(empty_db, tmp_path):
    bucket, manifest = two_file_run(tmp_path)
    put_collected(empty_db, bucket, "data", data_keys(manifest))
    load_observation_files(empty_db)

    assert readiness(empty_db) == {"outcome": "waiting", "blocked_files": 2, "pending_rows": 3, "ready_runs": []}
    assert overview(empty_db) == []

    # The manifest arrives; the same Observations (still pending) now flow.
    put_collected(empty_db, bucket, "manifests", [manifest_key(manifest)])
    assert readiness(empty_db)["observations"] == 3
    assert overview(empty_db) == [(date(2026, 10, 20), 3, 0)]


def test_a_partly_loaded_run_waits_until_every_file_and_row_is_in(empty_db, tmp_path):
    bucket, manifest = two_file_run(tmp_path)
    first, second = data_keys(manifest)
    put_collected(empty_db, bucket, "data", [first])
    load_observation_files(empty_db)
    put_collected(empty_db, bucket, "manifests", [manifest_key(manifest)])

    assert readiness(empty_db)["outcome"] == "waiting"
    assert overview(empty_db) == []
    assert pipeline_runs(empty_db) == [(manifest["run_id"], "waiting", 3, 2, None)]

    put_collected(empty_db, bucket, "data", [second])
    load_observation_files(empty_db)

    assert readiness(empty_db)["outcome"] == "processed"
    assert pipeline_runs(empty_db) == [(manifest["run_id"], "consumed", 3, 3, 3)]


def test_a_consumed_run_is_never_processed_again(empty_db, tmp_path):
    bucket, manifest = two_file_run(tmp_path)
    deliver(empty_db, bucket, manifest)
    readiness(empty_db)

    # Bulk-loading the same files again adds nothing to RAW; the check is idle.
    load_observation_files(empty_db)
    assert readiness(empty_db) == {"outcome": "idle"}
    assert observations_loaded(empty_db) == 3
    assert pipeline_runs(empty_db) == [(manifest["run_id"], "consumed", 3, 3, 3)]


def test_bulk_load_skips_files_snowpipe_already_loaded(empty_db, tmp_path):
    bucket, manifest = two_file_run(tmp_path)
    put_collected(empty_db, bucket, "data", data_keys(manifest))
    put_collected(empty_db, bucket, "manifests", [manifest_key(manifest)])

    # Load exactly as the pipe does: its own COPY statement, against the fixture stage.
    [ddl] = empty_db.cursor().execute("SELECT GET_DDL('PIPE', 'RAW.OBSERVATIONS_PIPE')").fetchone()  # type: ignore[misc]
    copy = re.search(r"(?is)\bas\s+(copy\s+into.*)", ddl).group(1)  # type: ignore[union-attr]
    empty_db.cursor().execute(copy.replace("@RAW.OBSERVATIONS_STAGE/", STAGES.observations))

    counts = run(empty_db, STAGES)

    assert counts["observations_loaded"] == 0
    assert observations_loaded(empty_db) == 3
    assert overview(empty_db) == [(date(2026, 10, 20), 3, 0)]
    # Its run reconciles once: each row was loaded exactly once.
    readiness(empty_db)
    [run_row] = rows_of(empty_db, "SELECT state, loaded_rows, duplicate_rows FROM OPS.PIPELINE_RUNS")
    assert run_row == {"state": "consumed", "loaded_rows": 3, "duplicate_rows": 0}


def test_an_empty_pass_is_consumed_by_the_daily_sweep(empty_db, tmp_path):
    bucket = tmp_path / "bucket"
    empty = collect(bucket, [], PLAN, DAY_1)
    put_collected(empty_db, bucket, "manifests", [manifest_key(empty)])

    result = readiness(empty_db)

    assert result == {"outcome": "processed", "runs": [empty["run_id"]], "observations": 0, "quarantined": 0}
    assert pipeline_runs(empty_db) == [(empty["run_id"], "consumed", 0, 0, 0)]


def test_a_run_waiting_more_than_six_hours_fails_the_check(empty_db, tmp_path):
    bucket, manifest = two_file_run(tmp_path)
    first, _ = data_keys(manifest)
    put_collected(empty_db, bucket, "data", [first])
    load_observation_files(empty_db)
    put_collected(empty_db, bucket, "manifests", [manifest_key(manifest)])
    assert readiness(empty_db)["outcome"] == "waiting"

    # Seven hours later the second file has still not loaded.
    empty_db.cursor().execute("UPDATE OPS.PIPELINE_RUNS SET first_seen_at = DATEADD(hour, -7, first_seen_at)")

    with pytest.raises(ProgrammingError, match="waited more than 6 hours"):
        readiness(empty_db)
    # The check's own bookkeeping still committed; nothing flowed downstream.
    assert pipeline_runs(empty_db) == [(manifest["run_id"], "waiting", 3, 2, None)]
    assert overview(empty_db) == []
