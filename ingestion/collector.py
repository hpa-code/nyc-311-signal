"""Collector: turns a Plan into immutable Observation files and a run manifest.

Storage layout (all keys relative to the bucket):

    data/extract_date=YYYY-MM-DD/run_id=<run>/<partition>/part-NNNNN.ndjson.gz
    manifests/extract_date=YYYY-MM-DD/run_id=<run>/manifest.json
    checkpoints/runs/<run>/run.json
    checkpoints/runs/<run>/partitions/<partition>.json

A file's key depends only on the run ID, the run's extract date (fixed when
the run first starts), the partition and the file's position in it, so a
resumed or retried run reproduces the same identities. Files and manifests are
only ever created, never replaced. If a file already exists when the collector
reaches it, the stored file wins and the page cursor continues from its last row.
"""

import gzip
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from ingestion.api_client import DATASET_ID, CreationWindow, PageCursor, SourceClient
from ingestion.object_store import ObjectStore

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Plan:
    run_id: str
    partitions: list[CreationWindow]


def creation_day_partitions(start: date, days: int) -> list[CreationWindow]:
    """One partition per NYC-local creation day."""
    first = datetime(start.year, start.month, start.day)
    return [CreationWindow(first + timedelta(days=i), first + timedelta(days=i + 1)) for i in range(days)]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Collector:
    def __init__(
        self,
        source: SourceClient,
        store: ObjectStore,
        clock: Callable[[], datetime] = utc_now,
        page_size: int = 10_000,
    ):
        self._source = source
        self._store = store
        self._clock = clock
        self._page_size = page_size

    def run(self, plan: Plan) -> dict:
        run_state = self._start_or_resume(plan)
        layout = RunLayout(plan.run_id, run_state["extract_date"])
        manifest = self._stored_manifest(layout)
        if manifest is None:
            partitions = [self._collect_partition(layout, window) for window in plan.partitions]
            manifest = self._write_manifest(layout, partitions)
        if run_state["state"] != "complete":
            run_state["state"] = "complete"
            self._store.put(layout.run_key, _json_bytes(run_state))
        return manifest

    def _start_or_resume(self, plan: Plan) -> dict:
        key = RunLayout(plan.run_id, "").run_key
        partition_ids = [w.id for w in plan.partitions]
        stored = self._store.get(key)
        if stored is not None:
            state = json.loads(stored)
            if state["partitions"] != partition_ids:
                raise ValueError(f"run {plan.run_id} was started with partitions {state['partitions']}; use a new run ID")
            return state
        state = {
            "run_id": plan.run_id,
            "extract_date": self._clock().date().isoformat(),
            "partitions": partition_ids,
            "state": "in_progress",
        }
        self._store.put(key, _json_bytes(state))
        return state

    def _stored_manifest(self, layout: "RunLayout") -> dict | None:
        stored = self._store.get(layout.manifest_key)
        return json.loads(stored) if stored is not None else None

    def _write_manifest(self, layout: "RunLayout", partitions: list[dict]) -> dict:
        files = [f for p in partitions for f in p["files"]]
        manifest = {
            "run_id": layout.run_id,
            "extract_date": layout.extract_date,
            "source_dataset": DATASET_ID,
            "schema_version": SCHEMA_VERSION,
            "partitions": [
                {k: p[k] for k in ("partition", "bounds", "row_count", "checksum")} for p in partitions
            ],
            "files": files,
            "file_count": len(files),
            "row_count": sum(f["rows"] for f in files),
            "completed_at": _iso(self._clock()),
        }
        if not self._store.put_if_absent(layout.manifest_key, _json_bytes(manifest)):
            return self._stored_manifest(layout) or manifest
        return manifest

    def _collect_partition(self, layout: "RunLayout", window: CreationWindow) -> dict:
        key = layout.checkpoint_key(window)
        stored = self._store.get(key)
        checkpoint = json.loads(stored) if stored is not None else _new_checkpoint(window)

        # Only an empty page ends a partition, so completion never depends on
        # the page size of the run that wrote earlier files.
        while checkpoint["state"] != "complete":
            after = tuple(checkpoint["after"]) if checkpoint["after"] else None
            rows = self._source.fetch_created_page(window, after, self._page_size)  # type: ignore[arg-type]
            if not rows:
                checkpoint["state"] = "complete"
                self._store.put(key, _json_bytes(checkpoint))
                break
            file_key = layout.data_key(window, len(checkpoint["files"]))
            data = _encode_file(rows, layout.run_id, self._clock())
            if not self._store.put_if_absent(file_key, data):
                # Stored before a crash: keep it and continue from its last row.
                data = self._store.get(file_key) or b""

            rows_in_file, last = _file_stats(data)
            checksum = hashlib.sha256(data).hexdigest()
            checkpoint["files"].append({"key": file_key, "partition": window.id, "rows": rows_in_file, "sha256": checksum})
            checkpoint["after"] = list(last)
            checkpoint["row_count"] += rows_in_file
            checkpoint["checksum"] = hashlib.sha256(
                "".join(f["sha256"] for f in checkpoint["files"]).encode()
            ).hexdigest()
            self._store.put(key, _json_bytes(checkpoint))

        return checkpoint


@dataclass(frozen=True)
class RunLayout:
    """Where one run's files live in the bucket (see module docstring)."""

    run_id: str
    extract_date: str

    @property
    def run_key(self) -> str:
        return f"checkpoints/runs/{self.run_id}/run.json"

    def checkpoint_key(self, window: CreationWindow) -> str:
        return f"checkpoints/runs/{self.run_id}/partitions/{window.id}.json"

    def data_key(self, window: CreationWindow, index: int) -> str:
        return f"data/extract_date={self.extract_date}/run_id={self.run_id}/{window.id}/part-{index:05d}.ndjson.gz"

    @property
    def manifest_key(self) -> str:
        return f"manifests/extract_date={self.extract_date}/run_id={self.run_id}/manifest.json"


def _new_checkpoint(window: CreationWindow) -> dict:
    return {
        "partition": window.id,
        "bounds": {"created_from": window.start.isoformat(), "created_before": window.end.isoformat()},
        "order": "created_date, unique_key",
        "after": None,
        "files": [],
        "row_count": 0,
        "checksum": hashlib.sha256(b"").hexdigest(),
        "state": "in_progress",
    }


def wrap_observation(row: dict, run_id: str, extracted_at: datetime) -> dict:
    """One Observation line.

    `payload` is the source row minus Socrata system fields (`:id`, `:version`,
    `:@computed_region_*`, ...), with values exactly as returned. `payload_hash`
    is the SHA-256 of that payload as canonical JSON, so two Observations of an
    unchanged Request hash the same even when `:updated_at` moves. The source's
    `:created_at` and `:updated_at` are kept verbatim beside it.
    """
    payload = {k: v for k, v in row.items() if not k.startswith(":")}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "run_id": run_id,
        "extracted_at": _iso(extracted_at),
        "source_dataset": DATASET_ID,
        "schema_version": SCHEMA_VERSION,
        "payload_hash": hashlib.sha256(canonical.encode()).hexdigest(),
        "source_created_at": row[":created_at"],
        "source_updated_at": row[":updated_at"],
        "payload": payload,
    }


def _encode_file(rows: list[dict], run_id: str, extracted_at: datetime) -> bytes:
    lines = "".join(
        json.dumps(wrap_observation(r, run_id, extracted_at), sort_keys=True, ensure_ascii=False) + "\n" for r in rows
    )
    return gzip.compress(lines.encode(), mtime=0)


def _file_stats(data: bytes) -> tuple[int, PageCursor]:
    lines = gzip.decompress(data).decode().splitlines()
    last = json.loads(lines[-1])["payload"]
    return len(lines), (last["created_date"], last["unique_key"])


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True).encode()
