"""Seam 2 harness: NYC311_DEV_TEST, rebuilt from the migrations each session.

Needs Snowflake credentials, e.g. `uv run --env-file .env pytest warehouse`;
skipped otherwise.
"""

import pytest

from warehouse.connection import connect, credentials_available
from warehouse.environment import DEV
from warehouse.migrate import migrate
from warehouse.tests.fixtures import FIXTURE_STAGE

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
    env = DEV.with_database(DEV.test_database)
    conn = connect(env.pipeline_role, env.warehouse, env.database)
    try:
        cur = conn.cursor()
        for schema in SCHEMAS:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        migrate(conn, env)
        cur.execute(f"CREATE STAGE {FIXTURE_STAGE}")
        yield conn, env
    finally:
        conn.close()


@pytest.fixture
def empty_db(test_env):
    """A connection to the test database with RAW, CLEAN, OPS and the fixture stage emptied."""
    conn, _ = test_env
    cur = conn.cursor()
    for table in ("RAW.RAW_311_OBSERVATIONS", "OPS.RUN_MANIFESTS", "CLEAN.SERVICE_REQUESTS"):
        cur.execute(f"TRUNCATE TABLE {table}")
    cur.execute(f"REMOVE @{FIXTURE_STAGE}")
    return conn
