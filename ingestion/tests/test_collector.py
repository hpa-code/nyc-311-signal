"""Seam 1: fake Socrata HTTP source in; files, manifests and checkpoints out."""

import gzip
import json
from datetime import date, datetime, timezone

import pytest

from ingestion.api_client import AuthError, SchemaError, SourceClient, SourceUnavailable
from ingestion.collector import Collector, Plan, creation_day_partitions
from ingestion.object_store import LocalObjectStore
from ingestion.tests.fake_socrata import FakeSocrata, SimulatedCrash

EXTRACTED_AT = datetime(2026, 9, 10, 6, 0, tzinfo=timezone.utc)
SEPT_1 = creation_day_partitions(date(2026, 9, 1), days=1)
ONE_DAY = Plan(run_id="one-day", partitions=SEPT_1)


def request(key: str, created: str, **fields) -> dict:
    row = {
        ":id": f"row-{key}",
        ":created_at": "2026-09-02T01:33:26.674Z",
        ":updated_at": "2026-09-08T01:33:26.619Z",
        ":version": f"rv-{key}",
        "unique_key": key,
        "created_date": created,
        "agency": "DSNY",
        "complaint_type": "Dirty Condition",
        "borough": "BROOKLYN",
        "status": "Open",
    }
    row.update(fields)
    return row


def make_collector(fake: FakeSocrata, store, page_size: int = 1000, sleeps=None, clock=lambda: EXTRACTED_AT) -> Collector:
    source = SourceClient(
        transport=fake,
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
        random=lambda: 1.0,
    )
    return Collector(source=source, store=store, clock=clock, page_size=page_size)


def read_observations(store: LocalObjectStore, manifest: dict) -> list[dict]:
    observations: list[dict] = []
    for f in manifest["files"]:
        data = store.get(f["key"])
        assert data is not None
        lines = gzip.decompress(data).decode().splitlines()
        observations.extend(json.loads(line) for line in lines)
    return observations


@pytest.fixture
def store(tmp_path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "bucket")


def test_one_day_collects_every_request_as_a_wrapped_observation(store):
    fake = FakeSocrata(
        [
            request("100", "2026-09-01T08:15:00.000"),
            request("101", "2026-09-01T23:59:59.000"),
            request("102", "2026-09-02T00:00:00.000"),  # next day: not in the plan
        ]
    )
    plan = Plan(run_id="slice-test", partitions=SEPT_1)

    manifest = make_collector(fake, store).run(plan)

    assert manifest["run_id"] == "slice-test"
    assert manifest["row_count"] == 2
    assert sum(f["rows"] for f in manifest["files"]) == 2
    observations = read_observations(store, manifest)
    assert [o["payload"]["unique_key"] for o in observations] == ["100", "101"]
    first = observations[0]
    assert first["run_id"] == "slice-test"
    assert first["extracted_at"] == "2026-09-10T06:00:00Z"
    assert first["source_dataset"] == "erm2-nwe9"
    assert first["schema_version"] == 1
    assert first["source_created_at"] == "2026-09-02T01:33:26.674Z"
    assert first["source_updated_at"] == "2026-09-08T01:33:26.619Z"
    assert len(first["payload_hash"]) == 64
    assert not any(k.startswith(":") for k in first["payload"])
    assert store.get("manifests/extract_date=2026-09-10/run_id=slice-test/manifest.json") is not None


def test_page_boundaries_inside_a_created_date_tie_neither_skip_nor_duplicate(store):
    tied = "2026-09-01T09:00:00.000"
    rows = [request(k, tied) for k in ("205", "201", "204", "202", "203")]
    rows += [request("300", "2026-09-01T08:00:00.000"), request("400", "2026-09-01T10:00:00.000")]
    fake = FakeSocrata(rows)
    plan = Plan(run_id="ties", partitions=SEPT_1)

    manifest = make_collector(fake, store, page_size=2).run(plan)

    keys = [o["payload"]["unique_key"] for o in read_observations(store, manifest)]
    assert keys == ["300", "201", "202", "203", "204", "205", "400"]
    assert manifest["row_count"] == 7
    assert [f["rows"] for f in manifest["files"]] == [2, 2, 2, 1]


def test_requests_missing_optional_fields_are_still_collected(store):
    sparse = {k: v for k, v in request("500", "2026-09-01T12:00:00.000").items() if k not in ("borough", "agency", "status")}
    fake = FakeSocrata([sparse, request("501", "2026-09-01T13:00:00.000", closed_date=None)])
    plan = Plan(run_id="sparse", partitions=SEPT_1)

    manifest = make_collector(fake, store).run(plan)

    observations = read_observations(store, manifest)
    assert [o["payload"]["unique_key"] for o in observations] == ["500", "501"]
    assert "borough" not in observations[0]["payload"]


def test_throttling_and_server_errors_back_off_with_jitter_then_recover(store):
    fake = FakeSocrata([request("600", "2026-09-01T12:00:00.000")])
    fake.fail_next(429, headers={"Retry-After": "5"})
    fake.fail_next(503, times=2)
    sleeps: list[float] = []
    plan = Plan(run_id="flaky", partitions=SEPT_1)

    manifest = make_collector(fake, store, sleeps=sleeps).run(plan)

    assert manifest["row_count"] == 1
    # Retry-After honoured; then exponential backoff (jitter fixed at 1.0 here).
    assert sleeps == [5.0, 2.0, 4.0]


def test_persistent_server_errors_give_up_without_a_manifest(store):
    fake = FakeSocrata([request("601", "2026-09-01T12:00:00.000")])
    fake.fail_next(500, times=50)
    plan = Plan(run_id="down", partitions=SEPT_1)

    with pytest.raises(SourceUnavailable):
        make_collector(fake, store).run(plan)

    assert store.get("manifests/extract_date=2026-09-10/run_id=down/manifest.json") is None


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_stops_immediately_without_retrying(store, status):
    fake = FakeSocrata([request("700", "2026-09-01T12:00:00.000")])
    fake.fail_next(status, body='{"code":"permission_denied","message":"Invalid app_token specified"}')

    with pytest.raises(AuthError, match="app_token"):
        make_collector(fake, store).run(ONE_DAY)

    assert len(fake.requests) == 1


def test_query_on_a_vanished_column_is_a_schema_break(store):
    fake = FakeSocrata([request("701", "2026-09-01T12:00:00.000")])
    fake.fail_next(400, body='{"message":"Query coordinator error: query.soql.no-such-column; No such column: created_date"}')

    with pytest.raises(SchemaError, match="no-such-column"):
        make_collector(fake, store).run(ONE_DAY)


@pytest.mark.parametrize("required", ["unique_key", ":updated_at", ":created_at"])
def test_rows_missing_a_required_field_are_a_schema_break_and_nothing_is_written(store, required):
    broken = {k: v for k, v in request("702", "2026-09-01T12:00:00.000").items() if k != required}
    fake = FakeSocrata([request("703", "2026-09-01T11:00:00.000"), broken])

    with pytest.raises(SchemaError, match=required):
        make_collector(fake, store).run(ONE_DAY)

    assert store.get("data/extract_date=2026-09-10/run_id=one-day/created-20260901T0000-20260902T0000/part-00000.ndjson.gz") is None


THREE_DAYS = creation_day_partitions(date(2026, 9, 1), days=3)


def three_days_of_requests() -> list[dict]:
    return [
        request(f"{day}{n:02d}", f"2026-09-0{day}T{n:02d}:00:00.000")
        for day in (1, 2, 3)
        for n in range(5)
    ]


def unique_keys(observations: list[dict]) -> list[str]:
    return [o["payload"]["unique_key"] for o in observations]


def test_resume_after_crash_redoes_only_incomplete_partitions_and_keeps_identities(tmp_path):
    reference_store = LocalObjectStore(tmp_path / "reference")
    reference = make_collector(FakeSocrata(three_days_of_requests()), reference_store, page_size=2).run(
        Plan(run_id="week", partitions=THREE_DAYS)
    )

    store = LocalObjectStore(tmp_path / "bucket")
    fake = FakeSocrata(three_days_of_requests())
    fake.crash_on_request(5)  # day 1: 3 pages + the empty page; crash on day 2's first
    with pytest.raises(SimulatedCrash):
        make_collector(fake, store, page_size=2).run(Plan(run_id="week", partitions=THREE_DAYS))
    assert store.get("manifests/extract_date=2026-09-10/run_id=week/manifest.json") is None

    fake.requests.clear()
    next_day = lambda: datetime(2026, 9, 11, 6, 0, tzinfo=timezone.utc)
    resumed = make_collector(fake, store, page_size=2, clock=next_day).run(Plan(run_id="week", partitions=THREE_DAYS))

    assert [f["key"] for f in resumed["files"]] == [f["key"] for f in reference["files"]]
    assert unique_keys(read_observations(store, resumed)) == unique_keys(read_observations(reference_store, reference))
    assert resumed["row_count"] == 15
    # Days 2 and 3 each take 3 pages + the empty page; complete day 1 is not refetched.
    assert len(fake.page_requests()) == 8


class CrashOnCheckpointStore:
    """Real local store that dies on the n-th checkpoint write."""

    def __init__(self, inner: LocalObjectStore, crash_on_checkpoint: int):
        self._inner = inner
        self._remaining = crash_on_checkpoint

    def get(self, key):
        return self._inner.get(key)

    def put_if_absent(self, key, data):
        return self._inner.put_if_absent(key, data)

    def put(self, key, data):
        if "/partitions/" in key:
            self._remaining -= 1
            if self._remaining == 0:
                raise SimulatedCrash()
        self._inner.put(key, data)


def test_file_stored_before_a_crash_is_kept_not_overwritten_and_not_duplicated(tmp_path):
    local = LocalObjectStore(tmp_path / "bucket")
    plan = Plan(run_id="adopt", partitions=SEPT_1)
    first_file = "data/extract_date=2026-09-10/run_id=adopt/created-20260901T0000-20260902T0000/part-00000.ndjson.gz"

    with pytest.raises(SimulatedCrash):
        make_collector(FakeSocrata(three_days_of_requests()), CrashOnCheckpointStore(local, 1), page_size=2).run(plan)
    original = local.get(first_file)
    assert original is not None

    later = lambda: datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc)
    manifest = make_collector(FakeSocrata(three_days_of_requests()), local, page_size=2, clock=later).run(plan)

    assert local.get(first_file) == original
    assert unique_keys(read_observations(local, manifest)) == ["100", "101", "102", "103", "104"]


def test_rerunning_a_complete_run_returns_its_manifest_without_fetching(store):
    plan = Plan(run_id="done", partitions=SEPT_1)
    first = make_collector(FakeSocrata(three_days_of_requests()), store).run(plan)

    fake = FakeSocrata(three_days_of_requests())
    again = make_collector(fake, store).run(plan)

    assert again == first
    assert fake.requests == []


def test_a_truncated_response_body_is_retried(store):
    fake = FakeSocrata([request("800", "2026-09-01T12:00:00.000")])
    fake.fail_next(200, body='[{"unique_key": "80')

    manifest = make_collector(fake, store).run(ONE_DAY)

    assert manifest["row_count"] == 1


def test_created_date_in_an_unexpected_format_is_a_schema_break(store):
    fake = FakeSocrata([request("801", "2026-09-01T12:00:00.000"), request("802", "2026-09-01T13:00")])

    with pytest.raises(SchemaError, match="created_date"):
        make_collector(fake, store).run(ONE_DAY)


def test_resuming_a_run_id_with_a_different_plan_is_refused(store):
    fake = FakeSocrata(three_days_of_requests())
    fake.crash_on_request(2)
    with pytest.raises(SimulatedCrash):
        make_collector(fake, store).run(Plan(run_id="week", partitions=THREE_DAYS))

    with pytest.raises(ValueError, match="new run ID"):
        make_collector(fake, store).run(Plan(run_id="week", partitions=SEPT_1))
