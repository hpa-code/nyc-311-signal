"""Seam 2: the examples a model is trained and evaluated on, and the evaluation
the Model Performance page reads. Fixture Requests are published (so their
Labels are recorded), then snapshotted into ML.MODEL_EXAMPLES for a split
plan. Metric values are measured, not asserted."""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from ml.examples import SplitPlan, snapshot_examples
from ml.promote import approved_model, promote
from ml.train import evaluate
from warehouse.pipeline import run
from warehouse.tests.fixtures import STAGES, source_row, stage_collector_run
from warehouse.tests.test_warehouse import rows_of

FIRST_PASS = datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)
SECOND_PASS = datetime(2026, 9, 25, 6, 0, tzinfo=timezone.utc)

# Creation-time windows in UTC, each 240 h (the horizon plus the 72 h
# allowance) after the one before.
PLAN = SplitPlan(
    train=(datetime(2026, 7, 1), datetime(2026, 7, 11)),
    validation=(datetime(2026, 7, 21), datetime(2026, 7, 31)),
    test=(datetime(2026, 8, 10), datetime(2026, 8, 20)),
)
# Fields a model may see: known when the Request was submitted.
FEATURE_COLUMNS = {"problem", "agency", "borough", "channel", "created_hour", "created_weekday", "created_month"}
POST_SUBMISSION = {"status", "closed_date", "closed_at_local", "closed_at_utc", "resolution_description", "hours_to_close"}


def closed(key: str, created: str, closed_date: str, **fields) -> dict:
    return source_row(key, created, status="Closed", closed_date=closed_date, **fields)


def examples(conn, model_version: str) -> dict[str, tuple]:
    rows = rows_of(conn, f"SELECT unique_key, split, label FROM ML.MODEL_EXAMPLES WHERE model_version = '{model_version}'")
    return {r["unique_key"]: (r["split"], r["label"]) for r in rows}


def columns(conn, relation: str) -> set[str]:
    return {r["name"].lower() for r in rows_of(conn, f"DESCRIBE TABLE {relation}")}


def test_examples_split_by_creation_time_with_a_gap_and_only_labels_known_at_the_cutoff(empty_db, tmp_path):
    # New York times; UTC is 4 hours later.
    first = [
        closed("100", "2026-07-02T08:00:00.000", "2026-07-03T08:00:00.000"),
        source_row("101", "2026-07-05T08:00:00.000"),
        source_row("102", "2026-07-10T19:59:59.000", open_data_channel_type="ONLINE"),  # 1 s before train ends
        source_row("103", "2026-07-10T20:00:00.000"),  # train end: in the gap
        source_row("104", "2026-07-20T20:00:00.000"),  # validation start
        closed("105", "2026-07-25T08:00:00.000", "2026-07-26T08:00:00.000"),
        source_row("106", "2026-08-11T08:00:00.000"),
        source_row("107", "2026-08-12T08:00:00.000", updated_at="2026-09-19T01:33:00.000Z"),  # Closed in the second pass
        source_row("108", "2026-08-12T08:00:00.000", agency="NYPD", complaint_type="Noise - Residential"),
        closed("109", "2026-08-13T08:00:00.000", "2026-08-13T07:00:00.000"),  # Closure before creation: no Label
        closed("110", "2026-08-14T08:00:00.000", "2026-08-15T08:00:00.000", updated_at="2026-09-19T01:33:00.000Z"),
        source_row("111", "2026-08-25T08:00:00.000"),  # after the test window
    ]
    stage_collector_run(empty_db, tmp_path, "first", first, date(2026, 7, 1), 60, FIRST_PASS)
    run(empty_db, STAGES)
    second = [
        closed("107", "2026-08-12T08:00:00.000", "2026-09-24T08:00:00.000", updated_at="2026-09-24T01:33:00.000Z"),
        source_row("110", "2026-08-14T08:00:00.000", updated_at="2026-09-24T01:33:00.000Z"),  # reopened
    ]
    stage_collector_run(empty_db, tmp_path, "second", second, date(2026, 8, 12), 3, SECOND_PASS)
    run(empty_db, STAGES)

    # Labels known on 09-20: 110 was then Closed in time.
    counts = snapshot_examples(empty_db, "known-0920", PLAN, label_cutoff=datetime(2026, 9, 20, 6, 0))
    known_0920 = {
        "100": ("train", 0), "101": ("train", 1), "102": ("train", 1),
        "104": ("validation", 1), "105": ("validation", 0),
        "106": ("test", 1), "107": ("test", 1), "110": ("test", 0),
    }
    assert examples(empty_db, "known-0920") == known_0920
    assert counts == {"train": 3, "validation": 2, "test": 3}
    # Training and evaluation IDs never overlap: one row per Request per model version.
    [row] = rows_of(empty_db, "SELECT COUNT(*) AS n, COUNT(DISTINCT unique_key) AS keys FROM ML.MODEL_EXAMPLES WHERE model_version = 'known-0920'")
    assert row["n"] == row["keys"] == 8

    # By 09-25 110 is a Reopened Request, excluded; 107 has two Request
    # Versions and is still one example.
    snapshot_examples(empty_db, "known-0925", PLAN, label_cutoff=datetime(2026, 9, 25, 6, 0))
    assert examples(empty_db, "known-0925") == {k: v for k, v in known_0920.items() if k != "110"}

    # Features are only fields known at submission, in New York time.
    [row] = rows_of(empty_db, "SELECT * FROM ML.MODEL_EXAMPLES WHERE model_version = 'known-0920' AND unique_key = '102'")
    assert {k: row[k] for k in FEATURE_COLUMNS} == {
        "problem": "Dirty Condition", "agency": "DSNY", "borough": "BROOKLYN", "channel": "ONLINE",
        "created_hour": 19, "created_weekday": 5, "created_month": 7,
    }
    assert columns(empty_db, "ML.FEATURES") == {"unique_key", "created_at_utc"} | FEATURE_COLUMNS
    assert not columns(empty_db, "ML.MODEL_EXAMPLES") & POST_SUBMISSION


def test_a_split_plan_without_the_gap_is_refused():
    with pytest.raises(ValueError, match="240 h"):
        SplitPlan(
            train=(datetime(2026, 7, 1), datetime(2026, 7, 11)),
            validation=(datetime(2026, 7, 20, 23), datetime(2026, 7, 31)),
            test=(datetime(2026, 8, 10), datetime(2026, 8, 20)),
        )


def test_an_evaluation_reports_every_baseline_and_the_model_on_the_test_period_once(empty_db, tmp_path):
    # Per day in each window: Dirty Condition Closed the next day (0), Illegal
    # Dumping left open (1). The test period adds a Problem training never saw.
    rows, n = [], 0
    for split, (start, end) in (("train", PLAN.train), ("validation", PLAN.validation), ("test", PLAN.test)):
        per_problem = 4 if split == "train" else 2
        for day in range((end - start).days):
            created = start + timedelta(days=day, hours=14)  # 10:00 New York
            for _ in range(per_problem):
                n += 1
                rows.append(closed(str(n), f"{created:%Y-%m-%dT%H:%M:%S}.000", f"{created + timedelta(days=1):%Y-%m-%dT%H:%M:%S}.000"))
                n += 1
                rows.append(source_row(str(n), f"{created:%Y-%m-%dT%H:%M:%S}.000", complaint_type="Illegal Dumping"))
    for _ in range(3):
        n += 1
        rows.append(source_row(str(n), "2026-08-15T10:00:00.000", complaint_type="Graffiti"))
    stage_collector_run(empty_db, tmp_path, "model", rows, date(2026, 7, 1), 60, FIRST_PASS)
    run(empty_db, STAGES)

    version = evaluate(empty_db, PLAN, label_cutoff=datetime(2026, 9, 20, 6, 0))

    [evaluation] = rows_of(empty_db, "SELECT * FROM APP.MODEL_EVALUATIONS")
    assert evaluation["model_version"] == version
    assert evaluation["feature_version"] == "features-v1"
    assert evaluation["label_cutoff"] == datetime(2026, 9, 20, 6, 0)
    splits = json.loads(evaluation["splits"])
    assert {name: splits[name]["requests"] for name in ("train", "validation", "test")} == {"train": 80, "validation": 40, "test": 43}
    assert splits["test"]["unseen_problem_requests"] == 3
    assert json.loads(evaluation["selection"])["chosen"] == evaluation["chosen_model"]

    metrics = rows_of(empty_db, "SELECT * FROM APP.MODEL_METRICS")
    overall = {(m["split"], m["scorer"]) for m in metrics if m["slice_kind"] == "overall"}
    baselines = {"constant_prevalence", "historical_problem", "agency_only"}
    candidates = {"logistic_regression", "gradient_boosting"}
    assert {s for split, s in overall if split == "validation"} == baselines | candidates
    # The test period scores the baselines and only the chosen model.
    assert {s for split, s in overall if split == "test"} == baselines | {evaluation["chosen_model"]}
    for kind in ("agency", "problem", "borough"):
        sliced = [m for m in metrics if m["split"] == "test" and m["scorer"] == "agency_only" and m["slice_kind"] == kind]
        assert sum(m["requests"] for m in sliced) == 43

    # The warehouse reproduces the trained model's scores, so it can be promoted.
    promote(empty_db, version)
    assert approved_model(empty_db) == version
