# 05: Request Versions, stale-overwrite guard and quarantine

**What to build:** Repeated or late Observations leave the Current Request correct, and malformed Observations are quarantined with a reason instead of breaking the load.

**Blocked by:** 04 (One-week slice: S3 to Snowflake to local dashboard)

**Status:** done

**Mirror:** GitHub #6

- [x] Request Versions keep only changed states (by payload hash)
- [x] The winning version is chosen by source `:updated_at`, then extraction time, then a deterministic tie-breaker; never by warehouse arrival time
- [x] An older Observation replayed later never overwrites a newer Current Request
- [x] Missing identifiers, invalid required dates and incompatible types become Quarantined Observations with reasons
- [x] Unknown Problems are kept, not dropped
- [x] Seam 2 tests cover duplicates, out-of-order arrival, replay and quarantine
- [x] Demo: replay an old Observation and the Current Request is unchanged

## Comments

**2026-10-24 (agent):** Done. Migration V006 adds:
- an append-only stream `RAW.NEW_OBSERVATIONS`
- `CLEAN.REQUEST_VERSIONS` and `CLEAN.QUARANTINED_RECORDS`
- `APP.QUARANTINE_SUMMARY`, plus version and quarantine counts in `APP.DATA_COVERAGE`

Processing (`snowflake/transformations/process/01–04`) runs as one transaction: quarantine, rebuild Request Versions for the affected Requests, then merge Current Requests with the stale guard. A version is a run of consecutive same-hash Observations ordered by `:updated_at`, then extraction time, then hash. Each version keeps its first and last point, so a late state that lands inside a run splits it.

- Seam 2: 6 new live tests (duplicates, out-of-order arrival, replay, a returning state, quarantine with 9 reasons, unknown Problems). A mutation check (disabling the changed-state collapse) failed 4 of them.
- Dev: 71,830 Observations became 71,830 versions, with 0 quarantined.
- Demo (dev): replayed an older Open Observation of Closed Request 70250323. The Current Request was unchanged (status, hash and `:updated_at`). The versions became [Open@2026-09-02, Closed@2026-09-09] and the Overview totals did not move (71,830 / 67,124 closed). The demo row was then removed from dev.
- Notes:
  - A stream's first read (SHOW_INITIAL_ROWS) returns only the rows present at its creation, so rows loaded after V006 are processed one run later.
  - A replay that carries an earlier extraction time moves First Observed earlier, which is correct for true replays.
- The spec-axis review was still pending at commit time; its fixes follow in a separate commit.
