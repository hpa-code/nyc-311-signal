"""Seam 2 harness: NYC311_DEV_TEST, rebuilt from the migrations each session.

Needs Snowflake credentials, e.g. `uv run --env-file .env pytest warehouse`;
skipped otherwise.
"""

import pytest

from warehouse.connection import connect, credentials_available
from warehouse.environment import DEV
from warehouse.migrate import migrate
from warehouse.pipeline import run
from warehouse.tests.fixtures import FIXTURE_STAGE, STAGES

SCHEMAS = ("RAW", "CLEAN", "ANALYTICS", "ML", "OPS", "APP")


def pytest_collection_modifyitems(config, items):
    if credentials_available():
        return
    skip = pytest.mark.skip(reason="no Snowflake credentials (run with --env-file .env)")
    for item in items:
        if "warehouse/tests" in str(item.fspath):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def test_env():
    env = DEV.for_tests(manifests_stage=STAGES.manifests)
    conn = connect(env.pipeline_role, env.warehouse, env.database)
    try:
        cur = conn.cursor()
        for schema in SCHEMAS:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        migrate(conn, env)
        cur.execute(f"CREATE STAGE {FIXTURE_STAGE}")
        # A stream's first read returns only the rows present when it was created
        # (none here); later rows wait for the next read. Take that first read now.
        run(conn, STAGES)
        yield conn, env
    finally:
        conn.close()


@pytest.fixture
def empty_db(test_env):
    """A connection to the test database with RAW, CLEAN, OPS, Publications and the fixture stage emptied."""
    # The stream is drained by every pipeline run, so emptying RAW leaves it empty too.
    # TRUNCATE also clears COPY load history, so fixture files with reused names load again.
    conn, _ = test_env
    cur = conn.cursor()
    # An append-only stream still returns rows deleted after they were added:
    # consume whatever an earlier test left unprocessed.
    cur.execute("INSERT INTO CLEAN.WORK_AFFECTED_REQUESTS SELECT NULL FROM RAW.NEW_OBSERVATIONS WHERE FALSE")
    for table in (
        "RAW.RAW_311_OBSERVATIONS", "RAW.BULK_LANDING", "OPS.RUN_MANIFESTS", "OPS.PIPELINE_RUNS",
        "CLEAN.SERVICE_REQUESTS", "CLEAN.REQUEST_VERSIONS", "CLEAN.QUARANTINED_RECORDS",
        "CLEAN.OBSERVATION_POINTS", "OPS.PUBLICATIONS", "OPS.CURRENT_PUBLICATION",
        "ANALYTICS.PUB_OVERVIEW_DAILY", "ANALYTICS.PUB_DATA_COVERAGE", "ANALYTICS.PUB_QUARANTINE_SUMMARY",
        "ANALYTICS.PUB_TABLEAU_DAILY", "ANALYTICS.PUB_DAILY_REQUEST_STATS", "ANALYTICS.DAILY_BACKLOG",
        "ANALYTICS.PUB_BACKLOG_DAILY", "ANALYTICS.PUB_DURATION_STATS", "ANALYTICS.PUB_GEO_CELLS",
        "ANALYTICS.PUB_OVERDUE_QUEUE", "ANALYTICS.PUB_LABEL_SUMMARY", "ML.TRAINING_LABELS",
    ):
        cur.execute(f"TRUNCATE TABLE {table}")
    cur.execute("UPDATE OPS.STEP_STATUS SET last_success_at = NULL, last_failure_at = NULL, last_error = NULL")
    cur.execute(f"REMOVE @{FIXTURE_STAGE}")
    return conn
