"""Warehouse pipeline: load S3 files into RAW, then (in one transaction) quarantine
malformed Observations, rebuild affected Request Versions and merge Current Requests,
then publish.

Daily, Snowpipe loads the files, the readiness task (OPS.READINESS_TASK)
processes them once their run reconciles, and its child OPS.PUBLISH_TASK
publishes. This module is the manual path: bulk COPY plus ungated processing
(backfill, tests), the readiness call, and publishing.

    uv run --env-file .env python -m warehouse.pipeline            # bulk load + process + publish
    uv run --env-file .env python -m warehouse.pipeline --ready    # what the task graph does
"""

import json
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


def run(conn: SnowflakeConnection, stages: Stages = Stages()) -> dict[str, int | str]:
    """Run every step; returns rows loaded or merged per step, and the publish outcome."""
    counts: dict[str, int | str] = {
        "manifests_loaded": load_manifests(conn, stages.manifests),
        "observations_loaded": load_observations(conn, stages.observations),
    }
    return counts | process(conn) | {"publication": publish(conn)["outcome"]}


def load_manifests(conn: SnowflakeConnection, stage: str) -> int:
    """COPY run manifests from `stage` into OPS.RUN_MANIFESTS; returns manifests loaded."""
    return _copy(conn, render(read("transformations/load_manifests.sql"), {"manifests_stage": stage}))


def load_observations(conn: SnowflakeConnection, stage: str) -> int:
    """Bulk COPY Observation files under `stage` (a stage or a path in one) into RAW;
    returns rows that reached RAW."""
    return _bulk_load(conn, render(read("transformations/load_observations.sql"), {"observations_stage": stage}))


def process_ready_runs(conn: SnowflakeConnection) -> dict:
    """One readiness check: process pending Observations if their runs reconcile."""
    row = conn.cursor().execute("CALL OPS.PROCESS_READY_RUNS()").fetchone()
    assert isinstance(row, tuple)
    return json.loads(row[0])


def publish(conn: SnowflakeConnection) -> dict:
    """Build, check and (if every check passes) publish a new Publication.

    Returns its outcome: published, unchanged, or waiting (a run is in flight).
    Raises if the checks fail; the current Publication then stays.
    """
    row = conn.cursor().execute("CALL OPS.PUBLISH()").fetchone()
    assert isinstance(row, tuple)
    return json.loads(row[0])


PROCESS_STEPS = {
    "take_new_observations": "transformations/process/01_take_new_observations.sql",
    "quarantine": "transformations/process/02_quarantine.sql",
    "request_versions": "transformations/process/03_request_versions.sql",
    "merge_current_requests": "transformations/process/04_merge_current_requests.sql",
}


def process(conn: SnowflakeConnection) -> dict[str, int]:
    """Quarantine, Request Versions and Current Requests for every pending Observation.
    All-or-nothing: the stream offset only advances if every step commits.
    Holds the pipeline lock, like the readiness procedure, so publishing waits."""
    cur = conn.cursor()
    cur.execute("BEGIN")
    try:
        conn.execute_string(read("transformations/take_pipeline_lock.sql"))
        # Each step's last statement is the one whose result we report.
        last = {name: list(conn.execute_string(read(path)))[-1] for name, path in PROCESS_STEPS.items()}
        cur.execute("COMMIT")
    except Exception:
        cur.execute("ROLLBACK")
        raise
    merge = last["merge_current_requests"].fetchone()
    assert isinstance(merge, tuple)
    return {
        "observations_quarantined": int(last["quarantine"].rowcount or 0),
        "current_requests_merged": int(merge[0]) + int(merge[1]),
    }


def _bulk_load(conn: SnowflakeConnection, sql: str) -> int:
    """Rows that reached RAW (the INSERT out of the landing table)."""
    _, insert, _ = conn.execute_string(sql)
    return int(insert.rowcount or 0)


def _copy(conn: SnowflakeConnection, sql: str) -> int:
    cur = conn.cursor()
    cur.execute(sql)
    columns = [c[0] for c in cur.description or []]
    if "rows_loaded" not in columns:  # "Copy executed with 0 files processed."
        return 0
    i = columns.index("rows_loaded")
    rows: list[tuple] = cur.fetchall()  # type: ignore[assignment]
    return sum(int(row[i]) for row in rows)


def main(argv: list[str]) -> int:
    env = DEV
    with connect(env.pipeline_role, env.warehouse, env.database) as conn:
        if "--ready" in argv:
            print(process_ready_runs(conn))
            print(publish(conn))
        else:
            print(run(conn))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
