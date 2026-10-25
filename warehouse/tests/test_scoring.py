"""Seam 2: model promotion and Admission Scores. Models are registered by
hand here with weights simple enough to score on paper: an Illegal Dumping
Request scores sigmoid(w), anything else sigmoid(0) = 0.5."""

import json
import math
from datetime import date, datetime, timezone

import pytest
from snowflake.connector.errors import ProgrammingError

from ml.promote import PromotionRefused, approved_model, promote
from warehouse.pipeline import publish, run
from warehouse.tests.fixtures import STAGES, source_row, stage_collector_run
from warehouse.tests.test_analytics import published
from warehouse.tests.test_publication import status
from warehouse.tests.test_warehouse import rows_of

FEATURES = ("problem", "agency", "borough", "channel", "created_hour", "created_weekday", "created_month")


def sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-z))


def register(conn, version: str, dumping_weight: float, *, kind: str = "logistic_regression", reference: float | None = None) -> None:
    """A registered model version: only Illegal Dumping has a weight, every
    other level reads as Other (weight 0). Its reference row is checked on promotion."""
    weights = {f: {"Other": 0.0} for f in FEATURES}
    weights["problem"]["Illegal Dumping"] = dumping_weight
    reference_row = {
        "problem": "Illegal Dumping", "agency": "DSNY", "borough": "BROOKLYN", "channel": "ONLINE",
        "created_hour": 10, "created_weekday": 2, "created_month": 9,
        "score": sigmoid(dumping_weight) if reference is None else reference,
    }
    model = {
        "kind": kind,
        "feature_version": "features-v1",
        "features": list(FEATURES),
        "calibration": {"slope": 1.0, "intercept": 0.0},
        "threshold": 0.5,
        "logistic_regression": {"C": 1.0, "intercept": 0.0, "weights": weights} if kind == "logistic_regression" else None,
        "reference_scores": [reference_row],
    }
    conn.cursor().execute(
        "INSERT INTO ML.MODEL_EVALUATIONS SELECT %s, 'features-v1', %s, '2026-09-01', CURRENT_TIMESTAMP(),"
        " PARSE_JSON('{}'), PARSE_JSON('{}'), PARSE_JSON(%s)",
        (version, kind, json.dumps(model)),
    )


def test_promotion_checks_the_registered_model_and_an_older_version_can_be_promoted_back(empty_db):
    assert approved_model(empty_db) is None
    with pytest.raises(PromotionRefused, match="not registered"):
        promote(empty_db, "m-missing")

    register(empty_db, "m-boost", 2.0, kind="gradient_boosting")
    with pytest.raises(PromotionRefused, match="no weights"):
        promote(empty_db, "m-boost")

    # The warehouse must reproduce the scores training computed.
    # Publications freeze features-v1 only.
    register(empty_db, "m-other-features", 2.0)
    empty_db.cursor().execute("UPDATE ML.MODEL_EVALUATIONS SET feature_version = 'features-v2' WHERE model_version = 'm-other-features'")
    with pytest.raises(PromotionRefused, match="features-v2"):
        promote(empty_db, "m-other-features")

    register(empty_db, "m-drifted", 2.0, reference=0.5)
    with pytest.raises(PromotionRefused, match="reference"):
        promote(empty_db, "m-drifted")
    assert approved_model(empty_db) is None

    register(empty_db, "m1", 2.0)
    register(empty_db, "m2", 3.0)
    promote(empty_db, "m1", note="first")
    assert approved_model(empty_db) == "m1"
    promote(empty_db, "m2")
    assert approved_model(empty_db) == "m2"
    promote(empty_db, "m1", note="rollback")
    assert approved_model(empty_db) == "m1"


def dumping(key: str, created: str, **fields) -> dict:
    return source_row(key, created, complaint_type="Illegal Dumping", **fields)


def scores(conn) -> dict[str, tuple]:
    """Published Admission Scores: (score, model version, prediction cutoff, scored at, Publication)."""
    rows = rows_of(
        conn, "SELECT unique_key, admission_score, model_version, prediction_cutoff, scored_at, publication_id FROM APP.ADMISSION_SCORES"
    )
    return {r["unique_key"]: (r["admission_score"], r["model_version"], r["prediction_cutoff"], r["scored_at"], r["publication_id"]) for r in rows}


def queue(conn) -> list[tuple]:
    rows = published(conn, "RISK_QUEUE", order="admission_score DESC NULLS LAST, unique_key")
    return [(r["unique_key"], r["admission_score"]) for r in rows]


def test_each_eligible_request_is_scored_once_by_the_publication_that_first_sees_it(empty_db, tmp_path):
    t1 = datetime(2026, 9, 10, 6, 0, tzinfo=timezone.utc)
    # New York times. 200-204 were created 40 h before the first extraction.
    stage_collector_run(empty_db, tmp_path, "t1", [
        dumping("200", "2026-09-08T10:00:00.000"),
        source_row("201", "2026-09-08T10:00:00.000"),  # Dirty Condition: no weight, scores 0.5
        dumping("202", "2026-09-06T10:00:00.000"),  # First Observed 88 h after creation: not eligible
        dumping("203", "2026-09-08T10:00:00.000", agency="NYPD"),  # outside the Modeling Cohort
        dumping("204", "2026-09-08T10:00:00.000", status="Closed", closed_date="2026-09-09T10:00:00.000"),
    ], date(2026, 9, 6), 5, t1)
    run(empty_db, STAGES)

    # No approved model yet: eligible open Requests are queued with no score, never a made-up one.
    assert scores(empty_db) == {}
    assert queue(empty_db) == [("200", None), ("201", None)]
    [row] = published(empty_db, "RISK_QUEUE", where="unique_key = '200'")
    assert row == {
        "unique_key": "200", "problem": "Illegal Dumping", "agency": "DSNY", "borough": "BROOKLYN",
        "created_date": date(2026, 9, 8), "admission_score": None,
    }

    register(empty_db, "m1", 2.0)
    promote(empty_db, "m1")
    t2 = datetime(2026, 9, 11, 6, 0, tzinfo=timezone.utc)
    stage_collector_run(empty_db, tmp_path, "t2", [
        dumping("205", "2026-09-10T02:00:00.000"),
        dumping("208", "2026-09-10T03:00:00.000", borough="QUEENS"),
    ], date(2026, 9, 10), 1, t2)
    # A replay of 202 extracted 54 h after its creation moves its First
    # Observed earlier, but it was already judged not eligible.
    replayed = datetime(2026, 9, 8, 20, 0, tzinfo=timezone.utc)
    stage_collector_run(empty_db, tmp_path, "replay", [dumping("202", "2026-09-06T10:00:00.000")], date(2026, 9, 6), 1, replayed)
    run(empty_db, STAGES)
    second = status(empty_db)["publication_id"]

    # Only Requests this Publication first sees are scored; 200 and 201 keep no score.
    after_m1 = scores(empty_db)
    assert {k: (pytest.approx(v[0]), *v[1:3], v[4]) for k, v in after_m1.items()} == {
        "205": (sigmoid(2.0), "m1", datetime(2026, 9, 11, 6, 0), second),
        "208": (sigmoid(2.0), "m1", datetime(2026, 9, 11, 6, 0), second),
    }
    assert queue(empty_db) == [("205", pytest.approx(sigmoid(2.0))), ("208", pytest.approx(sigmoid(2.0))), ("200", None), ("201", None)]

    # A new model scores only new Requests; stored scores are never recomputed.
    register(empty_db, "m2", 3.0)
    promote(empty_db, "m2")
    t3 = datetime(2026, 9, 12, 6, 0, tzinfo=timezone.utc)
    stage_collector_run(empty_db, tmp_path, "t3", [dumping("206", "2026-09-11T10:00:00.000")], date(2026, 9, 11), 1, t3)
    run(empty_db, STAGES)
    after_m2 = scores(empty_db)
    assert {k: v for k, v in after_m2.items() if k != "206"} == after_m1
    assert (pytest.approx(after_m2["206"][0]), after_m2["206"][1]) == (sigmoid(3.0), "m2")

    # A held attempt scores nothing that survives; the retry scores once.
    t4 = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    stage_collector_run(empty_db, tmp_path, "t4", [dumping("207", "2026-09-14T10:00:00.000")], date(2026, 9, 14), 1, t4)
    cur = empty_db.cursor()
    cur.execute("CREATE TEMPORARY TABLE SAVED_206 AS SELECT * FROM CLEAN.SERVICE_REQUESTS WHERE unique_key = '206'")
    cur.execute("INSERT INTO CLEAN.SERVICE_REQUESTS SELECT * FROM SAVED_206")
    with pytest.raises(ProgrammingError, match="Publication held"):
        run(empty_db, STAGES)
    cur.execute("DELETE FROM CLEAN.SERVICE_REQUESTS WHERE unique_key = '206'")
    cur.execute("INSERT INTO CLEAN.SERVICE_REQUESTS SELECT * FROM SAVED_206")
    assert publish(empty_db)["outcome"] == "published"
    fourth = status(empty_db)["publication_id"]

    after_retry = scores(empty_db)
    assert {k: v for k, v in after_retry.items() if k != "207"} == after_m2
    assert (pytest.approx(after_retry["207"][0]), *after_retry["207"][1:3], after_retry["207"][4]) == (
        sigmoid(3.0), "m2", datetime(2026, 9, 16, 6, 0), fourth,
    )
    # 200 and 201 are past their horizon (09-15 14:00 UTC): out of the queue, scores kept.
    assert [k for k, _ in queue(empty_db)] == ["206", "207", "205", "208"]
