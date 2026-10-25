"""Promote a registered model version, by hand; it scores new Requests from
the next Publication on.

    uv run --env-file .env python -m ml.promote <model_version> [note]

A version is registered by `python -m ml.train` (ML.MODEL_EVALUATIONS, with
its preprocessing, feature schema, dependencies and metrics). Promotion
refuses a version for another feature version than the one Publications
freeze, one the warehouse cannot score (no logistic regression weights:
gradient boosting cannot be promoted yet), or one whose reference scores ML.DELAYED_PROBABILITY does not
reproduce. Promoting an earlier version rolls back; Admission Scores already
stored keep the version that computed them.
"""

import sys

from snowflake.connector import SnowflakeConnection

from ml.features import FEATURE_VERSION
from warehouse.connection import connect
from warehouse.environment import current

# Largest difference between a reference score from training and the
# warehouse's score for the same features.
TOLERANCE = 1e-9

_REFERENCE_SCORES = """
SELECT
  r.value:score::FLOAT,
  ML.DELAYED_PROBABILITY(
    e.model, r.value:problem::STRING, r.value:agency::STRING, r.value:borough::STRING, r.value:channel::STRING,
    r.value:created_hour::NUMBER, r.value:created_weekday::NUMBER, r.value:created_month::NUMBER
  )
FROM ML.MODEL_EVALUATIONS AS e, LATERAL FLATTEN(input => e.model:reference_scores) AS r
WHERE e.model_version = %s
"""


class PromotionRefused(Exception):
    pass


def promote(conn: SnowflakeConnection, model_version: str, note: str | None = None) -> None:
    cur = conn.cursor()
    row = cur.execute(
        "SELECT model:kind::STRING, COALESCE(IS_OBJECT(model:logistic_regression), FALSE), feature_version"
        " FROM ML.MODEL_EVALUATIONS WHERE model_version = %s",
        (model_version,),
    ).fetchone()
    if row is None:
        raise PromotionRefused(f"{model_version} is not registered")
    assert isinstance(row, tuple)
    kind, scorable, feature_version = row
    if feature_version != FEATURE_VERSION:
        raise PromotionRefused(f"{model_version} reads {feature_version}; Publications freeze {FEATURE_VERSION}")
    if not scorable:
        raise PromotionRefused(f"{model_version} ({kind}) has no weights the warehouse can score with")
    pairs: list[tuple] = cur.execute(_REFERENCE_SCORES, (model_version,)).fetchall()  # type: ignore[assignment]
    if not pairs:
        raise PromotionRefused(f"{model_version} has no reference scores to check")
    worst = max(abs(expected - actual) for expected, actual in pairs)
    if worst > TOLERANCE:
        raise PromotionRefused(f"{model_version}: the warehouse misses a reference score by {worst:.3g}")
    cur.execute(
        "INSERT INTO ML.MODEL_PROMOTIONS (model_version, promoted_at, note) VALUES (%s, CURRENT_TIMESTAMP(), %s)",
        (model_version, note),
    )


def approved_model(conn: SnowflakeConnection) -> str | None:
    row = conn.cursor().execute("SELECT model_version FROM ML.APPROVED_MODEL").fetchone()
    return None if row is None else str(row[0])  # type: ignore[index]


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    env = current()
    with connect(env.pipeline_role, env.warehouse, env.database) as conn:
        promote(conn, argv[0], " ".join(argv[1:]) or None)
        print(f"{env.database}: approved model is now {approved_model(conn)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
