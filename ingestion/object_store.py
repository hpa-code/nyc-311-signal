"""Object store interface: S3 in deployed code, a local directory in tests and dev."""

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


class S3ObjectStore:
    """S3 bucket. Creates use a conditional put (If-None-Match: *), which the
    ingestion role requires for data/ and manifests/."""

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
