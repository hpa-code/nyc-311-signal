"""Collect a creation-date slice and reconcile it against the source, or run
one change-cursor pass (what the daily Lambda does).

    python -m ingestion.fetch_311 --start 2026-09-01 --days 7 --store local:data/raw/bucket
    python -m ingestion.fetch_311 --start 2026-09-01 --days 7 --store s3:dev-nyc311-observations-<account>
    python -m ingestion.fetch_311 --cursor --store s3:dev-nyc311-observations-<account> [--initial-cursor 2026-10-23T20:16:00.000Z]

The app token comes from SOCRATA_APP_TOKEN, or from the Secrets Manager entry
named by NYC311_SOCRATA_SECRET_NAME (JSON {"app_token": ...}). Without either
the API still answers, with a lower rate limit.
"""

import argparse
import json
import os
import sys
from datetime import date

from ingestion.api_client import SourceClient
from ingestion.collector import Collector, CursorPlan, Plan, creation_day_partitions
from ingestion.object_store import LocalObjectStore, ObjectStore, S3ObjectStore

REGION = "us-east-2"


def open_store(spec: str) -> ObjectStore:
    kind, _, location = spec.partition(":")
    if kind == "local":
        return LocalObjectStore(location)
    if kind == "s3":
        import boto3

        return S3ObjectStore(boto3.client("s3", region_name=REGION), location)
    raise SystemExit(f"unknown store {spec!r}; use local:<dir> or s3:<bucket>")


def app_token() -> str | None:
    if token := os.environ.get("SOCRATA_APP_TOKEN"):
        return token
    if secret_name := os.environ.get("NYC311_SOCRATA_SECRET_NAME"):
        import boto3

        secret = boto3.client("secretsmanager", region_name=REGION).get_secret_value(SecretId=secret_name)
        return json.loads(secret["SecretString"])["app_token"]
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", required=True, help="local:<dir> or s3:<bucket>")
    parser.add_argument("--start", type=date.fromisoformat, help="first NYC-local creation day")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--run-id", help="defaults to slice-<start>-<days>d; reuse it to resume")
    parser.add_argument("--cursor", action="store_true", help="run one change-cursor pass instead of a slice")
    parser.add_argument("--initial-cursor", help="seed :updated_at for the first cursor pass")
    parser.add_argument("--page-size", type=int, default=10_000)
    args = parser.parse_args(argv)
    source = SourceClient(app_token=app_token())
    collector = Collector(source=source, store=open_store(args.store), page_size=args.page_size)

    if args.cursor:
        manifest = collector.run(CursorPlan(initial_cursor=args.initial_cursor))
        print(f"run {manifest['run_id']}: {manifest['file_count']} files, {manifest['row_count']} rows")
        return 0

    if args.start is None:
        parser.error("--start is required for a slice")
    run_id = args.run_id or f"slice-{args.start.isoformat()}-{args.days}d"
    windows = creation_day_partitions(args.start, args.days)
    manifest = collector.run(Plan(run_id=run_id, partitions=windows))

    mismatches = 0
    print(f"run {run_id}: {manifest['file_count']} files, {manifest['row_count']} rows")
    for window, partition in zip(windows, manifest["partitions"], strict=True):
        expected = source.count_created(window)
        ok = expected == partition["row_count"]
        mismatches += not ok
        print(f"  {partition['partition']}: manifest {partition['row_count']}, source {expected} {'ok' if ok else 'MISMATCH'}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
