"""Seam 1: the historical backfill, one partition per creation month."""

from datetime import date, datetime, timedelta, timezone

import pytest

from ingestion.backfill import backfill_plan, progress, run_backfill
from ingestion.collector import creation_month_partitions
from ingestion.lease import LeaseHeld
from ingestion.object_store import LocalObjectStore
from ingestion.tests.fake_socrata import FakeSocrata, SimulatedCrash
from ingestion.tests.test_collector import make_collector, read_observations, request
from ingestion.tests.test_cursor import hold_lease

STARTED = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
PLAN = backfill_plan(through=date(2025, 3, 1))


def three_months_of_requests() -> list[dict]:
    """Two Requests per month, plus each month's boundaries."""
    return [
        request("1", "2025-01-01T00:00:00.000"),
        request("2", "2025-01-31T23:59:59.000"),
        request("3", "2025-02-01T00:00:00.000"),
        request("4", "2025-02-14T12:00:00.000"),
        request("5", "2025-03-10T08:00:00.000"),
        request("6", "2025-03-31T23:59:59.000"),
        request("7", "2025-04-01T00:00:00.000"),  # after the last month
        request("0", "2024-12-31T23:59:59.000"),  # before scope
    ]


class Clock:
    """A clock that only moves when the backfill sleeps."""

    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def store(tmp_path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "bucket")


def test_month_partitions_cover_each_nyc_local_month_across_a_year_end():
    windows = creation_month_partitions(date(2025, 11, 15), date(2026, 2, 3))
    assert [(w.start, w.end) for w in windows] == [
        (datetime(2025, 11, 1), datetime(2025, 12, 1)),
        (datetime(2025, 12, 1), datetime(2026, 1, 1)),
        (datetime(2026, 1, 1), datetime(2026, 2, 1)),
        (datetime(2026, 2, 1), datetime(2026, 3, 1)),
    ]


def test_backfill_collects_every_request_from_2025_01_through_its_last_month_into_bulk(store):
    manifest = run_backfill(make_collector(FakeSocrata(three_months_of_requests()), store), PLAN)

    assert PLAN.run_id == "backfill-202501-202503"
    assert [p["row_count"] for p in manifest["partitions"]] == [2, 2, 2]
    assert sorted(o["payload"]["unique_key"] for o in read_observations(store, manifest)) == ["1", "2", "3", "4", "5", "6"]
    assert all(f["key"].startswith("data/bulk/") for f in manifest["files"])


def test_a_killed_backfill_waits_out_its_lease_then_resumes_only_incomplete_months(store, tmp_path):
    reference = run_backfill(
        make_collector(FakeSocrata(three_months_of_requests()), LocalObjectStore(tmp_path / "reference"), page_size=1, clock=lambda: STARTED),
        PLAN,
    )

    clock = Clock(STARTED)
    fake = FakeSocrata(three_months_of_requests())
    fake.crash_on_request(5)  # January: 2 pages + the empty page; February: 1 page, then killed
    with pytest.raises(SimulatedCrash):
        run_backfill(make_collector(fake, store, page_size=1, clock=clock), PLAN, sleep=clock.sleep)
    assert [m["state"] for m in progress(store, PLAN)] == ["complete", "in_progress", "not_started"]
    # A killed task never releases its lease.
    hold_lease(store, f"run-{PLAN.run_id}", "killed", until=STARTED + timedelta(minutes=16))

    fake.requests.clear()
    resumed = run_backfill(make_collector(fake, store, page_size=1, clock=clock), PLAN, sleep=clock.sleep)

    assert clock.now >= STARTED + timedelta(minutes=16)
    assert [f["key"] for f in resumed["files"]] == [f["key"] for f in reference["files"]]
    assert resumed["row_count"] == 6
    # February continues after its stored page; January is never refetched.
    first_page = fake.page_requests()[0]
    assert "2025-02-01T00:00:00" in first_page["$where"] and "unique_key > '3'" in first_page["$where"]
    assert len(fake.page_requests()) == 2 + 3  # February: 1 page + empty; March: 2 pages + empty
    assert [m["state"] for m in progress(store, PLAN)] == ["complete"] * 3


def test_a_lease_still_held_after_the_wait_stops_the_backfill(store):
    clock = Clock(STARTED)
    hold_lease(store, f"run-{PLAN.run_id}", "running", until=STARTED + timedelta(hours=2))
    fake = FakeSocrata(three_months_of_requests())

    with pytest.raises(LeaseHeld, match="running"):
        run_backfill(make_collector(fake, store, clock=clock), PLAN, sleep=clock.sleep)
    assert fake.requests == []
