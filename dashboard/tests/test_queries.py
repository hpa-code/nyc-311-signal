"""A dashboard read survives an expired Snowflake session by logging in again."""

import pandas as pd
import pytest
from snowflake.connector.errors import ReauthenticationRequest

from dashboard import queries


class FakeCursor:
    def __init__(self, expired: bool) -> None:
        self.expired = expired

    def execute(self, sql: str, params: tuple) -> None:
        if self.expired:
            raise ReauthenticationRequest(msg="Master token can never be renewed")

    def fetch_pandas_all(self) -> pd.DataFrame:
        return pd.DataFrame({"N": [1]})


class FakeConnection:
    def __init__(self, expired: bool) -> None:
        self.expired = expired

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.expired)


class CachedConnection:
    """Stands in for the st.cache_resource-wrapped _connection."""

    def __init__(self, *connections: FakeConnection) -> None:
        self.connections = list(connections)
        self.cleared = 0

    def __call__(self) -> FakeConnection:
        return self.connections[0]

    def clear(self) -> None:
        self.cleared += 1
        self.connections.pop(0)


def test_expired_session_reconnects_once(monkeypatch: pytest.MonkeyPatch) -> None:
    cached = CachedConnection(FakeConnection(expired=True), FakeConnection(expired=False))
    monkeypatch.setattr(queries, "_connection", cached)

    frame = queries._frame("SELECT 1")

    assert cached.cleared == 1
    assert list(frame.columns) == ["n"]


def test_still_expired_after_reconnect_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    cached = CachedConnection(FakeConnection(expired=True), FakeConnection(expired=True))
    monkeypatch.setattr(queries, "_connection", cached)

    with pytest.raises(ReauthenticationRequest):
        queries._frame("SELECT 1")
    assert cached.cleared == 1
