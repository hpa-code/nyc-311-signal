"""Builders for Seam 2: fixture Observation files made by the real collector."""

from datetime import date, datetime, timezone
from pathlib import Path

from snowflake.connector import SnowflakeConnection

from ingestion.api_client import SourceClient
from ingestion.collector import Collector, Plan, creation_day_partitions
from ingestion.object_store import LocalObjectStore
from ingestion.tests.fake_socrata import FakeSocrata
from warehouse.pipeline import Stages

FIXTURE_STAGE = "RAW.FIXTURE_STAGE"
STAGES = Stages(observations=f"@{FIXTURE_STAGE}/data/", manifests=f"@{FIXTURE_STAGE}/manifests/")
EXTRACTED = datetime(2026, 9, 10, 6, 0, tzinfo=timezone.utc)


def source_row(key: str, created: str, updated_at: str = "2026-09-08T01:33:26.619Z", **fields) -> dict:
    """One Service Request as the Socrata API returns it."""
    row = {
        ":id": f"row-{key}",
        ":created_at": "2026-09-02T01:33:26.674Z",
        ":updated_at": updated_at,
        "unique_key": key,
        "created_date": created,
        "agency": "DSNY",
        "complaint_type": "Dirty Condition",
        "descriptor": "Trash",
        "borough": "BROOKLYN",
        "status": "Open",
    }
    row.update(fields)
    return row


def stage_collector_run(
    conn: SnowflakeConnection, tmp_path: Path, run_id: str, rows: list[dict], start: date, days: int, extracted_at: datetime
) -> dict:
    """Collect `rows` with the real collector and PUT its data and manifest files to the fixture stage."""
    root = tmp_path / run_id
    source = SourceClient(transport=FakeSocrata(rows), sleep=lambda s: None)
    manifest = Collector(source=source, store=LocalObjectStore(root), clock=lambda: extracted_at).run(
        Plan(run_id=run_id, partitions=creation_day_partitions(start, days))
    )
    cur = conn.cursor()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).parent.as_posix()
        if relative.split("/")[0] in ("data", "manifests"):
            cur.execute(f"PUT 'file://{path}' '@{FIXTURE_STAGE}/{relative}/' AUTO_COMPRESS = FALSE OVERWRITE = FALSE")
    return manifest


def observation(
    row: dict, run_id: str, extracted_at: datetime, *, updated_at: str | None = "2026-09-08T01:33:26.619Z"
) -> dict:
    """One Observation line as the collector writes it, built by hand so tests can
    also produce malformed ones the collector would refuse to write."""
    import hashlib
    import json

    canonical = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "run_id": run_id,
        "extracted_at": extracted_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_dataset": "erm2-nwe9",
        "schema_version": 1,
        "payload_hash": hashlib.sha256(canonical.encode()).hexdigest(),
        "source_created_at": "2026-09-02T01:33:26.674Z",
        "source_updated_at": updated_at,
        "payload": row,
    }


def stage_observation_file(conn: SnowflakeConnection, tmp_path: Path, name: str, observations: list[dict]) -> None:
    """PUT one gzip NDJSON data file (no manifest) to the fixture stage."""
    import gzip
    import json

    path = tmp_path / f"{name}.ndjson.gz"
    lines = "".join(json.dumps(o) + "\n" for o in observations)
    path.write_bytes(gzip.compress(lines.encode(), mtime=0))
    conn.cursor().execute(f"PUT 'file://{path}' '@{FIXTURE_STAGE}/data/{name}/' AUTO_COMPRESS = FALSE OVERWRITE = FALSE")


def payload(key, created: str = "2026-09-01T08:00:00.000", **fields) -> dict:
    """A source row without Socrata system fields (what Observations carry)."""
    row = {
        "unique_key": key,
        "created_date": created,
        "agency": "DSNY",
        "complaint_type": "Dirty Condition",
        "borough": "BROOKLYN",
        "status": "Open",
    }
    row.update(fields)
    return {k: v for k, v in row.items() if v is not None}
