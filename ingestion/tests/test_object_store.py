"""Both object stores honour the same contract the collector relies on."""

import boto3
import pytest
from moto import mock_aws

from ingestion.object_store import LocalObjectStore, S3ObjectStore


@pytest.fixture(params=["local", "s3"])
def store(request, tmp_path):
    if request.param == "local":
        yield LocalObjectStore(tmp_path)
        return
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-2")
        client.create_bucket(Bucket="dev-nyc311-test", CreateBucketConfiguration={"LocationConstraint": "us-east-2"})
        yield S3ObjectStore(client, "dev-nyc311-test")


def test_missing_key_reads_as_none(store):
    assert store.get("data/nothing-here") is None


def test_put_if_absent_creates_once_and_never_overwrites(store):
    assert store.put_if_absent("data/a/part-00000.ndjson.gz", b"first") is True
    assert store.put_if_absent("data/a/part-00000.ndjson.gz", b"second") is False
    assert store.get("data/a/part-00000.ndjson.gz") == b"first"


def test_put_replaces_mutable_control_files(store):
    store.put("checkpoints/runs/r/run.json", b"v1")
    store.put("checkpoints/runs/r/run.json", b"v2")
    assert store.get("checkpoints/runs/r/run.json") == b"v2"
