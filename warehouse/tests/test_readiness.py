"""Seam 2: the readiness gate. Collector runs flow downstream only once their
manifest reconciles with the files and rows loaded into RAW, and only once."""

import re
from datetime import datetime, timezone

from ingestion.collector import CursorPlan
from warehouse.pipeline import process_ready_runs, run
from warehouse.tests.fixtures import (
    FIXTURE_STAGE,
    STAGES,
    collect,
    load_observation_files,
    put_collected,
    source_row,
)
from warehouse.tests.test_warehouse import rows_of

DAY_1 = datetime(2026, 10, 24, 6, 0, tzinfo=timezone.utc)
DAY_2 = datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc)
PLAN = CursorPlan(initial_cursor="2026-10-23T20:00:00.000Z")
CREATED = "2026-10-20T08:00:00.000"


def manifest_key(manifest: dict) -> str:
    return f"manifests/extract_date={manifest['extract_date']}/run_id={manifest['run_id']}/manifest.json"


def data_keys(manifest: dict) -> list[str]:
    return [f["key"] for f in manifest["files"]]


def current(conn) -> list[tuple]:
    return [
        (r["unique_key"], r["status"])
        for r in rows_of(conn, "SELECT unique_key, status FROM CLEAN.SERVICE_REQUESTS ORDER BY unique_key")
    ]


def pipeline_runs(conn) -> list[tuple]:
    return [
        (r["run_id"], r["state"], r["expected_rows"], r["loaded_rows"], r["observations_processed"])
        for r in rows_of(conn, "SELECT * FROM OPS.PIPELINE_RUNS ORDER BY run_id")
    ]


def test_a_request_closed_at_the_source_is_closed_after_the_next_run(empty_db, tmp_path):
    bucket = tmp_path / "bucket"
    day_1 = collect(bucket, [source_row("700", CREATED, updated_at="2026-10-24T01:33:15.966Z")], PLAN, DAY_1)
    put_collected(empty_db, bucket, "data", data_keys(day_1))
    load_observation_files(empty_db)
    put_collected(empty_db, bucket, "manifests", [manifest_key(day_1)])

    assert process_ready_runs(empty_db)["outcome"] == "processed"
    assert current(empty_db) == [("700", "Open")]

    closed = source_row("700", CREATED, updated_at="2026-10-25T01:33:10.000Z", status="Closed", closed_date="2026-10-24T16:00:00.000")
    day_2 = collect(bucket, [closed, source_row("701", "2026-10-24T09:00:00.000", updated_at="2026-10-25T01:33:10.000Z")], PLAN, DAY_2)
    put_collected(empty_db, bucket, "data", data_keys(day_2))
    load_observation_files(empty_db)
    put_collected(empty_db, bucket, "manifests", [manifest_key(day_2)])

    result = process_ready_runs(empty_db)

    assert result == {"outcome": "processed", "runs": [day_2["run_id"]], "observations": 2, "quarantined": 0}
    assert current(empty_db) == [("700", "Closed"), ("701", "Open")]
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

    assert process_ready_runs(empty_db) == {"outcome": "waiting", "blocked_files": 2, "pending_rows": 3, "ready_runs": []}
    assert current(empty_db) == []

    # The manifest arrives; the same Observations (still pending) now flow.
    put_collected(empty_db, bucket, "manifests", [manifest_key(manifest)])
    assert process_ready_runs(empty_db)["observations"] == 3
    assert current(empty_db) == [("800", "Open"), ("801", "Open"), ("802", "Open")]


def test_a_partly_loaded_run_waits_until_every_file_and_row_is_in(empty_db, tmp_path):
    bucket, manifest = two_file_run(tmp_path)
    first, second = data_keys(manifest)
    put_collected(empty_db, bucket, "data", [first])
    load_observation_files(empty_db)
    put_collected(empty_db, bucket, "manifests", [manifest_key(manifest)])

    assert process_ready_runs(empty_db)["outcome"] == "waiting"
    assert current(empty_db) == []
    assert pipeline_runs(empty_db) == [(manifest["run_id"], "waiting", 3, 2, None)]

    put_collected(empty_db, bucket, "data", [second])
    load_observation_files(empty_db)

    assert process_ready_runs(empty_db)["outcome"] == "processed"
    assert pipeline_runs(empty_db) == [(manifest["run_id"], "consumed", 3, 3, 3)]


def test_a_consumed_run_is_never_processed_again(empty_db, tmp_path):
    bucket, manifest = two_file_run(tmp_path)
    put_collected(empty_db, bucket, "data", data_keys(manifest))
    load_observation_files(empty_db)
    put_collected(empty_db, bucket, "manifests", [manifest_key(manifest)])
    process_ready_runs(empty_db)

    # Bulk-loading the same files again adds nothing to RAW; the check is idle.
    load_observation_files(empty_db)
    assert process_ready_runs(empty_db) == {"outcome": "idle"}
    [row] = rows_of(empty_db, "SELECT COUNT(*) AS n FROM RAW.RAW_311_OBSERVATIONS")
    assert row["n"] == 3
    assert pipeline_runs(empty_db) == [(manifest["run_id"], "consumed", 3, 3, 3)]


def test_bulk_load_skips_files_snowpipe_already_loaded(empty_db, tmp_path):
    bucket, manifest = two_file_run(tmp_path)
    put_collected(empty_db, bucket, "data", data_keys(manifest))
    put_collected(empty_db, bucket, "manifests", [manifest_key(manifest)])

    # Load exactly as the pipe does: its own COPY statement, against the fixture stage.
    [ddl] = empty_db.cursor().execute("SELECT GET_DDL('PIPE', 'RAW.OBSERVATIONS_PIPE')").fetchone()  # type: ignore[misc]
    copy = re.search(r"(?is)\bas\s+(copy\s+into.*)", ddl).group(1)  # type: ignore[union-attr]
    empty_db.cursor().execute(copy.replace("@RAW.OBSERVATIONS_STAGE", STAGES.observations.rstrip("/")))

    counts = run(empty_db, STAGES)

    assert counts["observations_loaded"] == 0
    [row] = rows_of(empty_db, "SELECT COUNT(*) AS n, COUNT(DISTINCT source_file) AS files FROM RAW.RAW_311_OBSERVATIONS")
    assert (row["n"], row["files"]) == (3, 2)
    assert current(empty_db) == [("800", "Open"), ("801", "Open"), ("802", "Open")]


def test_the_pipe_reads_only_the_data_prefix(test_env):
    conn, _ = test_env
    [pipe] = rows_of(conn, "SHOW PIPES LIKE 'OBSERVATIONS_PIPE' IN SCHEMA RAW")
    [stage] = rows_of(conn, "SHOW STAGES LIKE 'OBSERVATIONS_STAGE' IN SCHEMA RAW")
    assert "@RAW.OBSERVATIONS_STAGE" in pipe["definition"]
    assert stage["url"].endswith("/data/")
    assert rows_of(conn, f"SHOW PIPES LIKE '%MANIFEST%' IN DATABASE") == []
    assert FIXTURE_STAGE not in pipe["definition"]
