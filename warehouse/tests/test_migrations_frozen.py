"""Applied migrations are never edited: every versioned migration in the
ledger (snowflake/migrations/CHECKSUMS) still has the checksum it was applied
with. Runs offline, so the pre-commit hook and CI catch an edit before
`warehouse.migrate` refuses it against a database."""

from warehouse.migrate import MIGRATIONS, file_checksum, read_ledger


def test_applied_migrations_are_unchanged():
    files = {p.stem.split("__", 1)[0]: p for p in MIGRATIONS.glob("V*__*.sql")}
    ledger = read_ledger()
    missing = sorted(set(ledger) - set(files))
    changed = sorted(v for v, checksum in ledger.items() if v in files and file_checksum(files[v]) != checksum)
    assert not missing, f"applied migrations deleted or renamed: {missing}"
    assert not changed, f"applied migrations edited: {changed}; restore them and add a new V migration instead"
