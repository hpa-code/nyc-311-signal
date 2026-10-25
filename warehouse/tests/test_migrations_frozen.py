"""Applied migrations are never edited: every versioned migration in the
ledger (snowflake/migrations/CHECKSUMS) still has the checksum it was applied
with. Runs offline, so the pre-commit hook and CI catch an edit before
`warehouse.migrate` refuses it against a database."""

import subprocess

from warehouse.migrate import MIGRATIONS, file_checksum, read_ledger, uncommitted_migrations


def test_applied_migrations_are_unchanged():
    files = {p.stem.split("__", 1)[0]: p for p in MIGRATIONS.glob("V*__*.sql")}
    ledger = read_ledger()
    missing = sorted(set(ledger) - set(files))
    changed = sorted(v for v, checksum in ledger.items() if v in files and file_checksum(files[v]) != checksum)
    assert not missing, f"applied migrations deleted or renamed: {missing}"
    assert not changed, f"applied migrations edited: {changed}; restore them and add a new V migration instead"


def test_only_committed_migrations_may_be_applied(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    git = lambda *args: subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    (migrations / "V001__applied.sql").write_text("SELECT 1;")
    (migrations / "CHECKSUMS").write_text("V001 abc\n")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "init")
    assert uncommitted_migrations(migrations) == []

    # A new migration and an edited one block; the ledger the tool writes does not.
    (migrations / "V002__new.sql").write_text("SELECT 2;")
    (migrations / "V001__applied.sql").write_text("SELECT 11;")
    (migrations / "CHECKSUMS").write_text("V001 abc\nV002 def\n")
    assert uncommitted_migrations(migrations) == ["migrations/V001__applied.sql", "migrations/V002__new.sql"]
