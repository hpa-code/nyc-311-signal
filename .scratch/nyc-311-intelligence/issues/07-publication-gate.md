# 07: Publication gate

**What to build:** The dashboard only ever shows one complete, validated Publication. When a run fails, it keeps the last valid Publication and shows a stale-data warning.

**Blocked by:** 06 (Daily cursor collection and auto-ingest)

**Status:** ready-for-agent

**Mirror:** GitHub #8

- [ ] `OPS.PUBLICATIONS` records each release; outputs are versioned by publication ID
- [ ] Task graph: readiness, then validate and dedupe, then merge, then downstream steps, then the publication check, then publish or hold
- [ ] The current-Publication pointer moves only after the checks pass
- [ ] Dashboard shows the Publication timestamp and a stale-data banner when the latest run failed; missing scores are never filled in
- [ ] Seam 2 test: a partial run never becomes the current Publication
- [ ] Demo: force a failure and the dashboard shows the previous Publication with a warning
