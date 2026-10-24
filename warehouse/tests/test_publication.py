"""Seam 2: Publications. The dashboard shows one complete, validated
Publication; a failed check or a partial run never replaces it."""

import json
from datetime import date, datetime, timezone

import pytest
from snowflake.connector.errors import ProgrammingError

from warehouse.pipeline import process_ready_runs, publish, run
from warehouse.tests.fixtures import STAGES, load_observation_files, put_collected, source_row, stage_collector_run
from warehouse.tests.test_readiness import data_keys, manifest_key, overview, two_file_run
from warehouse.tests.test_warehouse import rows_of


def status(conn) -> dict:
    [row] = rows_of(conn, "SELECT * FROM APP.PUBLICATION_STATUS")
    return row


def publish_one_request(conn, tmp_path, key: str) -> dict:
    """A bulk run of one Request, extracted now (so the data is fresh), published."""
    stage_collector_run(conn, tmp_path, f"run-{key}", [source_row(key, "2026-09-01T08:00:00.000")], date(2026, 9, 1), 1, datetime.now(timezone.utc))
    assert run(conn, STAGES)["publication"] == "published"
    return status(conn)


def test_a_run_publishes_and_an_unchanged_rerun_keeps_its_publication(empty_db, tmp_path):
    assert status(empty_db)["stale_reason"] == "no Publication yet"

    published = publish_one_request(empty_db, tmp_path, "900")

    assert published["publication_id"] is not None
    assert (published["latest_attempt_state"], published["stale"], published["stale_reason"]) == ("published", False, "")
    assert overview(empty_db) == [(date(2026, 9, 1), 1, 0)]
    assert run(empty_db, STAGES)["publication"] == "unchanged"
    assert status(empty_db)["publication_id"] == published["publication_id"]


def test_a_failed_check_holds_and_the_previous_publication_stays_visible(empty_db, tmp_path):
    previous = publish_one_request(empty_db, tmp_path, "910")
    # Break an invariant the checks guard: two Current Requests for one unique_key.
    empty_db.cursor().execute("INSERT INTO CLEAN.SERVICE_REQUESTS SELECT * FROM CLEAN.SERVICE_REQUESTS")

    with pytest.raises(ProgrammingError, match="Publication held"):
        publish(empty_db)
    # Asking again changes nothing and still fails.
    with pytest.raises(ProgrammingError, match="Publication held"):
        publish(empty_db)

    held = status(empty_db)
    assert held["publication_id"] == previous["publication_id"]
    assert (held["latest_attempt_state"], held["stale"]) == ("held", True)
    assert held["stale_reason"] == "the latest Publication attempt failed its checks"
    assert overview(empty_db) == [(date(2026, 9, 1), 1, 0)]
    [checks] = rows_of(empty_db, "SELECT checks FROM OPS.PUBLICATIONS WHERE state = 'held'")
    failed = [name for name, passed in json.loads(checks["checks"]).items() if not passed]
    assert failed == ["one_current_request_per_unique_key"]

    # Once fixed, the next publish succeeds and the warning clears.
    empty_db.cursor().execute("INSERT OVERWRITE INTO CLEAN.SERVICE_REQUESTS SELECT DISTINCT * FROM CLEAN.SERVICE_REQUESTS")
    assert publish(empty_db)["outcome"] == "published"
    recovered = status(empty_db)
    assert recovered["publication_id"] != previous["publication_id"]
    assert (recovered["latest_attempt_state"], recovered["stale"]) == ("published", False)


def test_a_partial_run_never_becomes_the_current_publication(empty_db, tmp_path):
    previous = publish_one_request(empty_db, tmp_path, "920")
    bucket, manifest = two_file_run(tmp_path)
    first, _ = data_keys(manifest)
    put_collected(empty_db, bucket, "data", [first])
    load_observation_files(empty_db)
    put_collected(empty_db, bucket, "manifests", [manifest_key(manifest)])

    assert process_ready_runs(empty_db)["outcome"] == "waiting"
    assert publish(empty_db) == {"outcome": "waiting"}

    assert status(empty_db)["publication_id"] == previous["publication_id"]
    assert overview(empty_db) == [(date(2026, 9, 1), 1, 0)]


def test_old_data_is_published_but_flagged_stale(empty_db, tmp_path):
    stage_collector_run(empty_db, tmp_path, "old", [source_row("930", "2026-09-01T08:00:00.000")], date(2026, 9, 1), 1, datetime(2026, 9, 10, 6, 0, tzinfo=timezone.utc))
    run(empty_db, STAGES)

    old = status(empty_db)
    assert (old["latest_attempt_state"], old["stale"], old["stale_reason"]) == ("published", True, "the data is more than 36 hours old")
