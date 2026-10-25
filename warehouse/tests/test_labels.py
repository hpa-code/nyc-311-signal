"""Seam 2: Labels and the Modeling Cohort, read from ML.TRAINING_LABELS (the
label versions training reads) and APP.PUBLISHED_LABEL_SUMMARY (the counts
beside the model)."""

from datetime import date, datetime, timezone

from warehouse.pipeline import run
from warehouse.tests.fixtures import STAGES, observation, payload, source_row, stage_collector_run, stage_observation_file
from warehouse.tests.test_analytics import published
from warehouse.tests.test_warehouse import rows_of

# 2026-09-20 06:00 UTC. With the 72 h allowance, a Request is matured when
# created at or before 2026-09-10 06:00 UTC (02:00 New York time).
OBSERVED = datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)
# Created 2026-09-01 08:00 New York; the horizon is 2026-09-08 08:00 New York.
CREATED = "2026-09-01T08:00:00.000"
DUPLICATE = "The Department of Transportation determined that this complaint is a duplicate of a previously filed complaint."


def labels(conn) -> dict[str, tuple]:
    """Each Request's latest label version."""
    rows = rows_of(
        conn,
        "SELECT unique_key, label, no_label_reason FROM ML.TRAINING_LABELS"
        " QUALIFY ROW_NUMBER() OVER (PARTITION BY unique_key ORDER BY observed_through DESC, recorded_at DESC) = 1",
    )
    return {r["unique_key"]: (r["label"], r["no_label_reason"]) for r in rows}


def versions(conn, key: str) -> list[tuple]:
    rows = rows_of(
        conn,
        f"SELECT label, no_label_reason, observed_through FROM ML.TRAINING_LABELS WHERE unique_key = '{key}' ORDER BY observed_through",
    )
    return [(r["label"], r["no_label_reason"], r["observed_through"]) for r in rows]


def closed(key: str, closed_date: str, created: str = CREATED, **fields) -> dict:
    return source_row(key, created, status="Closed", closed_date=closed_date, **fields)


def test_labels_follow_the_horizon_allowance_and_date_only_rules(empty_db, tmp_path):
    rows = [
        closed("900", "2026-09-08T08:00:00.000"),  # exactly 168 h
        closed("901", "2026-09-08T08:00:01.000"),  # one second later
        source_row("902", CREATED),  # confirmed open after the horizon
        source_row("903", "2026-09-10T02:00:00.000"),  # matured exactly at the extraction
        source_row("904", "2026-09-10T02:00:01.000"),  # one second short
        # Date-Only Closures, by New York calendar day against the horizon day (09-08).
        closed("905", "2026-09-07T00:00:00.000"),
        closed("906", "2026-09-08T00:00:00.000"),
        closed("907", "2026-09-09T00:00:00.000"),
        closed("908", "2026-09-01T00:00:00.000"),  # the creation day: not before creation
        # Exceptions.
        closed("910", "2026-09-01T07:59:59.000"),  # before creation
        closed("911", "2026-08-31T00:00:00.000"),  # Date-Only, the day before creation
        source_row("912", CREATED, status="Closed"),  # Closed with no Closure time
        source_row("913", CREATED, status="In Progress", closed_date="2026-09-02T08:00:00.000"),
        # A Duplicate Closure is an ordinary Closure, labeled by timing (ADR 0003).
        closed("914", "2026-09-01T09:00:00.000", resolution_description=DUPLICATE),
        closed("916", "2026-09-09T08:00:00.000", resolution_description=DUPLICATE),  # late: Delayed
        closed("918", "2026-09-01T09:00:00.000", resolution_description="This complaint is not a duplicate."),
        source_row("917", CREATED),  # its newer Observation is quarantined, below
        source_row("915", CREATED, agency="NYPD", complaint_type="Noise - Residential"),
    ]
    stage_collector_run(empty_db, tmp_path, "labels", rows, date(2026, 8, 31), 12, OBSERVED)
    unreadable = payload("917", CREATED, status="Closed", closed_date="2026-09-02")
    stage_observation_file(
        empty_db, tmp_path, "rejected", [observation(unreadable, "rejected", OBSERVED, updated_at="2026-09-19T01:33:00.000Z")]
    )
    run(empty_db, STAGES)

    assert labels(empty_db) == {
        "900": (0, None),
        "901": (1, None),
        "902": (1, None),
        "903": (1, None),
        "904": (None, "not_matured"),
        "905": (0, None),
        "906": (None, "date_only_closure_on_horizon_day"),
        "907": (1, None),
        "908": (0, None),
        "910": (None, "closure_before_creation"),
        "911": (None, "closure_before_creation"),
        "912": (None, "closed_without_closure_time"),
        "913": (None, "closure_time_while_open"),
        "914": (0, None),
        "916": (1, None),
        "917": (None, "quarantined_observation"),
        "918": (0, None),
    }

    summary = published(empty_db, "LABEL_SUMMARY", order="agency, label, no_label_reason")
    assert summary == [
        {"agency": "DSNY", "label": 0, "no_label_reason": None, "requests": 5, "duplicate_closures": 1},
        {"agency": "DSNY", "label": 1, "no_label_reason": None, "requests": 5, "duplicate_closures": 1},
        {"agency": "DSNY", "label": None, "no_label_reason": "closed_without_closure_time", "requests": 1, "duplicate_closures": 0},
        {"agency": "DSNY", "label": None, "no_label_reason": "closure_before_creation", "requests": 2, "duplicate_closures": 0},
        {"agency": "DSNY", "label": None, "no_label_reason": "closure_time_while_open", "requests": 1, "duplicate_closures": 0},
        {"agency": "DSNY", "label": None, "no_label_reason": "date_only_closure_on_horizon_day", "requests": 1, "duplicate_closures": 0},
        {"agency": "DSNY", "label": None, "no_label_reason": "not_matured", "requests": 1, "duplicate_closures": 0},
        {"agency": "DSNY", "label": None, "no_label_reason": "quarantined_observation", "requests": 1, "duplicate_closures": 0},
        {"agency": "NYPD", "label": None, "no_label_reason": "outside_modeling_cohort", "requests": 1, "duplicate_closures": 0},
    ]


def test_label_versions_wait_for_a_late_enough_observation_and_keep_every_correction(empty_db, tmp_path):
    # First seen by an extraction on 2026-09-05: old by the wall clock today,
    # but no Observation yet shows the outcome past the horizon plus allowance.
    early = datetime(2026, 9, 5, 6, 0, tzinfo=timezone.utc)
    first = [
        closed("950", "2026-09-02T08:00:00.000", updated_at="2026-09-03T01:33:00.000Z"),
        source_row("951", CREATED, updated_at="2026-09-03T01:33:00.000Z"),
    ]
    stage_collector_run(empty_db, tmp_path, "early", first, date(2026, 9, 1), 1, early)
    run(empty_db, STAGES)
    assert labels(empty_db) == {"950": (None, "not_matured"), "951": (None, "not_matured")}

    # A later pass re-fetches only what changed (here, another Request). Its
    # extraction confirms 950 Closed in time and 951 still open.
    later = datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)
    other = source_row("952", "2026-09-15T08:00:00.000", updated_at="2026-09-19T01:33:00.000Z")
    stage_collector_run(empty_db, tmp_path, "later", [other], date(2026, 9, 15), 1, later)
    run(empty_db, STAGES)
    assert labels(empty_db) == {"950": (0, None), "951": (1, None), "952": (None, "not_matured")}

    # 950 is then reopened. Its Label goes, but the version known on 09-20 stays.
    latest = datetime(2026, 9, 25, 6, 0, tzinfo=timezone.utc)
    reopened = source_row("950", CREATED, updated_at="2026-09-24T01:33:00.000Z", status="Open")
    stage_collector_run(empty_db, tmp_path, "latest", [reopened], date(2026, 9, 1), 1, latest)
    run(empty_db, STAGES)
    assert labels(empty_db)["950"] == (None, "reopened")
    assert versions(empty_db, "950") == [
        (None, "not_matured", datetime(2026, 9, 5, 6, 0)),
        (0, None, datetime(2026, 9, 20, 6, 0)),
        (None, "reopened", datetime(2026, 9, 25, 6, 0)),
    ]
    assert versions(empty_db, "951") == [(None, "not_matured", datetime(2026, 9, 5, 6, 0)), (1, None, datetime(2026, 9, 20, 6, 0))]
