"""Training and evaluation examples: Modeling Cohort Requests with a Label,
split by creation time, snapshotted per model version into ML.MODEL_EXAMPLES.

Each Request is one example (its Current Request), so all its Observations
are in one split. A Label is the Request's latest label version observed by
the label cutoff, and only if that version has one: a Request reopened by then
is left out. Each split also takes only Labels matured when it would have
been used: training ones by the validation start, validation ones by the
test start, test ones by the label cutoff.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd
from snowflake.connector import SnowflakeConnection

from ml.features import FEATURES

# The Seven-Day Horizon plus the reporting allowance (ML.CURRENT_LABELS);
# snapshot_examples refuses label versions with a longer allowance.
GAP = timedelta(hours=168 + 72)
SPLITS = ("train", "validation", "test")

Window = tuple[datetime, datetime]


@dataclass(frozen=True)
class SplitPlan:
    """Half-open creation-time windows, naive UTC, each at least GAP after the one before."""

    train: Window
    validation: Window
    test: Window

    def __post_init__(self) -> None:
        windows = [self.train, self.validation, self.test]
        for start, end in windows:
            if not start < end:
                raise ValueError(f"empty window {start} to {end}")
        for (_, end), (start, _) in zip(windows, windows[1:]):
            if start < end + GAP:
                raise ValueError(f"window starting {start} is less than {GAP.total_seconds() / 3600:.0f} h after {end}")

    def labels_known_at(self, label_cutoff: datetime) -> dict[str, datetime]:
        """When each split's Labels must have matured: training ones by the
        validation start, validation ones by the test start, test ones by the cutoff."""
        return {"train": self.validation[0], "validation": self.test[0], "test": label_cutoff}

    def as_json(self) -> dict:
        return {name: [t.isoformat() for t in getattr(self, name)] for name in SPLITS}


_SNAPSHOT = """
INSERT INTO ML.MODEL_EXAMPLES
WITH labels AS (
  SELECT unique_key, label, duplicate_closure, matured_at_utc, observed_through
  FROM ML.TRAINING_LABELS
  WHERE observed_through <= %(label_cutoff)s
  QUALIFY ROW_NUMBER() OVER (PARTITION BY unique_key ORDER BY observed_through DESC, recorded_at DESC) = 1
),
examples AS (
  SELECT
    f.*,
    l.label,
    l.duplicate_closure,
    l.matured_at_utc,
    l.observed_through,
    CASE
      WHEN f.created_at_utc >= %(train_start)s AND f.created_at_utc < %(train_end)s THEN 'train'
      WHEN f.created_at_utc >= %(validation_start)s AND f.created_at_utc < %(validation_end)s THEN 'validation'
      WHEN f.created_at_utc >= %(test_start)s AND f.created_at_utc < %(test_end)s THEN 'test'
    END AS split,
    CASE split WHEN 'train' THEN %(train_known_at)s WHEN 'validation' THEN %(validation_known_at)s ELSE %(test_known_at)s END::TIMESTAMP_NTZ
      AS labels_known_at
  FROM ML.FEATURES AS f
  JOIN labels AS l ON l.unique_key = f.unique_key
)
SELECT
  %(model_version)s, split, unique_key, created_at_utc, label, duplicate_closure, observed_through AS label_observed_through,
  {features}
FROM examples
WHERE split IS NOT NULL AND label IS NOT NULL AND matured_at_utc <= labels_known_at
""".format(features=", ".join(FEATURES))


def snapshot_examples(conn: SnowflakeConnection, model_version: str, plan: SplitPlan, label_cutoff: datetime) -> dict[str, int]:
    """Snapshot `model_version`'s examples; returns the count per split."""
    params: dict = {"model_version": model_version, "label_cutoff": label_cutoff}
    known_at = plan.labels_known_at(label_cutoff)
    for name in SPLITS:
        start, end = getattr(plan, name)
        params |= {f"{name}_start": start, f"{name}_end": end, f"{name}_known_at": known_at[name]}
    cur = conn.cursor()
    row = cur.execute("SELECT MAX(reporting_allowance_hours) FROM ML.TRAINING_LABELS").fetchone()
    assert isinstance(row, tuple)
    if row[0] is not None and timedelta(hours=168 + row[0]) > GAP:
        raise ValueError(f"label versions use a {row[0]} h allowance; GAP assumes {GAP - timedelta(hours=168)}")
    cur.execute(_SNAPSHOT, params)
    counts = dict(cur.execute(
        "SELECT split, COUNT(*) FROM ML.MODEL_EXAMPLES WHERE model_version = %s GROUP BY split", (model_version,)
    ).fetchall())
    return {name: counts.get(name, 0) for name in SPLITS}


def load_examples(conn: SnowflakeConnection, model_version: str) -> pd.DataFrame:
    """A model version's examples, one row per Request."""
    cur = conn.cursor()
    cur.execute(
        f"SELECT split, unique_key, created_at_utc, label, duplicate_closure, {', '.join(FEATURES)}"
        " FROM ML.MODEL_EXAMPLES WHERE model_version = %s",
        (model_version,),
    )
    frame = cur.fetch_pandas_all()
    frame.columns = [c.lower() for c in frame.columns]
    return frame
