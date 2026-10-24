"""Source client for the NYC 311 dataset on the Socrata SODA API."""

import http.client
import json
import random as random_module
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

DATASET_ID = "erm2-nwe9"
DATASET_URL = f"https://data.cityofnewyork.us/resource/{DATASET_ID}.json"

# Present on every row; a row without them cannot be paginated or traced.
REQUIRED_FIELDS = ("unique_key", "created_date", ":created_at", ":updated_at")
# Floating timestamp, e.g. 2026-09-01T00:00:05.000 (NYC local, no offset).
FLOATING_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?")


RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class SourceError(Exception):
    """The source could not be read; collection must stop."""


class AuthError(SourceError):
    """The app token was rejected. Retrying cannot help."""


class SchemaError(SourceError):
    """The source no longer matches the schema this collector understands."""


class SourceUnavailable(SourceError):
    """Transient failures persisted past the retry budget."""


class TransportError(Exception):
    """Connection-level failure (DNS, reset, timeout). Always retryable."""


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    headers: dict


Transport = Callable[[str, dict, dict], Response]


def urllib_transport(url: str, params: dict, headers: dict, timeout: float = 60) -> Response:
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return Response(resp.status, resp.read(), dict(resp.headers))
    except urllib.error.HTTPError as err:
        return Response(err.code, err.read(), dict(err.headers))
    except (urllib.error.URLError, http.client.HTTPException, OSError) as err:
        raise TransportError(str(err)) from err


@dataclass(frozen=True)
class CreationWindow:
    """Requests whose `created_date` (NYC local, floating) is in [start, end)."""

    start: datetime
    end: datetime

    order = ("created_date", "unique_key")

    @property
    def id(self) -> str:
        return f"created-{self.start:%Y%m%dT%H%M}-{self.end:%Y%m%dT%H%M}"

    @property
    def month(self) -> str:
        """The creation month it starts in, YYYY-MM."""
        return f"{self.start:%Y-%m}"

    @property
    def bounds(self) -> dict:
        return {"created_from": self.start.isoformat(), "created_before": self.end.isoformat()}

    def where(self) -> str:
        return f"created_date >= {_timestamp_literal(self.start)} AND created_date < {_timestamp_literal(self.end)}"


@dataclass(frozen=True)
class UpdateWindow:
    """Requests created on or after `created_from` (NYC local) whose `:updated_at`
    is after `updated_after` (a source `:updated_at` value, UTC). Open-ended: a
    Request republished during the pass moves later in the order and is still reached."""

    updated_after: str
    created_from: datetime

    order = (":updated_at", "unique_key")

    @property
    def id(self) -> str:
        return f"updated-after-{self.updated_after.replace('-', '').replace(':', '')}"

    @property
    def bounds(self) -> dict:
        return {"updated_after": self.updated_after, "created_from": self.created_from.isoformat()}

    def where(self) -> str:
        return f":updated_at > {_quote(self.updated_after)} AND created_date >= {_timestamp_literal(self.created_from)}"


Window = CreationWindow | UpdateWindow

# Keyset position: the window's order fields of the last row already taken,
# e.g. (created_date, unique_key) or (:updated_at, unique_key).
PageCursor = tuple[str, str]


class SourceClient:
    def __init__(
        self,
        transport: Transport = urllib_transport,
        app_token: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        random: Callable[[], float] = random_module.random,
        max_attempts: int = 8,
        backoff_base: float = 1.0,
        backoff_cap: float = 120.0,
    ):
        self._transport = transport
        self._app_token = app_token
        self._sleep = sleep
        self._random = random
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap

    def fetch_page(self, window: Window, after: PageCursor | None, limit: int) -> list[dict]:
        """One page of the window's Requests in its stable order, after the keyset position `after`."""
        where = window.where()
        if after is not None:
            (first, key), (first_value, key_value) = window.order, after
            where += (
                f" AND ({first} > {_quote(first_value)}"
                f" OR ({first} = {_quote(first_value)} AND {key} > {_quote(key_value)}))"
            )
        rows = self._get(
            {
                "$select": ":*, *",
                "$where": where,
                "$order": ", ".join(window.order),
                "$limit": str(limit),
            }
        )
        for row in rows:
            missing = [f for f in REQUIRED_FIELDS if not row.get(f)]
            if missing:
                raise SchemaError(f"Request {row.get('unique_key', '?')} is missing required field(s) {missing}")
            if not FLOATING_TIMESTAMP.fullmatch(row["created_date"]):
                raise SchemaError(f"Request {row['unique_key']} has created_date {row['created_date']!r}")
        return rows

    def count_created(self, window: CreationWindow) -> int:
        rows = self._get({"$select": "count(*)", "$where": window.where()})
        return int(rows[0]["count"])

    def _get(self, params: dict) -> list[dict]:
        headers = {"Accept": "application/json"}
        if self._app_token:
            headers["X-App-Token"] = self._app_token
        failure, delay = "", 0.0
        for attempt in range(self._max_attempts):
            if attempt:
                self._sleep(delay)
            try:
                response = self._transport(DATASET_URL, params, headers)
            except TransportError as err:
                failure, delay = f"transport error: {err}", self._backoff(attempt)
                continue
            if response.status == 200:
                try:
                    return json.loads(response.body)
                except json.JSONDecodeError:
                    # A truncated body is a dropped connection, not a schema break.
                    failure, delay = "unparseable 200 body", self._backoff(attempt)
                    continue
            if response.status in RETRYABLE_STATUSES:
                failure = f"HTTP {response.status}"
                delay = max(self._backoff(attempt), _retry_after(response))
                continue
            detail = response.body[:500].decode(errors="replace")
            if response.status in (401, 403):
                raise AuthError(f"HTTP {response.status}: {detail}")
            if "no-such-column" in detail:
                raise SchemaError(f"HTTP {response.status}: {detail}")
            raise SourceError(f"HTTP {response.status}: {detail}")
        raise SourceUnavailable(f"gave up after {self._max_attempts} attempts; last failure: {failure}")

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff with full jitter."""
        return min(self._backoff_cap, self._backoff_base * 2**attempt) * self._random()


def _retry_after(response: Response) -> float:
    value = {k.lower(): v for k, v in response.headers.items()}.get("retry-after", "")
    try:
        return float(value)
    except ValueError:
        return 0.0


def _timestamp_literal(value: datetime) -> str:
    return _quote(value.strftime("%Y-%m-%dT%H:%M:%S"))


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
