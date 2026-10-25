# 16: Recovery drill and final docs

**What to build:** Demonstrate the whole system end to end, including recovery from failure, and document it for reviewers.

**Blocked by:** 12 (Admission scoring, Risk Queue and Request lookup), 13 (Weekly reconciliation, monitoring and Pipeline Health page), 15 (Continuous deployment on merge)

**Status:** ready-for-agent

**Mirror:** GitHub #17

- [x] Recovery drill recorded: interrupt an extraction, resume, rebuild, and match an uninterrupted run; replay an old Observation without overwriting newer state
- [x] End-to-end trace of one Request: S3 Observation, RAW, Current Request, feature snapshot, Admission Score, dashboard; then a later Closure updates state while the original score is preserved
- [x] README with architecture, screenshots, setup, real metrics and limitations
- [x] Model card (including duplicate-Closure share and NYPD exclusion), runbook, cost notes and shutdown instructions
- [ ] Completion checklist in the guide fully ticked

## Comments

**2026-10-25 (agent): done except the deployment item.** Still blocked by 15, which waits on the owner.
- **Recovery drill** (`warehouse/drill.py`, record in `docs/recovery-drill.md`), run against the real source. Three creation days (34,506 rows), collected straight through and again with the collector killed after three pages and resumed:
  - identical Observations, none lost or repeated;
  - rebuilt separately, identical Current Requests and Overview;
  - a replayed older Observation became an earlier Request Version, and the Current Request stayed Closed.
  - The script exits 1 on any failed check.
  - The record says what the drill does not exercise (the readiness gate, the crash between a file and its checkpoint) and which Seam 1 and 2 tests cover those.
- **End-to-end trace** (`docs/end-to-end-trace.md`): Request 70856306, from its S3 Observation through RAW, its Request Version, the frozen snapshot, its Admission Score (0.832) and the dashboard. A later Closed Observation added a version while the score and its inputs stayed unchanged.
- **Docs:**
  - the root `README.md` (architecture, pages, four screenshots in `docs/screenshots/`, measured results, setup, limitations);
  - `docs/cost-and-shutdown.md`: measured AWS spend about $0 so far; the Snowflake credit queries need ACCOUNTADMIN;
  - the model card and runbook (earlier tickets).
- **Completion checklist** (`instruction.md` §19): 14 of 15 ticked. "Infrastructure and deployment are reproducible" waits for tickets 14 and 15 to be applied.
- **Fixes found while taking screenshots:**
  - Pipeline Health showed a pass age before any pass had run, and the text "None".
  - A run consumed with none of its Observations in the batch was credited with every Request Version it had ever made (the backfill: 6.27M). `R__readiness` now counts only the batch's Requests, and V024 corrects earlier rows.
- **Shared helper:** `rebuild_test_database()` moved into `warehouse/tests/fixtures.py`, used by the Seam 2 harness and the drill.
