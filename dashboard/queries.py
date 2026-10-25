"""Dashboard reads: APP views only, through the read-only dashboard role.

The Publication status is re-read every few minutes; aggregates are cached per
publication_id, so they stay cached for the life of a Publication. Queries are
cut off after 60 s so the dashboard cannot hog the shared warehouse.
"""

from typing import NamedTuple

import pandas as pd
import streamlit as st
from snowflake.connector import SnowflakeConnection

from warehouse.connection import connect
from warehouse.environment import current

ENV = current()
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


class GroupFilter(NamedTuple):
    """Problem, agency and borough to narrow to; None means all."""

    problem: str | None = None
    agency: str | None = None
    borough: str | None = None


# Per-day outputs and the Overdue Queue are summed over the groups a filter
# matches; duration rows are read as stored, since medians do not add up.
_GROUP_FILTER = " AND (%s IS NULL OR problem = %s) AND (%s IS NULL OR agency = %s) AND (%s IS NULL OR borough = %s)"
_ROLLUP_FILTER = " AND problem IS NOT DISTINCT FROM %s AND agency IS NOT DISTINCT FROM %s AND borough IS NOT DISTINCT FROM %s"


def _group_params(f: GroupFilter) -> tuple:
    return (f.problem, f.problem, f.agency, f.agency, f.borough, f.borough)


# Problem / agency / borough combinations with their Request counts, for the
# filters (from the all-time duration rollup, so no extra scan).
@st.cache_data(max_entries=2)
def groups(publication_id: str) -> pd.DataFrame:
    return _frame(
        "SELECT problem, agency, borough, requests FROM APP.PUBLISHED_DURATION_STATS"
        " WHERE publication_id = %s AND created_month IS NULL"
        " AND problem IS NOT NULL AND agency IS NOT NULL AND borough IS NOT NULL",
        (publication_id,),
    )


@st.cache_data(max_entries=32)
def daily_stats(publication_id: str, f: GroupFilter) -> pd.DataFrame:
    return _frame(
        "SELECT stat_date, SUM(submitted) AS submitted, SUM(closed) AS closed"
        " FROM APP.PUBLISHED_DAILY_REQUEST_STATS WHERE publication_id = %s" + _GROUP_FILTER
        + " GROUP BY stat_date ORDER BY stat_date",
        (publication_id, *_group_params(f)),
    )


@st.cache_data(max_entries=32)
def backlog(publication_id: str, f: GroupFilter) -> pd.DataFrame:
    return _frame(
        "SELECT backlog_date, kind, SUM(open_requests) AS open_requests"
        " FROM APP.PUBLISHED_BACKLOG_DAILY WHERE publication_id = %s" + _GROUP_FILTER
        + " GROUP BY backlog_date, kind ORDER BY backlog_date",
        (publication_id, *_group_params(f)),
    )


@st.cache_data(max_entries=32)
def durations(publication_id: str, f: GroupFilter) -> pd.DataFrame:
    """One row per creation month, plus the all-time row (created_month NULL) first."""
    return _frame(
        "SELECT * EXCLUDE publication_id FROM APP.PUBLISHED_DURATION_STATS WHERE publication_id = %s" + _ROLLUP_FILTER
        + " ORDER BY created_month NULLS FIRST",
        (publication_id, f.problem, f.agency, f.borough),
    )


@st.cache_data(max_entries=32)
def borough_durations(publication_id: str, problem: str | None) -> pd.DataFrame:
    """All-time durations per borough, within one Problem (None: all Problems)."""
    return _frame(
        "SELECT * EXCLUDE publication_id FROM APP.PUBLISHED_DURATION_STATS WHERE publication_id = %s"
        " AND created_month IS NULL AND agency IS NULL AND borough IS NOT NULL AND problem IS NOT DISTINCT FROM %s"
        " ORDER BY requests DESC",
        (publication_id, problem),
    )


@st.cache_data(max_entries=32)
def geo_cells(publication_id: str, problem: str) -> pd.DataFrame:
    return _frame(
        "SELECT * EXCLUDE publication_id FROM APP.PUBLISHED_GEO_CELLS WHERE publication_id = %s AND problem = %s",
        (publication_id, problem),
    )


@st.cache_data(max_entries=8)
def overdue_groups(publication_id: str) -> pd.DataFrame:
    return _frame(
        "SELECT agency, problem, borough, COUNT(*) AS requests FROM APP.PUBLISHED_OVERDUE_QUEUE"
        " WHERE publication_id = %s GROUP BY ALL",
        (publication_id,),
    )


@st.cache_data(max_entries=32)
def overdue_count(publication_id: str, f: GroupFilter) -> int:
    row = _frame(
        "SELECT COUNT(*) AS n FROM APP.PUBLISHED_OVERDUE_QUEUE WHERE publication_id = %s" + _GROUP_FILTER,
        (publication_id, *_group_params(f)),
    )
    return int(row["n"].iloc[0])


@st.cache_data(max_entries=64)
def overdue_page(publication_id: str, f: GroupFilter, page: int, page_size: int) -> pd.DataFrame:
    """One page of the Overdue Queue, oldest first."""
    return _frame(
        "SELECT unique_key, problem, agency, borough, status, created_at_local, hours_open"
        " FROM APP.PUBLISHED_OVERDUE_QUEUE WHERE publication_id = %s" + _GROUP_FILTER
        + " ORDER BY created_at_local, unique_key LIMIT %s OFFSET %s",
        (publication_id, *_group_params(f), page_size, page * page_size),
    )


# Model evaluations are written by hand (`python -m ml.train`), not per
# Publication: the latest is re-read with the status, each cached by version.
@st.cache_data(ttl=STATUS_SECONDS)
def latest_model_version() -> str | None:
    row = _frame("SELECT MAX_BY(model_version, evaluated_at) AS model_version FROM APP.MODEL_EVALUATIONS")
    value = row["model_version"].iloc[0]
    return None if pd.isna(value) else str(value)


@st.cache_data(max_entries=4)
def model_evaluation(model_version: str) -> pd.Series:
    return _frame("SELECT * FROM APP.MODEL_EVALUATIONS WHERE model_version = %s", (model_version,)).iloc[0]


@st.cache_data(max_entries=4)
def model_metrics(model_version: str) -> pd.DataFrame:
    return _frame("SELECT * EXCLUDE model_version FROM APP.MODEL_METRICS WHERE model_version = %s", (model_version,))


@st.cache_data(max_entries=2)
def label_summary(publication_id: str) -> pd.DataFrame:
    return _frame(
        "SELECT agency, label, no_label_reason, requests, duplicate_closures"
        " FROM APP.PUBLISHED_LABEL_SUMMARY WHERE publication_id = %s",
        (publication_id,),
    )


@st.cache_data(max_entries=8)
def risk_queue_groups(publication_id: str) -> pd.DataFrame:
    return _frame(
        "SELECT agency, problem, borough, COUNT(*) AS requests, COUNT(admission_score) AS scored"
        " FROM APP.PUBLISHED_RISK_QUEUE WHERE publication_id = %s GROUP BY ALL",
        (publication_id,),
    )


@st.cache_data(max_entries=32)
def risk_queue_count(publication_id: str, f: GroupFilter) -> int:
    row = _frame(
        "SELECT COUNT(*) AS n FROM APP.PUBLISHED_RISK_QUEUE WHERE publication_id = %s" + _GROUP_FILTER,
        (publication_id, *_group_params(f)),
    )
    return int(row["n"].iloc[0])


@st.cache_data(max_entries=64)
def risk_queue_page(publication_id: str, f: GroupFilter, page: int, page_size: int) -> pd.DataFrame:
    """One page of the Risk Queue, highest Admission Score first; Requests without one last."""
    return _frame(
        "SELECT unique_key, problem, agency, borough, created_date, admission_score"
        " FROM APP.PUBLISHED_RISK_QUEUE WHERE publication_id = %s" + _GROUP_FILTER
        + " ORDER BY admission_score DESC NULLS LAST, unique_key LIMIT %s OFFSET %s",
        (publication_id, *_group_params(f), page_size, page * page_size),
    )


# The Request lookup reads Request Versions up to the Publication's data time,
# so it shows what that Publication saw.
@st.cache_data(max_entries=64)
def request_versions(unique_key: str, data_as_of) -> pd.DataFrame:
    """The Request's versions seen by `data_as_of`, latest first."""
    return _frame(
        "SELECT * FROM APP.REQUEST_LOOKUP WHERE unique_key = %s AND valid_from_extracted_at <= %s"
        " ORDER BY valid_from_extracted_at DESC",
        (unique_key, data_as_of),
    )


@st.cache_data(max_entries=64)
def admission_score(unique_key: str, publication_id: str) -> pd.DataFrame:
    """The Request's Admission Score with provenance (no rows if it has none);
    `publication_id` only keys the cache."""
    return _frame("SELECT * FROM APP.ADMISSION_SCORES WHERE unique_key = %s", (unique_key,))


# Pipeline health is live operational state, not part of a Publication: a
# held Publication or a stuck run must show here. Re-read with the status.
@st.cache_data(ttl=STATUS_SECONDS)
def monitor_signals() -> pd.Series:
    return _frame("SELECT * FROM APP.MONITOR").iloc[0]


@st.cache_data(ttl=STATUS_SECONDS)
def recent_runs() -> pd.DataFrame:
    return _frame("SELECT * FROM APP.RUN_HEALTH ORDER BY first_seen_at DESC, run_id DESC LIMIT 30")


@st.cache_data(ttl=STATUS_SECONDS)
def publication_history() -> pd.DataFrame:
    return _frame("SELECT * FROM APP.PUBLICATION_HISTORY ORDER BY started_at DESC, publication_id DESC LIMIT 14")


@st.cache_data(max_entries=2)
def quarantine_summary(publication_id: str) -> pd.DataFrame:
    return _frame(
        "SELECT reason, observations FROM APP.PUBLISHED_QUARANTINE_SUMMARY WHERE publication_id = %s ORDER BY observations DESC",
        (publication_id,),
    )
