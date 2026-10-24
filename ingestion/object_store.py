"""Object store interface: S3 in deployed code, a local directory in tests and dev."""

import fcntl
import hashlib
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


class ObjectStore(Protocol):
    def get(self, key: str) -> bytes | None:
        """The object's bytes, or None if it does not exist."""
        ...

    def put(self, key: str, data: bytes) -> None:
        """Create or replace. Only for mutable control files (checkpoints)."""
        ...

    def put_if_absent(self, key: str, data: bytes) -> bool:
        """Create only; False (and no write) if the key already exists."""
        ...

    def get_versioned(self, key: str) -> tuple[bytes, str] | None:
        """The object's bytes and its current version tag (ETag), or None."""
        ...

    def replace_if_match(self, key: str, data: bytes, etag: str) -> str | None:
        """Replace only if the object is still at version `etag`; returns the new
        tag, or None (and no write) if it changed or no longer exists."""
        ...


class LocalObjectStore:
    def __init__(self, root: Path | str):
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        return self._root / key

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.exists() else None

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def put_if_absent(self, key: str, data: bytes) -> bool:
        # Write aside, then hard-link into place: atomic, and fails if the
        # key exists, so a crash never leaves a truncated object behind.
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_bytes(data)
        try:
            os.link(tmp, path)
        except FileExistsError:
            return False
        finally:
            tmp.unlink()
        return True

    def get_versioned(self, key: str) -> tuple[bytes, str] | None:
        data = self.get(key)
        return (data, _local_etag(data)) if data is not None else None

    def replace_if_match(self, key: str, data: bytes, etag: str) -> str | None:
        path = self._path(key)
        if not path.exists():
            return None
        # One writer at a time compares and swaps, across processes.
        with open(path.with_name(f".{path.name}.lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if _local_etag(path.read_bytes()) != etag:
                return None
            self.put(key, data)
        return _local_etag(data)


def _local_etag(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class S3ObjectStore:
    """S3 bucket. Creates use a conditional put (If-None-Match: *), which the
    ingestion role requires for data/ and manifests/; replaces of control
    files that must not race (the lease) use If-Match."""

    def __init__(self, client: "S3Client", bucket: str):
        self._client = client
        self._bucket = bucket

    def get(self, key: str) -> bytes | None:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except self._client.exceptions.NoSuchKey:
            return None
        return response["Body"].read()

    def put(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data)

    def put_if_absent(self, key: str, data: bytes) -> bool:
        try:
            self._client.put_object(Bucket=self._bucket, Key=key, Body=data, IfNoneMatch="*")
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code")
            if code == "PreconditionFailed":
                return False
            raise
        return True

    def get_versioned(self, key: str) -> tuple[bytes, str] | None:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except self._client.exceptions.NoSuchKey:
            return None
        return response["Body"].read(), response["ETag"]

    def replace_if_match(self, key: str, data: bytes, etag: str) -> str | None:
        try:
            response = self._client.put_object(Bucket=self._bucket, Key=key, Body=data, IfMatch=etag)
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code")
            # 412: changed; 409: a concurrent conditional write won; 404: gone.
            if code in ("PreconditionFailed", "ConditionalRequestConflict", "NoSuchKey"):
                return None
            raise
        return response["ETag"]
