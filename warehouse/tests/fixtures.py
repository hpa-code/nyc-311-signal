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
