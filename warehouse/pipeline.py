"""Warehouse pipeline steps: load S3 files into RAW, then merge Current Requests.

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
    return {
        "manifests_loaded": _copy(conn, render(read("transformations/load_manifests.sql"), values)),
        "observations_loaded": _copy(conn, render(read("transformations/load_observations.sql"), values)),
        "current_requests_merged": _merge(conn, read("transformations/merge_current_requests.sql")),
    }


def _copy(conn: SnowflakeConnection, sql: str) -> int:
    cur = conn.cursor()
    cur.execute(sql)
    columns = [c[0] for c in cur.description or []]
    if "rows_loaded" not in columns:  # "Copy executed with 0 files processed."
        return 0
    i = columns.index("rows_loaded")
    rows: list[tuple] = cur.fetchall()  # type: ignore[assignment]
    return sum(int(row[i]) for row in rows)


def _merge(conn: SnowflakeConnection, sql: str) -> int:
    cur = conn.cursor()
    cur.execute(sql)
    row = cur.fetchone()
    assert isinstance(row, tuple)
    inserted, updated = row
    return int(inserted) + int(updated)


def main() -> int:
    env = DEV
    with connect(env.pipeline_role, env.warehouse, env.database) as conn:
        print(run(conn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
