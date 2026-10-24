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


def _frame(sql: str, params: tuple = ()) -> pd.DataFrame:
    cur = _connection().cursor()
    cur.execute(sql, params)
    frame = cur.fetch_pandas_all()
    frame.columns = [c.lower() for c in frame.columns]
    return frame


@st.cache_data(ttl=STATUS_SECONDS)
def publication_status() -> pd.Series:
    return _frame("SELECT * FROM APP.PUBLICATION_STATUS").iloc[0]


# Read by ID, not from the current-Publication views, so every page reads one
# Publication even if the pointer moves between queries.
@st.cache_data(max_entries=2)
def overview_daily(publication_id: str) -> pd.DataFrame:
    return _frame(
        "SELECT created_date, requests, closed_requests, open_requests"
        " FROM APP.PUBLISHED_OVERVIEW_DAILY WHERE publication_id = %s ORDER BY created_date",
        (publication_id,),
    )


@st.cache_data(max_entries=2)
def data_coverage(publication_id: str) -> pd.Series:
    return _frame("SELECT * FROM APP.PUBLISHED_DATA_COVERAGE WHERE publication_id = %s", (publication_id,)).iloc[0]
