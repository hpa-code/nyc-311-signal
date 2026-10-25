"""Apply reviewed SQL migrations (snowflake/migrations/V###__name.sql) in order.

Each migration runs once per database and is recorded in OPS.SCHEMA_MIGRATIONS
with its checksum. Editing an applied migration is refused: add a new one.

Repeatable migrations (R__name.sql: procedures, tasks) run after them, again
whenever their rendered text (including any included files) changes.

Applying to the dev database also rewrites snowflake/migrations/CHECKSUMS, the
ledger of applied versions that an offline test holds the files to, so an
edit to an applied migration fails before it reaches any database.

    uv run --env-file .env python -m warehouse.migrate                  # dev
    NYC311_ENV=prod uv run --env-file .env python -m warehouse.migrate  # prod
"""

import hashlib
import sys
from pathlib import Path

from snowflake.connector import SnowflakeConnection

from warehouse.connection import connect
from warehouse.environment import DEV, Environment, current
from warehouse.sql import SQL_ROOT, render

MIGRATIONS = SQL_ROOT / "migrations"
LEDGER = MIGRATIONS / "CHECKSUMS"
LEDGER_HEADER = (
    "# Versioned migrations applied to NYC311_DEV, with the sha256 they were applied with.\n"
    "# Written by `python -m warehouse.migrate`; checked offline by warehouse/tests/test_migrations_frozen.py.\n"
)


class MigrationChanged(Exception):
    pass


def migrate(conn: SnowflakeConnection, env: Environment) -> list[str]:
    """Apply pending migrations to env.database; returns the versions (and R__ names) applied."""
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
        checksum = file_checksum(path)
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

    for path in sorted(MIGRATIONS.glob("R__*.sql")):
        sql = render(path.read_text(), env.placeholders())
        checksum = hashlib.sha256(sql.encode()).hexdigest()
        if applied.get(path.stem) == checksum:
            continue
        conn.execute_string(sql)
        cur.execute("DELETE FROM OPS.SCHEMA_MIGRATIONS WHERE version = %s", (path.stem,))
        cur.execute(
            "INSERT INTO OPS.SCHEMA_MIGRATIONS (version, name, checksum) VALUES (%s, %s, %s)",
            (path.stem, path.stem.removeprefix("R__"), checksum),
        )
        newly_applied.append(path.stem)
    return newly_applied


def file_checksum(path: Path) -> str:
    return hashlib.sha256(path.read_text().encode()).hexdigest()


def read_ledger() -> dict[str, str]:
    """Version -> checksum of every versioned migration applied to dev."""
    lines = (line.split() for line in LEDGER.read_text().splitlines() if line and not line.startswith("#"))
    return {version: checksum for version, checksum in lines}


def write_ledger(conn: SnowflakeConnection) -> None:
    rows = conn.cursor().execute(
        "SELECT version, checksum FROM OPS.SCHEMA_MIGRATIONS WHERE version LIKE 'V%' ORDER BY version"
    ).fetchall()
    LEDGER.write_text(LEDGER_HEADER + "".join(f"{version} {checksum}\n" for version, checksum in rows))


def main() -> int:
    env = current()
    with connect(env.pipeline_role, env.warehouse) as conn:
        applied = migrate(conn, env)
        # The ledger records what dev has applied: the offline guard for edits.
        if env == DEV:
            write_ledger(conn)
    print(f"{env.database}: applied {applied or 'nothing (up to date)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
