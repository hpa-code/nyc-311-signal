"""Dashboard reads: APP views only, through the read-only dashboard role.

Results are cached for an hour; the source publishes once a day. Queries are
cut off after 60 s so the dashboard cannot hog the shared warehouse.
"""

import pandas as pd
import streamlit as st
from snowflake.connector import SnowflakeConnection

from warehouse.connection import connect
from warehouse.environment import DEV

ENV = DEV
CACHE_SECONDS = 3600


@st.cache_resource
def _connection() -> SnowflakeConnection:
    return connect(ENV.dashboard_role, ENV.warehouse, ENV.database, statement_timeout_seconds=60)


def _frame(sql: str) -> pd.DataFrame:
    cur = _connection().cursor()
    cur.execute(sql)
    frame = cur.fetch_pandas_all()
    frame.columns = [c.lower() for c in frame.columns]
    return frame


@st.cache_data(ttl=CACHE_SECONDS)
def overview_daily() -> pd.DataFrame:
    return _frame(
        "SELECT created_date, requests, closed_requests, open_requests"
        " FROM APP.OVERVIEW_DAILY ORDER BY created_date"
    )


@st.cache_data(ttl=CACHE_SECONDS)
def data_coverage() -> pd.Series:
    return _frame("SELECT * FROM APP.DATA_COVERAGE").iloc[0]
