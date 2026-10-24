"""Load the historical backfill (ingestion.backfill) into the warehouse one
creation month at a time, then reconcile it month by month.

    uv run --env-file .env python -m warehouse.backfill load --through 2026-10
    uv run --env-file .env python -m warehouse.backfill report --through 2026-10

`load` needs the run's manifest, so it only starts once the backfill is
complete. Each month's files are bulk-COPYed from their own prefix, checked
against the manifest (every file, every row), and only then processed, in
their own transaction, so no statement grows with the whole backfill (the
warehouse stops statements at 600 s). If Observations of another run are
waiting (a daily pass Snowpipe just loaded), it stops: those belong to the
readiness task. Rerunning is safe: COPY's load history and RAW's source_file
check skip files already loaded. One Publication follows the last month.

`report` prints, per month, the rows the source holds now, the rows the
manifest recorded, the rows RAW holds from the run, and the distinct Current
Requests created that month, and exits 1 unless every month agrees. It is a
snapshot for right after the load: later daily passes and source edits move
the source and Current Request counts, and show as differences.
"""

import argparse
import sys

from snowflake.connector import SnowflakeConnection

from ingestion.api_client import CreationWindow, SourceClient
from ingestion.backfill import backfill_plan, month_windows, parse_month
from ingestion.collector import Plan, RunLayout
from ingestion.fetch_311 import app_token
from warehouse.connection import connect
from warehouse.environment import DEV
from warehouse.pipeline import Stages, load_manifests, load_observations, process, publish


class BackfillIncomplete(Exception):
    """The backfill run has no manifest yet: it is still collecting, or stopped."""


class MonthMismatch(Exception):
    """A month's loaded files or rows differ from its manifest; nothing of it was processed."""


def load(conn: SnowflakeConnection, plan: Plan, stages: Stages = Stages()) -> dict:
    """Load, check and process each month of the plan's run, then publish."""
    load_manifests(conn, stages.manifests)
    row = conn.cursor().execute("SELECT extract_date FROM OPS.RUN_MANIFESTS WHERE run_id = %s", (plan.run_id,)).fetchone()
    if row is None:
        raise BackfillIncomplete(f"no manifest for run {plan.run_id}; finish the backfill first")
    assert isinstance(row, tuple)
    layout = RunLayout(plan.run_id, row[0].isoformat(), channel="bulk")

    months = []
    for window in month_windows(plan):
        prefix = layout.partition_prefix(window)
        loaded = load_observations(conn, f"{stages.observations.rstrip('/')}/{prefix.removeprefix('data/')}")
        _check_month(conn, plan.run_id, window, prefix)
        months.append({"month": window.month, "observations_loaded": loaded} | process(conn, only_run=plan.run_id))
        print(months[-1], flush=True)
    return {"months": months, "publication": publish(conn)["outcome"]}


def _check_month(conn: SnowflakeConnection, run_id: str, window: CreationWindow, prefix: str) -> None:
    cur = conn.cursor()
    expected = cur.execute(
        "SELECT COUNT(*), COALESCE(SUM(f.value:rows::NUMBER), 0)"
        " FROM OPS.RUN_MANIFESTS AS m, LATERAL FLATTEN(m.manifest:files) AS f"
        " WHERE m.run_id = %s AND f.value:partition::STRING = %s",
        (run_id, window.id),
    ).fetchone()
    loaded = cur.execute(
        "SELECT COUNT(DISTINCT source_file), COUNT(*) FROM RAW.RAW_311_OBSERVATIONS"
        " WHERE run_id = %s AND source_file LIKE %s",
        (run_id, f"%{prefix}%"),
    ).fetchone()
    if expected != loaded:
        raise MonthMismatch(f"{window.month}: manifest has (files, rows) {expected}, RAW has {loaded}")


def report(conn: SnowflakeConnection, source: SourceClient, plan: Plan) -> list[dict]:
    """One row per month: source_rows, manifest_rows, raw_rows, current_requests, ok."""
    cur = conn.cursor()
    manifest = dict(
        cur.execute(
            "SELECT p.value:partition::STRING, p.value:row_count::NUMBER"
            " FROM OPS.RUN_MANIFESTS AS m, LATERAL FLATTEN(m.manifest:partitions) AS p WHERE m.run_id = %s",
            (plan.run_id,),
        ).fetchall()  # type: ignore[arg-type]
    )
    # Per data file, not per payload: each file holds one partition (month).
    raw = dict(
        cur.execute(
            "SELECT REGEXP_SUBSTR(source_file, 'created-[^/]+'), COUNT(*)"
            " FROM RAW.RAW_311_OBSERVATIONS WHERE run_id = %s GROUP BY 1",
            (plan.run_id,),
        ).fetchall()  # type: ignore[arg-type]
    )
    current = dict(
        cur.execute(
            "SELECT TO_CHAR(created_date_local, 'YYYY-MM'), COUNT(*) FROM CLEAN.SERVICE_REQUESTS GROUP BY 1"
        ).fetchall()  # type: ignore[arg-type]
    )
    rows = []
    for window in month_windows(plan):
        counts = {
            "source_rows": source.count_created(window),
            "manifest_rows": int(manifest.get(window.id, 0)),
            "raw_rows": int(raw.get(window.id, 0)),
            "current_requests": int(current.get(window.month, 0)),
        }
        rows.append({"month": window.month} | counts | {"ok": len(set(counts.values())) == 1})
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["load", "report"])
    parser.add_argument("--through", required=True, type=parse_month, help="the backfill's last month, YYYY-MM")
    args = parser.parse_args(argv)
    plan = backfill_plan(args.through)
    env = DEV
    with connect(env.pipeline_role, env.warehouse, env.database) as conn:
        if args.action == "load":
            print(load(conn, plan))
            return 0
        rows = report(conn, SourceClient(app_token=app_token()), plan)
    print(f"{'month':8} {'source':>9} {'manifest':>9} {'raw':>9} {'current':>9}")
    for r in rows:
        flag = "ok" if r["ok"] else "DIFF"
        print(f"{r['month']:8} {r['source_rows']:>9} {r['manifest_rows']:>9} {r['raw_rows']:>9} {r['current_requests']:>9}  {flag}")
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
