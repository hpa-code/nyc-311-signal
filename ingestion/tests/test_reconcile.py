"""Seam 1: the weekly reconciliation pass. It re-fetches older creation-date
partitions through the auto-ingested prefix, so the warehouse can find
changes the change cursor missed."""

from datetime import date, datetime, timedelta, timezone

import pytest

from ingestion.api_client import CreationWindow
from ingestion.collector import CursorPlan
from ingestion.lambda_handler import plan_for, run_pass
from ingestion.object_store import LocalObjectStore
from ingestion.reconcile import reconcile_plan
from ingestion.tests.fake_socrata import FakeSocrata
from ingestion.tests.test_collector import make_collector, read_observations, request

# Sunday 2026-10-25, in the ISO week starting Monday 2026-10-19.
SUNDAY = datetime(2026, 10, 25, 8, 0, tzinfo=timezone.utc)


def windows(plan) -> list[tuple[datetime, datetime]]:
    assert all(isinstance(w, CreationWindow) for w in plan.partitions)
    return [(w.start, w.end) for w in plan.partitions]


@pytest.fixture
def store(tmp_path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "bucket")


def test_a_week_sweeps_the_eight_weeks_before_it_and_one_older_month_in_turn():
    plan = reconcile_plan(SUNDAY.date())
    assert plan.run_id == "reconcile-2026W43"
    assert plan.channel == "daily"
    days = windows(plan)[:56]
    assert days[0] == (datetime(2026, 8, 24), datetime(2026, 8, 25))
    assert days[-1] == (datetime(2026, 10, 18), datetime(2026, 10, 19))
    assert all(end == start + timedelta(days=1) for start, end in days)
    # Months 2025-01 to 2026-07 lie wholly before the days: 19 of them. This
    # is week 93 since the first Monday of 2025, and 93 % 19 = 17: 2026-06.
    assert windows(plan)[56:] == [(datetime(2026, 6, 1), datetime(2026, 7, 1))]
    # The next week moves on to the next month.
    assert windows(reconcile_plan(date(2026, 10, 26)))[56:] == [(datetime(2026, 7, 1), datetime(2026, 8, 1))]


def test_any_day_of_the_week_gives_the_same_run_and_only_sunday_starts_one(store):
    assert reconcile_plan(date(2026, 10, 19)) == reconcile_plan(SUNDAY.date())
    assert plan_for({"plan": "reconcile"}, SUNDAY.date(), store) == reconcile_plan(SUNDAY.date())
    assert plan_for({"plan": "reconcile"}, date(2026, 10, 21), store) is None
    assert isinstance(plan_for({}, date(2026, 10, 21), store), CursorPlan)


def test_an_unfinished_pass_is_resumed_the_next_day_not_the_next_week(store):
    # Sunday's pass stops unfinished and its continuation is lost. Its files
    # would block processing until its manifest exists.
    collector = make_collector(FakeSocrata([request("1", "2026-09-01T08:00:00.000")]), store, clock=lambda: SUNDAY)
    sunday_plan = plan_for({"plan": "reconcile"}, SUNDAY.date(), store)
    assert run_pass({"plan": "reconcile"}, collector, sunday_plan, SUNDAY, lambda payload: None)["outcome"] == "paused"

    monday = date(2026, 10, 26)
    assert plan_for({"plan": "reconcile"}, monday, store) == sunday_plan
    finished = run_pass({"plan": "reconcile"}, collector, sunday_plan, SUNDAY + timedelta(days=1), lambda payload: None)
    assert finished["outcome"] == "complete"
    assert plan_for({"plan": "reconcile"}, monday, store) is None
    assert plan_for({"plan": "reconcile"}, date(2026, 11, 1), store) == reconcile_plan(date(2026, 11, 1))


def test_the_pass_collects_its_partitions_only_and_records_when_it_started(store):
    rows = [
        request("1", "2026-08-24T00:00:00.000"),  # first swept day
        request("2", "2026-10-18T23:59:59.000"),  # last swept day
        request("3", "2026-10-19T00:00:00.000"),  # this week: the cursor's
        request("4", "2026-06-15T08:00:00.000"),  # this week's older month
        request("5", "2026-05-15T08:00:00.000"),  # another month's turn
    ]
    moments = iter([SUNDAY + timedelta(seconds=s) for s in range(1000)])
    collector = make_collector(FakeSocrata(rows), store, clock=lambda: next(moments))
    plan = plan_for({"plan": "reconcile"}, SUNDAY.date(), store)
    assert plan is not None
    continued: list[dict] = []

    paused = run_pass({"plan": "reconcile"}, collector, plan, SUNDAY, continued.append)
    assert paused["outcome"] == "paused" and continued == [{"plan": "reconcile", "continuation": 1}]
    done = run_pass(continued[0], collector, plan, SUNDAY + timedelta(hours=1), continued.append)
    assert done["outcome"] == "complete" and done["run_id"] == "reconcile-2026W43"

    manifest = collector.run(plan)
    assert sorted(o["payload"]["unique_key"] for o in read_observations(store, manifest)) == ["1", "2", "4"]
    assert all(f["key"].startswith("data/daily/") for f in manifest["files"])
    assert manifest["started_at"] <= manifest["completed_at"]
