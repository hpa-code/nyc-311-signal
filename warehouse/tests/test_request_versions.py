"""Seam 2: duplicates, out-of-order arrival, replay and quarantine, seen through APP views."""

from datetime import date, datetime, timezone

from warehouse.pipeline import run
from warehouse.tests.fixtures import STAGES, observation, payload, stage_observation_file
from warehouse.tests.test_warehouse import rows_of

DAY_1 = datetime(2026, 9, 2, 6, 0, tzinfo=timezone.utc)
DAY_2 = datetime(2026, 9, 3, 6, 0, tzinfo=timezone.utc)
DAY_3 = datetime(2026, 9, 4, 6, 0, tzinfo=timezone.utc)
T1, T2, T3 = "2026-09-02T01:33:00.000Z", "2026-09-03T01:33:00.000Z", "2026-09-04T01:33:00.000Z"

OPEN = payload("500")
CLOSED = payload("500", status="Closed", closed_date="2026-09-02T15:00:00.000")


def overview(conn) -> list[tuple]:
    return [
        (r["created_date"], r["requests"], r["closed_requests"], r["open_requests"])
        for r in rows_of(conn, "SELECT * FROM APP.OVERVIEW_DAILY ORDER BY created_date")
    ]


def coverage(conn) -> dict:
    [row] = rows_of(
        conn,
        "SELECT observations_loaded, distinct_observations, request_versions, current_requests, quarantined_observations"
        " FROM APP.DATA_COVERAGE",
    )
    return row


def test_overlapping_duplicates_collapse_into_one_request_version(empty_db, tmp_path):
    # The 6-hour cursor overlap fetches the same unchanged state twice.
    stage_observation_file(empty_db, tmp_path, "run-1", [observation(OPEN, "run-1", DAY_1, updated_at=T1)])
    stage_observation_file(empty_db, tmp_path, "run-2", [observation(OPEN, "run-2", DAY_2, updated_at=T1)])

    run(empty_db, STAGES)

    assert coverage(empty_db) == {
        "observations_loaded": 2, "distinct_observations": 2, "request_versions": 1, "current_requests": 1,
        "quarantined_observations": 0,
    }
    assert overview(empty_db) == [(date(2026, 9, 1), 1, 0, 1)]


def test_an_older_observation_arriving_after_a_newer_one_never_overwrites_it(empty_db, tmp_path):
    stage_observation_file(empty_db, tmp_path, "newer", [observation(CLOSED, "run-2", DAY_2, updated_at=T2)])
    run(empty_db, STAGES)

    stage_observation_file(empty_db, tmp_path, "older-late", [observation(OPEN, "run-1", DAY_1, updated_at=T1)])
    run(empty_db, STAGES)

    assert overview(empty_db) == [(date(2026, 9, 1), 1, 1, 0)]
    assert coverage(empty_db)["request_versions"] == 2


def test_replaying_an_old_observation_leaves_the_current_request_and_versions_unchanged(empty_db, tmp_path):
    stage_observation_file(empty_db, tmp_path, "run-1", [observation(OPEN, "run-1", DAY_1, updated_at=T1)])
    stage_observation_file(empty_db, tmp_path, "run-2", [observation(CLOSED, "run-2", DAY_2, updated_at=T2)])
    run(empty_db, STAGES)
    before = coverage(empty_db)

    # Replay keeps the original extraction time, in a new file.
    stage_observation_file(empty_db, tmp_path, "replay-of-run-1", [observation(OPEN, "run-1", DAY_1, updated_at=T1)])
    run(empty_db, STAGES)

    assert overview(empty_db) == [(date(2026, 9, 1), 1, 1, 0)]
    after = coverage(empty_db)
    assert after["request_versions"] == before["request_versions"] == 2
    assert after["distinct_observations"] == before["distinct_observations"] == 2  # the replay adds nothing
    assert after["observations_loaded"] == 3


def test_a_state_that_returns_is_a_new_version_even_when_a_late_change_lands_in_between(empty_db, tmp_path):
    # Open at T1, still Open (unchanged) at T3: one version so far.
    stage_observation_file(empty_db, tmp_path, "a", [observation(OPEN, "run-1", DAY_1, updated_at=T1)])
    stage_observation_file(empty_db, tmp_path, "a-again", [observation(OPEN, "run-3", DAY_3, updated_at=T3)])
    run(empty_db, STAGES)
    assert coverage(empty_db)["request_versions"] == 1

    # A Closed state from T2 arrives late: Open -> Closed -> Open.
    stage_observation_file(empty_db, tmp_path, "b-late", [observation(CLOSED, "run-2", DAY_2, updated_at=T2)])
    run(empty_db, STAGES)

    assert coverage(empty_db)["request_versions"] == 3
    assert overview(empty_db) == [(date(2026, 9, 1), 1, 0, 1)]  # latest state is Open


def test_malformed_observations_are_quarantined_with_reasons_and_the_rest_still_load(empty_db, tmp_path):
    good = observation(payload("600"), "run-1", DAY_1, updated_at=T1)
    bad = [
        observation(payload(None), "run-1", DAY_1, updated_at=T1),                                   # no unique_key
        observation(payload("601", created="not a date"), "run-1", DAY_1, updated_at=T1),           # bad created_date
        observation(payload("602", created=None), "run-1", DAY_1, updated_at=T1),                   # no created_date
        observation(payload(603), "run-1", DAY_1, updated_at=T1),                                    # number, not text
        observation(payload("604", closed_date="yesterday"), "run-1", DAY_1, updated_at=T1),        # bad closed_date
        observation(payload("605"), "run-1", DAY_1, updated_at=None),                                # no :updated_at
        {**observation(payload("606"), "run-1", DAY_1, updated_at=T1), "extracted_at": "soon"},     # bad extraction time
        {**observation(payload("607"), "run-1", DAY_1, updated_at=T1), "payload": "not an object"},
        observation(payload("608", created={"date": "2026-09-01"}), "run-1", DAY_1, updated_at=T1),  # object, not text
        observation(payload("609", created="1693526400"), "run-1", DAY_1, updated_at=T1),            # epoch-looking text
        observation(payload("610", closed_date=["2026-09-02"]), "run-1", DAY_1, updated_at=T1),      # array, not text
        observation(payload(["611"]), "run-1", DAY_1, updated_at=T1),                                # array key
    ]
    # Envelope fields of the wrong type load as NULL instead of failing the file.
    odd_envelope = {**observation(payload("612"), "run-1", DAY_1, updated_at=T1), "run_id": {"not": "text"}}
    stage_observation_file(empty_db, tmp_path, "mixed", [good, odd_envelope, *bad])

    run(empty_db, STAGES)

    assert overview(empty_db) == [(date(2026, 9, 1), 2, 0, 2)]
    summary = {r["reason"]: r["observations"] for r in rows_of(empty_db, "SELECT * FROM APP.QUARANTINE_SUMMARY")}
    assert summary == {
        "missing_unique_key": 2,  # the non-object payload has no unique_key either
        "invalid_created_date": 2,
        "incompatible_created_date": 1,
        "missing_created_date": 2,
        "incompatible_unique_key": 2,
        "invalid_closed_date": 1,
        "incompatible_closed_date": 1,
        "missing_source_updated_at": 1,
        "invalid_extracted_at": 1,
        "incompatible_payload": 1,
    }
    assert coverage(empty_db)["quarantined_observations"] == 12


def test_unknown_and_missing_problems_are_kept(empty_db, tmp_path):
    stage_observation_file(
        empty_db, tmp_path, "problems",
        [
            observation(payload("700", complaint_type="Brand New Problem"), "run-1", DAY_1, updated_at=T1),
            observation(payload("701", complaint_type=None), "run-1", DAY_1, updated_at=T1),
        ],
    )

    run(empty_db, STAGES)

    assert overview(empty_db) == [(date(2026, 9, 1), 2, 0, 2)]
    assert coverage(empty_db)["quarantined_observations"] == 0
