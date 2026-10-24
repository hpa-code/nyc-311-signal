"""Seam 1: the daily change-cursor pass, its overlap, and the collector lease."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from ingestion.collector import CursorMissing, CursorPlan, Plan, RunPaused, creation_day_partitions
from ingestion.lambda_handler import run_pass
from ingestion.lease import LeaseHeld
from ingestion.object_store import LocalObjectStore
from ingestion.tests.fake_socrata import FakeSocrata, SimulatedCrash
from ingestion.tests.test_collector import make_collector, read_observations, request

CURSOR_KEY = "checkpoints/cursor/311.json"
SEED = "2026-10-23T20:00:00.000Z"
PLAN = CursorPlan(initial_cursor=SEED)
DAY_1 = datetime(2026, 10, 24, 6, 0, tzinfo=timezone.utc)
DAY_2 = datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc)


def changed(key: str, updated_at: str, created: str = "2026-10-20T08:00:00.000", **fields) -> dict:
    return request(key, created, **{":updated_at": updated_at, **fields})


def hold_lease(store, name: str, owner: str, until: datetime) -> None:
    """A lease left by another collector, as it stores it."""
    record = {"owner": owner, "renewed_at": DAY_1.isoformat(), "expires_at": until.isoformat()}
    store.put(f"checkpoints/leases/{name}.json", json.dumps(record).encode())


def cursor_state(store) -> dict:
    return json.loads(store.get(CURSOR_KEY))


def keys(store, manifest) -> list[str]:
    return [o["payload"]["unique_key"] for o in read_observations(store, manifest)]


@pytest.fixture
def store(tmp_path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "bucket")


def test_first_pass_needs_a_cursor(store):
    with pytest.raises(CursorMissing):
        make_collector(FakeSocrata([]), store).run(CursorPlan())
    assert store.get(CURSOR_KEY) is None


def test_pass_fetches_changes_past_cursor_minus_overlap_within_scope(store):
    fake = FakeSocrata(
        [
            changed("1", "2026-10-23T13:59:59.999Z"),  # before seed - 6h
            changed("2", "2026-10-23T14:00:00.000Z"),  # exactly seed - 6h: excluded (strictly after)
            changed("3", "2026-10-23T14:00:00.001Z"),
            changed("4", "2026-10-24T01:33:15.966Z", status="Closed", closed_date="2026-10-23T11:00:00.000"),
            changed("5", "2026-10-24T01:33:15.966Z", created="2024-12-31T23:59:59.000"),  # out of scope
            changed("6", "2026-10-24T01:38:29.714Z", created="2025-01-01T00:00:00.000"),
        ]
    )

    manifest = make_collector(fake, store, clock=lambda: DAY_1).run(PLAN)

    assert keys(store, manifest) == ["3", "4", "6"]
    assert manifest["run_id"] == "cursor-20261024T060000Z"
    assert manifest["partitions"][0]["bounds"] == {
        "updated_after": "2026-10-23T14:00:00.000Z",
        "created_from": "2025-01-01T00:00:00",
    }
    state = cursor_state(store)
    assert state["updated_at"] == "2026-10-24T01:38:29.714Z"
    assert state["pending"] is None
    assert state["last_run_id"] == "cursor-20261024T060000Z"


def test_next_pass_overlaps_six_hours_and_loses_nothing(store):
    day_1 = [changed("10", "2026-10-24T01:33:15.966Z"), changed("11", "2026-10-24T01:38:00.000Z")]
    make_collector(FakeSocrata(day_1), store, clock=lambda: DAY_1).run(PLAN)

    # Overnight: 11 republished unchanged inside the overlap; 10 closed; 12 new.
    day_2 = [
        changed("10", "2026-10-25T01:33:10.000Z", status="Closed", closed_date="2026-10-24T09:00:00.000"),
        changed("11", "2026-10-24T01:38:00.000Z"),
        changed("12", "2026-10-25T01:33:10.000Z", created="2026-10-24T07:00:00.000"),
    ]
    manifest = make_collector(FakeSocrata(day_2), store, clock=lambda: DAY_2).run(PLAN)

    assert manifest["partitions"][0]["bounds"]["updated_after"] == "2026-10-23T19:38:00.000Z"
    observations = read_observations(store, manifest)
    assert [o["payload"]["unique_key"] for o in observations] == ["11", "10", "12"]
    assert observations[1]["payload"]["status"] == "Closed"
    assert cursor_state(store)["updated_at"] == "2026-10-25T01:33:10.000Z"


def test_page_boundaries_inside_an_updated_at_tie_neither_skip_nor_duplicate(store):
    batch = "2026-10-24T01:33:15.966Z"
    rows = [changed(k, batch) for k in ("205", "201", "204", "202", "203")]
    rows += [changed("300", "2026-10-23T22:00:00.000Z"), changed("100", "2026-10-24T01:38:00.000Z")]

    manifest = make_collector(FakeSocrata(rows), store, page_size=2, clock=lambda: DAY_1).run(PLAN)

    assert keys(store, manifest) == ["300", "201", "202", "203", "204", "205", "100"]
    assert [f["rows"] for f in manifest["files"]] == [2, 2, 2, 1]


def test_cursor_passes_write_to_the_auto_ingested_prefix_and_slices_to_the_bulk_one(store):
    cursor = make_collector(FakeSocrata([changed("1", "2026-10-24T01:33:15.966Z")]), store, clock=lambda: DAY_1).run(PLAN)
    september = creation_day_partitions(datetime(2026, 9, 1).date(), days=1)
    sliced = make_collector(FakeSocrata([request("2", "2026-09-01T08:00:00.000")]), store, clock=lambda: DAY_1).run(
        Plan("slice", september)
    )

    assert [f["key"].split("/")[1] for f in cursor["files"]] == ["daily"]
    assert [f["key"].split("/")[1] for f in sliced["files"]] == ["bulk"]


def test_an_empty_pass_completes_and_keeps_the_cursor(store):
    manifest = make_collector(FakeSocrata([]), store, clock=lambda: DAY_1).run(PLAN)

    assert manifest["row_count"] == 0 and manifest["files"] == []
    assert manifest["partitions"][0]["last_position"] is None
    assert cursor_state(store)["updated_at"] == SEED


def rows_for_three_pages() -> list[dict]:
    return [changed(f"{n}", f"2026-10-24T01:3{n}:00.000Z") for n in range(5)]


def test_cursor_stays_put_after_a_crash_and_the_resumed_pass_keeps_its_bounds_and_files(store, tmp_path):
    reference_store = LocalObjectStore(tmp_path / "reference")
    reference = make_collector(FakeSocrata(rows_for_three_pages()), reference_store, page_size=2, clock=lambda: DAY_1).run(PLAN)

    fake = FakeSocrata(rows_for_three_pages())
    fake.crash_on_request(2)
    with pytest.raises(SimulatedCrash):
        make_collector(fake, store, page_size=2, clock=lambda: DAY_1).run(PLAN)
    state = cursor_state(store)
    assert state["updated_at"] == SEED
    assert state["pending"]["run_id"] == "cursor-20261024T060000Z"

    # Resumed later (and after more source changes): same run, same lower bound.
    later = lambda: DAY_1 + timedelta(hours=3)
    fake.requests.clear()
    manifest = make_collector(fake, store, page_size=2, clock=later).run(PLAN)

    assert manifest["run_id"] == reference["run_id"]
    assert [f["key"] for f in manifest["files"]] == [f["key"] for f in reference["files"]]
    assert keys(store, manifest) == ["0", "1", "2", "3", "4"]
    assert len(fake.page_requests()) == 3  # page 1 was stored before the crash
    assert cursor_state(store)["updated_at"] == "2026-10-24T01:34:00.000Z"


class CrashOnCursorWrite:
    """Real local store that dies on the n-th write of the cursor file."""

    def __init__(self, inner: LocalObjectStore, crash_on_write: int):
        self._inner = inner
        self._remaining = crash_on_write

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def put(self, key, data):
        if key == CURSOR_KEY:
            self._remaining -= 1
            if self._remaining == 0:
                raise SimulatedCrash()
        self._inner.put(key, data)


def test_cursor_advances_only_after_the_manifest_is_stored_and_a_rerun_just_advances_it(store):
    # Write 1 records the pending pass; write 2 (the advance) dies.
    with pytest.raises(SimulatedCrash):
        make_collector(FakeSocrata(rows_for_three_pages()), CrashOnCursorWrite(store, 2), clock=lambda: DAY_1).run(PLAN)
    assert store.get("manifests/extract_date=2026-10-24/run_id=cursor-20261024T060000Z/manifest.json") is not None
    assert cursor_state(store)["updated_at"] == SEED

    fake = FakeSocrata(rows_for_three_pages())
    manifest = make_collector(fake, store, clock=lambda: DAY_2).run(PLAN)

    assert manifest["run_id"] == "cursor-20261024T060000Z"
    assert fake.requests == []
    assert cursor_state(store) | {"advanced_at": None} == {
        "updated_at": "2026-10-24T01:34:00.000Z",
        "pending": None,
        "last_run_id": "cursor-20261024T060000Z",
        "advanced_at": None,
    }


def test_a_pass_paused_at_its_deadline_resumes_where_it_stopped(store):
    moments = iter([DAY_1 + timedelta(seconds=s) for s in range(100)])
    clock = lambda: next(moments)
    fake = FakeSocrata(rows_for_three_pages())
    collector = make_collector(fake, store, page_size=2, clock=clock)

    with pytest.raises(RunPaused):
        collector.run(PLAN, stop_at=DAY_1 + timedelta(seconds=8))
    assert cursor_state(store)["updated_at"] == SEED
    fetched_before_pause = len(fake.page_requests())
    assert 0 < fetched_before_pause < 4

    manifest = collector.run(PLAN)

    assert len(fake.page_requests()) == 4  # 3 pages + the empty page, none twice
    assert keys(store, manifest) == ["0", "1", "2", "3", "4"]
    assert cursor_state(store)["pending"] is None


def test_a_running_collector_holds_off_a_second_one(store):
    second = make_collector(FakeSocrata([]), store, clock=lambda: DAY_1)
    blocked: list[Exception] = []

    class OverlappingSource(FakeSocrata):
        def __call__(self, url, params, headers):
            if not blocked:
                try:
                    second.run(PLAN)
                except LeaseHeld as err:
                    blocked.append(err)
            return super().__call__(url, params, headers)

    fake = OverlappingSource(rows_for_three_pages())
    manifest = make_collector(fake, store, clock=lambda: DAY_1).run(PLAN)

    assert len(blocked) == 1
    assert manifest["row_count"] == 5
    assert cursor_state(store)["updated_at"] == "2026-10-24T01:34:00.000Z"


def test_lease_is_released_after_success_and_after_failure(store):
    make_collector(FakeSocrata([]), store, clock=lambda: DAY_1).run(PLAN)
    failing = FakeSocrata([])
    failing.crash_on_request(1)
    a_minute_later = lambda: DAY_1 + timedelta(minutes=1)
    with pytest.raises(SimulatedCrash):
        make_collector(failing, store, clock=a_minute_later).run(PLAN)

    # Same moment, new collector: the lease is free (and it resumes the failed pass).
    manifest = make_collector(FakeSocrata([]), store, clock=a_minute_later).run(PLAN)
    assert manifest["run_id"] == "cursor-20261024T060100Z"


def test_a_new_pass_never_reuses_an_existing_run_id(store):
    make_collector(FakeSocrata([]), store, clock=lambda: DAY_1).run(PLAN)
    with pytest.raises(ValueError, match="already exists"):
        make_collector(FakeSocrata([]), store, clock=lambda: DAY_1).run(PLAN)
    assert cursor_state(store)["pending"] is None


def test_lease_left_by_a_killed_collector_blocks_until_it_expires(store):
    # A Lambda killed at its timeout never releases: simulate its lease.
    hold_lease(store, "cursor-311", "killed", until=DAY_1 + timedelta(minutes=16))
    fake = FakeSocrata(rows_for_three_pages())

    with pytest.raises(LeaseHeld, match="killed"):
        make_collector(fake, store, clock=lambda: DAY_1 + timedelta(minutes=10)).run(PLAN)
    assert fake.requests == []

    manifest = make_collector(fake, store, clock=lambda: DAY_1 + timedelta(minutes=17)).run(PLAN)
    assert manifest["row_count"] == 5


def test_partition_runs_lease_per_run_id(store):
    september = creation_day_partitions(datetime(2026, 9, 1).date(), days=1)
    hold_lease(store, "run-backfill", "other", until=DAY_1 + timedelta(hours=1))

    with pytest.raises(LeaseHeld):
        make_collector(FakeSocrata([]), store, clock=lambda: DAY_1).run(Plan("backfill", september))
    # A cursor pass is a different lease and runs alongside.
    make_collector(FakeSocrata([]), store, clock=lambda: DAY_1).run(PLAN)


def test_lambda_pass_continues_itself_when_paused_and_skips_when_another_holds_the_lease(store):
    moments = iter([DAY_1 + timedelta(seconds=s) for s in range(100)])
    collector = make_collector(FakeSocrata(rows_for_three_pages()), store, page_size=2, clock=lambda: next(moments))
    continued: list[dict] = []

    paused = run_pass({}, collector, PLAN, DAY_1 + timedelta(seconds=5), continued.append)
    assert paused["outcome"] == "paused" and continued == [{"continuation": 1}]

    done = run_pass(continued[0], collector, PLAN, DAY_1 + timedelta(hours=1), continued.append)
    assert done["outcome"] == "complete" and done["rows"] == 5 and len(continued) == 1

    hold_lease(store, "cursor-311", "other", until=DAY_2 + timedelta(hours=1))
    late = make_collector(FakeSocrata([]), store, clock=lambda: DAY_2)
    assert run_pass({}, late, PLAN, DAY_2 + timedelta(hours=1), continued.append)["outcome"] == "skipped"


def test_lambda_pass_gives_up_after_too_many_continuations(store):
    collector = make_collector(FakeSocrata(rows_for_three_pages()), store, page_size=2, clock=lambda: DAY_1)
    with pytest.raises(RuntimeError, match="continuations"):
        run_pass({"continuation": 12}, collector, PLAN, DAY_1, lambda payload: None)
