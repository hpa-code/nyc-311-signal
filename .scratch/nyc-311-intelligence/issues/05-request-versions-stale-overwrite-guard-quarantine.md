# 05: Request Versions, stale-overwrite guard and quarantine

**What to build:** Repeated or late Observations leave the Current Request correct, and malformed Observations are quarantined with a reason instead of breaking the load.

**Blocked by:** 04 (One-week slice: S3 to Snowflake to local dashboard)

**Status:** ready-for-agent

**Mirror:** GitHub #6

- [ ] Request Versions keep only changed states (by payload hash)
- [ ] The winning version is chosen by source `:updated_at`, then extraction time, then a deterministic tie-breaker; never by warehouse arrival time
- [ ] An older Observation replayed later never overwrites a newer Current Request
- [ ] Missing identifiers, invalid required dates and incompatible types become Quarantined Observations with reasons
- [ ] Unknown Problems are kept, not dropped
- [ ] Seam 2 tests cover duplicates, out-of-order arrival, replay and quarantine
- [ ] Demo: replay an old Observation and the Current Request is unchanged
