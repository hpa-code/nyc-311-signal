"""Recovery drill (instruction.md §14), against the real source and the test
database NYC311_DEV_TEST, which it rebuilds from the migrations:

1. Collect a few NYC-local creation days twice into local buckets: once
   straight through, once killed after a few pages and then resumed.
2. Compare the two runs' Observations: the same Requests and payloads, with
   no row lost or repeated across the interruption.
3. Rebuild the warehouse from each run in turn and compare Current Requests
   and the published Overview.
4. Replay an older Observation of one Request (an earlier extraction and
   source `:updated_at`, a different status) and check that the newer state
   stays current while the old one joins its history.

    uv run --env-file .env python -m warehouse.drill [--start 2026-10-01 --days 3]

Prints a report and exits 1 if any check fails; docs/recovery-drill.md
records a run. Never run it while the Seam 2 tests are running: both rebuild
NYC311_DEV_TEST.
"""

import argparse
import gzip
import json
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ingestion.api_client import SourceClient, urllib_transport
from ingestion.collector import Collector, Plan, creation_day_partitions, wrap_observation
from ingestion.fetch_311 import app_token
from ingestion.object_store import LocalObjectStore
from warehouse.connection import connect
from warehouse.environment import DEV
from warehouse.pipeline import run
from warehouse.tests.fixtures import STAGES, put_collected, rebuild_test_database, stage_observation_file

PAGE_SIZE = 2000


class Killed(Exception):
    """The collector process dying mid-run (not a retryable source error)."""


def killing_transport(after_requests: int):
    calls = 0

    def transport(url: str, params: dict, headers: dict):
        nonlocal calls
        calls += 1
        if calls > after_requests:
            raise Killed(f"killed after {after_requests} requests")
        return urllib_transport(url, params, headers)

    return transport


def collect(root: Path, plan: Plan, transport=urllib_transport) -> dict:
    source = SourceClient(transport=transport, app_token=app_token())
    return Collector(source=source, store=LocalObjectStore(root), page_size=PAGE_SIZE).run(plan)


def observations(root: Path, manifest: dict) -> dict[tuple[str, str], int]:
    """(unique_key, payload_hash) -> how many times the run wrote it."""
    seen: dict[tuple[str, str], int] = {}
    for f in manifest["files"]:
        for line in gzip.decompress((root / f["key"]).read_bytes()).decode().splitlines():
            o = json.loads(line)
            k = (o["payload"]["unique_key"], o["payload_hash"])
            seen[k] = seen.get(k, 0) + 1
    return seen


def rebuild(conn, env, root: Path) -> dict:
    """Fresh test database, the run's files loaded and published; what the dashboard would show."""
    rebuild_test_database(conn, env)
    put_collected(conn, root, "data")
    put_collected(conn, root, "manifests")
    outcome = run(conn, STAGES)
    cur = conn.cursor()
    current = cur.execute(
        "SELECT COUNT(*), COUNT(DISTINCT unique_key), COUNT_IF(status = 'Closed'), HASH_AGG(unique_key, payload_hash)"
        " FROM CLEAN.SERVICE_REQUESTS"
    ).fetchone()
    overview = cur.execute(
        "SELECT created_date, requests, closed_requests, open_requests FROM APP.OVERVIEW_DAILY ORDER BY 1"
    ).fetchall()
    return {"publication": outcome["publication"], "current": current, "overview": overview}


def replay_older(conn, tmp: Path) -> dict:
    """Stage an older, different Observation of one Closed Request and process it."""
    cur = conn.cursor()
    key, payload, updated, extracted = cur.execute(
        "SELECT unique_key, v.payload, v.valid_from_updated_at, v.valid_from_extracted_at FROM CLEAN.REQUEST_VERSIONS AS v"
        " WHERE unique_key IN (SELECT unique_key FROM CLEAN.SERVICE_REQUESTS WHERE status = 'Closed')"
        " ORDER BY unique_key LIMIT 1"
    ).fetchone()
    before = cur.execute(
        "SELECT status, payload_hash, source_updated_at FROM CLEAN.SERVICE_REQUESTS WHERE unique_key = %s", (key,)
    ).fetchone()
    older = {**json.loads(payload), "status": "Open"}
    older.pop("closed_date", None)
    older_updated = updated - timedelta(days=3)
    older_extracted = extracted - timedelta(days=3)
    row = {**older, ":created_at": older_updated.isoformat(), ":updated_at": older_updated.strftime("%Y-%m-%dT%H:%M:%S.000Z")}
    observation = {**wrap_observation(row, "drill-replay", older_extracted), "source_updated_at": row[":updated_at"]}
    stage_observation_file(conn, tmp, "drill-replay", [observation])
    run(conn, STAGES)
    after = cur.execute(
        "SELECT status, payload_hash, source_updated_at FROM CLEAN.SERVICE_REQUESTS WHERE unique_key = %s", (key,)
    ).fetchone()
    history = cur.execute(
        "SELECT payload:status::STRING, valid_from_updated_at FROM CLEAN.REQUEST_VERSIONS WHERE unique_key = %s"
        " ORDER BY valid_from_updated_at, valid_from_extracted_at",
        (key,),
    ).fetchall()
    return {"key": key, "before": before, "after": after, "history": history}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 10, 1))
    parser.add_argument("--days", type=int, default=3)
    args = parser.parse_args(argv)
    partitions = creation_day_partitions(args.start, args.days)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        uninterrupted, interrupted = tmp / "uninterrupted", tmp / "interrupted"
        print(f"1. Collecting {args.days} creation days from {args.start}, {PAGE_SIZE} rows a page")
        started = datetime.now(timezone.utc)
        straight_run = collect(uninterrupted, Plan("drill-uninterrupted", partitions))
        print(f"   uninterrupted: {straight_run['row_count']:,} rows in {straight_run['file_count']} files")
        try:
            collect(interrupted, Plan("drill-interrupted", partitions), killing_transport(after_requests=3))
            raise AssertionError("the interrupted run was not interrupted")
        except Killed as killed:
            files = sorted((interrupted / "data").rglob("*.ndjson.gz"))
            print(f"   interrupted: {killed}; {len(files)} files written, no manifest: {not list((interrupted / 'manifests').rglob('*.json'))}")
        resumed = collect(interrupted, Plan("drill-interrupted", partitions))
        print(f"   resumed: {resumed['row_count']:,} rows in {resumed['file_count']} files, "
              f"{(datetime.now(timezone.utc) - started).total_seconds():.0f} s in all")

        checks: dict[str, bool] = {}
        straight_obs, resumed_obs = observations(uninterrupted, straight_run), observations(interrupted, resumed)
        repeated = sum(n - 1 for n in resumed_obs.values() if n > 1)
        checks["same Observations, none repeated"] = straight_obs == resumed_obs and repeated == 0
        print(f"2. Same Observations: {straight_obs == resumed_obs} ({len(straight_obs):,} distinct; {repeated} repeated "
              f"across the interruption; {len(set(straight_obs) - set(resumed_obs))} only uninterrupted, "
              f"{len(set(resumed_obs) - set(straight_obs))} only resumed)")

        env = DEV.for_tests(manifests_stage=STAGES.manifests)
        with connect(env.pipeline_role, env.warehouse, env.database) as conn:
            from_straight = rebuild(conn, env, uninterrupted)
            from_resumed = rebuild(conn, env, interrupted)
            print(f"3. Rebuilt: uninterrupted {from_straight['publication']}, resumed {from_resumed['publication']}; "
                  f"Current Requests (rows, distinct, Closed) {from_straight['current'][:3]} vs {from_resumed['current'][:3]}; "
                  f"same states: {from_straight['current'][3] == from_resumed['current'][3]}; "
                  f"same Overview: {from_straight['overview'] == from_resumed['overview']} ({len(from_straight['overview'])} days)")
            checks["same Current Requests and Overview"] = (
                from_straight["current"] == from_resumed["current"] and from_straight["overview"] == from_resumed["overview"]
            )
            replay = replay_older(conn, tmp)
            kept = replay["before"] == replay["after"]
            checks["older Observation kept out of the Current Request"] = kept and len(replay["history"]) == 2
            print(f"4. Replayed an older 'Open' Observation of {replay['key']} (Closed): current state kept: {kept}; "
                  f"history now {[s for s, _ in replay['history']]}")
    failed = [name for name, passed in checks.items() if not passed]
    print("Drill passed." if not failed else f"Drill FAILED: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
