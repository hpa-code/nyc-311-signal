"""Export the Tableau Public companion's data as CSV (ADR 0002).

Reads the current Publication by ID from APP views, through the read-only
dashboard role, and writes tableau/out/nyc311_daily.csv and
tableau/out/nyc311_coverage.csv. Tableau Public cannot refresh these itself:
re-export and republish the workbook.

    uv run --env-file .env python -m tableau.export
"""

import csv
import sys
from pathlib import Path

from snowflake.connector import SnowflakeConnection

from warehouse.connection import connect
from warehouse.environment import DEV

OUT = Path(__file__).resolve().parent / "out"
# Both files come from one Publication, even if the pointer moves mid-export.
EXPORTS = {
    "nyc311_daily.csv": (
        "SELECT * EXCLUDE publication_id FROM APP.PUBLISHED_TABLEAU_DAILY WHERE publication_id = %s"
        " ORDER BY created_date, borough, agency, problem, channel"
    ),
    "nyc311_coverage.csv": (
        "SELECT publication_id, first_created_date, last_created_date, current_requests,"
        " CONVERT_TIMEZONE('America/New_York', latest_extracted_at)::TIMESTAMP_NTZ AS latest_extracted_at_new_york"
        " FROM APP.PUBLISHED_DATA_COVERAGE WHERE publication_id = %s"
    ),
}


class NoPublication(Exception):
    pass


def export(conn: SnowflakeConnection, out: Path = OUT) -> dict[str, int]:
    """Write each export to `out`; returns rows written per file."""
    row = conn.cursor().execute("SELECT publication_id FROM APP.PUBLICATION_STATUS").fetchone()
    assert isinstance(row, tuple)
    [publication_id] = row
    if publication_id is None:
        raise NoPublication("no Publication yet; nothing to export")
    out.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, sql in EXPORTS.items():
        cur = conn.cursor().execute(sql, (publication_id,))
        rows = cur.fetchall()
        with open(out / name, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([c[0].lower() for c in cur.description or ()])
            writer.writerows(rows)
        written[name] = len(rows)
    return written


def main() -> int:
    env = DEV
    with connect(env.dashboard_role, env.warehouse, env.database, statement_timeout_seconds=60) as conn:
        written = export(conn)
    for name, count in written.items():
        print(f"{OUT / name}: {count:,} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
