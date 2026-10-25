"""Collector: turns a plan into immutable Observation files and a run manifest.

A plan is either a set of creation-date partitions (slices, backfill) or a
cursor pass: every Request whose source `:updated_at` is past the stored
cursor minus an overlap.

Storage layout (all keys relative to the bucket):

    data/<channel>/extract_date=YYYY-MM-DD/run_id=<run>/<partition>/part-NNNNN.ndjson.gz
    manifests/extract_date=YYYY-MM-DD/run_id=<run>/manifest.json
    checkpoints/runs/<run>/run.json
    checkpoints/runs/<run>/partitions/<partition>.json
    checkpoints/cursor/<name>.json
    checkpoints/leases/<name>.json

A file's key depends only on the run ID, the run's extract date (fixed when
the run first starts), the partition and the file's position in it, so a
resumed or retried run reproduces the same identities. Files and manifests are
only ever created, never replaced. If a file already exists when the collector
reaches it, the stored file wins and the page cursor continues from its last row.
"""

import gzip
import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from ingestion.api_client import DATASET_ID, CreationWindow, PageCursor, SourceClient, UpdateWindow, Window
from ingestion.lease import Lease
from ingestion.object_store import ObjectStore

SCHEMA_VERSION = 1
# Project scope: Requests created from this NYC-local moment on.
SCOPE_START = datetime(2025, 1, 1)
# A collector killed without releasing its lease blocks others this long.
LEASE_TTL = timedelta(minutes=16)


@dataclass(frozen=True)
class Plan:
    """Collect fixed partitions under one run ID; rerun the same ID to resume.

    `channel` picks the data prefix: cursor passes write data/daily/, which
    Snowpipe auto-ingests; slices and backfill write data/bulk/, which only
    bulk COPY loads. One prefix per load path, so no file loads twice.
    """

    run_id: str
    partitions: Sequence[Window]
    channel: str = "bulk"

    @property
    def lease_name(self) -> str:
        return f"run-{self.run_id}"


@dataclass(frozen=True)
class CursorPlan:
    """One change-cursor pass. Its run ID is chosen by the collector: a pass
    that did not finish is resumed (same run, same bounds) before a new one starts.

    `initial_cursor` seeds the cursor only when none is stored yet.
    """

    name: str = "311"
    overlap: timedelta = timedelta(hours=6)
    created_from: datetime = SCOPE_START
    initial_cursor: str | None = None

    @property
    def lease_name(self) -> str:
        return f"cursor-{self.name}"


class RunPaused(Exception):
    """The run stopped at its deadline with work left; rerunning resumes it."""


class CursorMissing(Exception):
    """A cursor pass needs a stored cursor or an initial one."""


def creation_day_partitions(start: date, days: int) -> list[CreationWindow]:
    """One partition per NYC-local creation day."""
    first = datetime(start.year, start.month, start.day)
    return [CreationWindow(first + timedelta(days=i), first + timedelta(days=i + 1)) for i in range(days)]


def creation_month_partitions(first: date, last: date) -> list[CreationWindow]:
    """One partition per NYC-local creation month, from `first`'s month through `last`'s."""
    months = (last.year - first.year) * 12 + last.month - first.month + 1
    starts = [datetime(first.year + (first.month - 1 + i) // 12, (first.month - 1 + i) % 12 + 1, 1) for i in range(months + 1)]
    return [CreationWindow(start, end) for start, end in zip(starts, starts[1:])]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Collector:
    def __init__(
        self,
        source: SourceClient,
        store: ObjectStore,
        clock: Callable[[], datetime] = utc_now,
        page_size: int = 10_000,
        lease_ttl: timedelta = LEASE_TTL,
    ):
        self._source = source
        self._store = store
        self._clock = clock
        self._page_size = page_size
        self._lease_ttl = lease_ttl

    def run(self, plan: Plan | CursorPlan, stop_at: datetime | None = None) -> dict:
        """Collect the plan and return its manifest. Raises LeaseHeld if another
        collector is running the same plan, and RunPaused at `stop_at`."""
        lease = Lease(self._store, plan.lease_name, self._clock, self._lease_ttl)
        lease.acquire()
        try:
            if isinstance(plan, CursorPlan):
                return self._run_cursor(plan, lease, stop_at)
            return self._run_partitions(plan, lease, stop_at)
        finally:
            lease.release()

    def _run_cursor(self, plan: CursorPlan, lease: Lease, stop_at: datetime | None) -> dict:
        key = f"checkpoints/cursor/{plan.name}.json"
        stored = self._store.get(key)
        if stored is not None:
            state = json.loads(stored)
        elif plan.initial_cursor is not None:
            state = {"updated_at": _source_timestamp(plan.initial_cursor), "pending": None}
        else:
            raise CursorMissing(f"no cursor at {key}; seed one with an initial cursor")

        if state["pending"] is None:
            since = _source_timestamp(_parse_source_timestamp(state["updated_at"]) - plan.overlap)
            run_id = f"cursor-{self._clock():%Y%m%dT%H%M%SZ}"
            if self._store.get(run_state_key(run_id)) is not None:
                raise ValueError(f"run {run_id} already exists; start the next pass later")
            state["pending"] = {"run_id": run_id, "updated_after": since}
            self._store.put(key, _json_bytes(state))
        pending = state["pending"]
        window = UpdateWindow(updated_after=pending["updated_after"], created_from=plan.created_from)

        manifest = self._run_partitions(Plan(pending["run_id"], [window], channel="daily"), lease, stop_at)

        # Files and manifest are stored: only now may the cursor move.
        last = manifest["partitions"][0]["last_position"]
        if last is not None:
            state["updated_at"] = max(state["updated_at"], last[0], key=_parse_source_timestamp)
        state.update(pending=None, last_run_id=pending["run_id"], advanced_at=_iso(self._clock()))
        self._store.put(key, _json_bytes(state))
        return manifest

    def _run_partitions(self, plan: Plan, lease: Lease, stop_at: datetime | None) -> dict:
        run_state = self._start_or_resume(plan)
        layout = RunLayout(plan.run_id, run_state["extract_date"], plan.channel)
        manifest = self._stored_manifest(layout)
        if manifest is None:
            partitions = [self._collect_partition(layout, window, lease, stop_at) for window in plan.partitions]
            manifest = self._write_manifest(layout, partitions, run_state.get("started_at"))
        if run_state["state"] != "complete":
            run_state["state"] = "complete"
            self._store.put(run_state_key(layout.run_id), _json_bytes(run_state))
        return manifest

    def _start_or_resume(self, plan: Plan) -> dict:
        key = run_state_key(plan.run_id)
        partition_ids = [w.id for w in plan.partitions]
        stored = self._store.get(key)
        if stored is not None:
            state = json.loads(stored)
            if state["partitions"] != partition_ids:
                raise ValueError(f"run {plan.run_id} was started with partitions {state['partitions']}; use a new run ID")
            return state
        now = self._clock()
        state = {
            "run_id": plan.run_id,
            "extract_date": now.date().isoformat(),
            "started_at": _iso(now),
            "partitions": partition_ids,
            "state": "in_progress",
        }
        self._store.put(key, _json_bytes(state))
        return state

    def _stored_manifest(self, layout: "RunLayout") -> dict | None:
        stored = self._store.get(layout.manifest_key)
        return json.loads(stored) if stored is not None else None

    def _write_manifest(self, layout: "RunLayout", partitions: list[dict], started_at: str | None) -> dict:
        files = [f for p in partitions for f in p["files"]]
        manifest = {
            "run_id": layout.run_id,
            "extract_date": layout.extract_date,
            "source_dataset": DATASET_ID,
            "schema_version": SCHEMA_VERSION,
            "partitions": [
                {k: p[k] for k in ("partition", "bounds", "row_count", "checksum")} | {"last_position": p["after"]}
                for p in partitions
            ],
            "files": files,
            "file_count": len(files),
            "row_count": sum(f["rows"] for f in files),
            "started_at": started_at,
            "completed_at": _iso(self._clock()),
        }
        if not self._store.put_if_absent(layout.manifest_key, _json_bytes(manifest)):
            return self._stored_manifest(layout) or manifest
        return manifest

    def _collect_partition(self, layout: "RunLayout", window: Window, lease: Lease, stop_at: datetime | None) -> dict:
        key = partition_checkpoint_key(layout.run_id, window)
        stored = self._store.get(key)
        checkpoint = json.loads(stored) if stored is not None else _new_checkpoint(window)

        # Only an empty page ends a partition, so completion never depends on
        # the page size of the run that wrote earlier files.
        while checkpoint["state"] != "complete":
            if stop_at is not None and self._clock() >= stop_at:
                raise RunPaused(f"run {layout.run_id} paused in {window.id} after {len(checkpoint['files'])} files")
            lease.renew()
            after = tuple(checkpoint["after"]) if checkpoint["after"] else None
            rows = self._source.fetch_page(window, after, self._page_size)  # type: ignore[arg-type]
            if not rows:
                checkpoint["state"] = "complete"
                self._store.put(key, _json_bytes(checkpoint))
                break
            file_key = layout.data_key(window, len(checkpoint["files"]))
            data = _encode_file(rows, layout.run_id, self._clock())
            if not self._store.put_if_absent(file_key, data):
                # Stored before a crash: keep it and continue from its last row.
                data = self._store.get(file_key) or b""

            rows_in_file, last = _file_stats(data, window)
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
    channel: str

    def partition_prefix(self, window: Window) -> str:
        """The folder holding one partition's data files."""
        return f"data/{self.channel}/extract_date={self.extract_date}/run_id={self.run_id}/{window.id}/"

    def data_key(self, window: Window, index: int) -> str:
        return f"{self.partition_prefix(window)}part-{index:05d}.ndjson.gz"

    @property
    def manifest_key(self) -> str:
        return f"manifests/extract_date={self.extract_date}/run_id={self.run_id}/manifest.json"


def run_state_key(run_id: str) -> str:
    return f"checkpoints/runs/{run_id}/run.json"


def partition_checkpoint_key(run_id: str, window: Window) -> str:
    return f"checkpoints/runs/{run_id}/partitions/{window.id}.json"


def _new_checkpoint(window: Window) -> dict:
    return {
        "partition": window.id,
        "bounds": window.bounds,
        "order": ", ".join(window.order),
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


def _file_stats(data: bytes, window: Window) -> tuple[int, PageCursor]:
    """Row count and keyset position of a file's last row."""
    lines = gzip.decompress(data).decode().splitlines()
    last = json.loads(lines[-1])
    source_fields = {":updated_at": last["source_updated_at"], **last["payload"]}
    first, key = (source_fields[f] for f in window.order)
    return len(lines), (first, key)


def _parse_source_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _source_timestamp(value: str | datetime) -> str:
    """A `:updated_at` value in the source's own format, 2026-10-23T01:33:15.966Z."""
    moment = _parse_source_timestamp(value) if isinstance(value, str) else value
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True).encode()
