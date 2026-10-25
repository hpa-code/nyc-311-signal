"""The Reconciliation Pass: re-fetch older creation-date partitions so the
warehouse can find changes the change cursor missed (a Request whose
`:updated_at` did not move, or one changed before the cursor started).

Each week sweeps the 56 creation days before its Monday, plus one older
month in turn. As months are added the turn shifts, so a month may wait
longer than the number of months for its next visit. The files go to
data/daily/ like a cursor pass: Snowpipe loads them, the readiness check
processes them, and any change they reveal is both recorded
(OPS.RUN_METRICS.missed_changes) and taken in.

A pass's files block processing until its manifest arrives, so an
unfinished pass must not wait a week: it is resumed the next day.
"""

import json
from datetime import date, timedelta

from ingestion.collector import SCOPE_START, Plan, creation_day_partitions, creation_month_partitions, run_state_key
from ingestion.object_store import ObjectStore

SWEPT_DAYS = 56
# Older months are taken in turn, counting weeks from this Monday.
FIRST_MONDAY = date(2025, 1, 6)
SUNDAY = 6


def reconcile_plan(today: date) -> Plan:
    """The pass for `today`'s ISO week: every day of the week gives the same
    run ID and partitions, so a continuation resumes it."""
    monday = today - timedelta(days=today.weekday())
    first_day = monday - timedelta(days=SWEPT_DAYS)
    days = creation_day_partitions(first_day, SWEPT_DAYS)
    # Months wholly before the swept days.
    older = creation_month_partitions(SCOPE_START.date(), first_day.replace(day=1) - timedelta(days=1))
    month = older[((monday - FIRST_MONDAY).days // 7) % len(older)]
    year, week, _ = monday.isocalendar()
    return Plan(run_id=f"reconcile-{year}W{week:02d}", partitions=[*days, month], channel="daily")


def due_reconcile_plan(store: ObjectStore, today: date) -> Plan | None:
    """The pass to run today: an unfinished one from this week or the last,
    else, on a Sunday, this week's if it has not run. None otherwise."""
    for day in (today, today - timedelta(weeks=1)):
        plan = reconcile_plan(day)
        stored = store.get(run_state_key(plan.run_id))
        if stored is not None and json.loads(stored)["state"] != "complete":
            return plan
    this_week = reconcile_plan(today)
    if today.weekday() == SUNDAY and store.get(run_state_key(this_week.run_id)) is None:
        return this_week
    return None
