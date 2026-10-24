"""Apply reviewed SQL migrations (snowflake/migrations/V###__name.sql) in order.

Each migration runs once per database and is recorded in OPS.SCHEMA_MIGRATIONS
with its checksum. Editing an applied migration is refused: add a new one.

    uv run --env-file .env python -m warehouse.migrate
"""

import hashlib
import sys

from snowflake.connector import SnowflakeConnection

from warehouse.connection import connect
from warehouse.environment import DEV, Environment
from warehouse.sql import SQL_ROOT, render

MIGRATIONS = SQL_ROOT / "migrations"


class MigrationChanged(Exception):
    pass


def migrate(conn: SnowflakeConnection, env: Environment) -> list[str]:
    """Apply pending migrations to env.database; returns the versions applied."""
    cur = conn.cursor()
    cur.execute(f"USE DATABASE {env.database}")
    cur.execute("CREATE SCHEMA IF NOT EXISTS OPS")
    cur.execute(
        "CREATE TABLE IF NOT EXISTS OPS.SCHEMA_MIGRATIONS ("
        " version STRING NOT NULL, name STRING NOT NULL, checksum STRING NOT NULL,"
        " applied_at TIMESTAMP_TZ NOT NULL DEFAULT CURRENT_TIMESTAMP())"
    )
    applied = dict(cur.execute("SELECT version, checksum FROM OPS.SCHEMA_MIGRATIONS").fetchall())

    paths = sorted(MIGRATIONS.glob("V*__*.sql"))
    versions = [p.stem.split("__", 1)[0] for p in paths]
    if len(set(versions)) != len(versions):
        raise MigrationChanged(f"duplicate migration versions in {sorted(versions)}")

    newly_applied = []
    for path in paths:
        version, name = path.stem.split("__", 1)
        sql = path.read_text()
        checksum = hashlib.sha256(sql.encode()).hexdigest()
        if version in applied:
            if applied[version] != checksum:
                raise MigrationChanged(f"{path.name} changed after it was applied; add a new migration instead")
            continue
        conn.execute_string(render(sql, env.placeholders()))
        cur.execute(
            "INSERT INTO OPS.SCHEMA_MIGRATIONS (version, name, checksum) VALUES (%s, %s, %s)",
            (version, name, checksum),
        )
        newly_applied.append(version)
    return newly_applied


def main() -> int:
    env = DEV
    with connect(env.pipeline_role, env.warehouse) as conn:
        applied = migrate(conn, env)
    print(f"{env.database}: applied {applied or 'nothing (up to date)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
