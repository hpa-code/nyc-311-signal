# 16: Recovery drill and final docs

**What to build:** Demonstrate the whole system end to end, including recovery from failure, and document it for reviewers.

**Blocked by:** 12 (Admission scoring, Risk Queue and Request lookup), 13 (Weekly reconciliation, monitoring and Pipeline Health page), 15 (Continuous deployment on merge)

**Status:** ready-for-agent

**Mirror:** GitHub #17

- [ ] Recovery drill recorded: interrupt an extraction, resume, rebuild, and match an uninterrupted run; replay an old Observation without overwriting newer state
- [ ] End-to-end trace of one Request: S3 Observation, RAW, Current Request, feature snapshot, Admission Score, dashboard; then a later Closure updates state while the original score is preserved
- [ ] README with architecture, screenshots, setup, real metrics and limitations
- [ ] Model card (including duplicate-Closure share and NYPD exclusion), runbook, cost notes and shutdown instructions
- [ ] Completion checklist in the guide fully ticked
