"""Dashboard reads: APP views only, through the read-only dashboard role.

The Publication status is re-read every few minutes; aggregates are cached per
publication_id, so they stay cached for the life of a Publication. Queries are
cut off after 60 s so the dashboard cannot hog the shared warehouse.
"""

import pandas as pd
import streamlit as st
from snowflake.connector import SnowflakeConnection

from warehouse.connection import connect
from warehouse.environment import DEV

ENV = DEV
STATUS_SECONDS = 300


@st.cache_resource
def _connection() -> SnowflakeConnection:
    return connect(ENV.dashboard_role, ENV.warehouse, ENV.database, statement_timeout_seconds=60)


def _frame(sql: str) -> pd.DataFrame:
    cur = _connection().cursor()
    cur.execute(sql)
    frame = cur.fetch_pandas_all()
    frame.columns = [c.lower() for c in frame.columns]
    return frame


@st.cache_data(ttl=STATUS_SECONDS)
def publication_status() -> pd.Series:
    return _frame("SELECT * FROM APP.PUBLICATION_STATUS").iloc[0]


# publication_id is the cache key: APP views always show the current Publication.
@st.cache_data(max_entries=2)
def overview_daily(publication_id: str) -> pd.DataFrame:
    return _frame(
        "SELECT created_date, requests, closed_requests, open_requests"
        " FROM APP.OVERVIEW_DAILY ORDER BY created_date"
    )


@st.cache_data(max_entries=2)
def data_coverage(publication_id: str) -> pd.Series:
    return _frame("SELECT * FROM APP.DATA_COVERAGE").iloc[0]
