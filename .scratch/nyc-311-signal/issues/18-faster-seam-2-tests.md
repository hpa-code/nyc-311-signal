# 18: Faster Seam 2 tests

**What to build:** The Snowflake test suite runs in minutes rather than about 40, and two runs (or a run and the recovery drill) no longer block each other on one shared test database.

**Blocked by:** none

**Status:** needs-triage

- [ ] Build the test database from the migrations once per migration set, as a template; each session or worker gets a zero-copy `CLONE` of it instead of rebuilding
- [ ] Run Seam 2 tests in parallel (`pytest-xdist`), one cloned database per worker; clones are dropped afterwards
- [ ] `warehouse/drill.py` and CI's `warehouse-tests` use their own clone, so they can run beside a local suite
- [ ] Document fast subsets per area in `warehouse/tests/conftest.py`

## Comments

**2026-10-25 (agent, from the tickets 11–16 retro):**
- The full suite took 35–40 minutes on X-Small, mostly one pipeline run per test plus a full migration rebuild per session.
- Every run rebuilds `NYC311_DEV_TEST`, so the recovery drill and targeted reruns had to wait for it. One commit went in before its suite had finished.
- Clones of a template share storage, so they cost almost nothing until written. The X-Small warehouse is shared, so check how far parallel workers actually speed things up before choosing a worker count.
