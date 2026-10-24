"""A time-limited, exclusive lease on a name, kept as a control file in the object store.

    checkpoints/leases/<name>.json   {"owner", "renewed_at", "expires_at"}

Every change is a conditional write (create-if-absent, or replace-if-unchanged
since read), so two collectors can never both believe they hold it. A holder
that dies without releasing (a Lambda timeout) blocks others only until the
lease expires. A released lease is kept, expired, so the next holder takes it
over with the same conditional replace.
"""

import json
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from ingestion.object_store import ObjectStore


class LeaseHeld(Exception):
    """Another collector holds the lease; this one must not run."""


class LeaseLost(Exception):
    """The lease was taken over while this collector still worked under it."""


class Lease:
    def __init__(self, store: ObjectStore, name: str, clock: Callable[[], datetime], ttl: timedelta):
        self._store = store
        self._key = f"checkpoints/leases/{name}.json"
        self._clock = clock
        self._ttl = ttl
        self._owner = uuid.uuid4().hex
        self._etag: str | None = None

    def acquire(self) -> None:
        now = self._clock()
        record = self._record(now)
        current = self._store.get_versioned(self._key)
        if current is None:
            if self._store.put_if_absent(self._key, record):
                current = self._store.get_versioned(self._key)
                if current is not None and json.loads(current[0])["owner"] == self._owner:
                    self._etag = current[1]
                    return
            raise LeaseHeld(f"{self._key} was taken by another collector")
        data, etag = current
        held = json.loads(data)
        if datetime.fromisoformat(held["expires_at"]) > now:
            raise LeaseHeld(f"{self._key} is held by {held['owner']} until {held['expires_at']}")
        self._etag = self._store.replace_if_match(self._key, record, etag)
        if self._etag is None:
            raise LeaseHeld(f"{self._key} was taken by another collector")

    def renew(self) -> None:
        """Extend the lease from now; fails if anyone else has touched it."""
        assert self._etag is not None, "renew before acquire"
        self._etag = self._store.replace_if_match(self._key, self._record(self._clock()), self._etag)
        if self._etag is None:
            raise LeaseLost(f"{self._key} was taken over; stopping")

    def release(self) -> None:
        """Expire the lease now, unless someone else already holds it."""
        if self._etag is not None:
            self._store.replace_if_match(self._key, self._record(self._clock(), ttl=timedelta(0)), self._etag)
            self._etag = None

    def _record(self, now: datetime, ttl: timedelta | None = None) -> bytes:
        expires = now + (self._ttl if ttl is None else ttl)
        return json.dumps(
            {"owner": self._owner, "renewed_at": now.isoformat(), "expires_at": expires.isoformat()},
            indent=2,
        ).encode()
