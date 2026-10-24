"""Warehouse pipeline: load S3 files into RAW, then (in one transaction) quarantine
malformed Observations, rebuild affected Request Versions and merge Current Requests.

    uv run --env-file .env python -m warehouse.pipeline
"""

import sys
from dataclasses import dataclass

from snowflake.connector import SnowflakeConnection

from warehouse.connection import connect
from warehouse.environment import DEV
from warehouse.sql import read, render


@dataclass(frozen=True)
class Stages:
    observations: str = "@RAW.OBSERVATIONS_STAGE"
    manifests: str = "@OPS.MANIFESTS_STAGE"


def run(conn: SnowflakeConnection, stages: Stages = Stages()) -> dict[str, int]:
    """Run every step; returns rows loaded or merged per step."""
    values = {"observations_stage": stages.observations, "manifests_stage": stages.manifests}
    counts = {
        "manifests_loaded": _copy(conn, render(read("transformations/load_manifests.sql"), values)),
        "observations_loaded": _copy(conn, render(read("transformations/load_observations.sql"), values)),
    }
    return counts | _process(conn)


PROCESS_STEPS = [
    "transformations/process/01_take_new_observations.sql",
    "transformations/process/02_quarantine.sql",
    "transformations/process/03_request_versions.sql",
    "transformations/process/04_merge_current_requests.sql",
]


def _process(conn: SnowflakeConnection) -> dict[str, int]:
    """All-or-nothing: the stream offset only advances if every step commits."""
    cur = conn.cursor()
    cur.execute("BEGIN")
    try:
        results = [list(conn.execute_string(read(step))) for step in PROCESS_STEPS]
        cur.execute("COMMIT")
    except Exception:
        cur.execute("ROLLBACK")
        raise
    quarantined = results[1][-1].rowcount or 0
    merge = results[3][-1].fetchone()
    assert isinstance(merge, tuple)
    return {"observations_quarantined": int(quarantined), "current_requests_merged": int(merge[0]) + int(merge[1])}


def _copy(conn: SnowflakeConnection, sql: str) -> int:
    cur = conn.cursor()
    cur.execute(sql)
    columns = [c[0] for c in cur.description or []]
    if "rows_loaded" not in columns:  # "Copy executed with 0 files processed."
        return 0
    i = columns.index("rows_loaded")
    rows: list[tuple] = cur.fetchall()  # type: ignore[assignment]
    return sum(int(row[i]) for row in rows)


def main() -> int:
    env = DEV
    with connect(env.pipeline_role, env.warehouse, env.database) as conn:
        print(run(conn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
