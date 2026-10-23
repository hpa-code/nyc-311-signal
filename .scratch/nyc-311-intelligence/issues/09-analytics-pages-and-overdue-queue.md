# 09: Analytics pages: Overview, Geographic and Overdue Queue

**What to build:** An analyst can explore submission and closure trends, backlog, geography and currently Overdue Requests from the published data.

**Blocked by:** 07 (Publication gate), 08 (Full backfill from 2025-01)

**Status:** ready-for-agent

**Mirror:** GitHub #10

- [ ] Daily Request stats by date, Problem, agency and geography; saved daily backlog snapshots
- [ ] Median and p90 durations shown with denominators, closure rates and unresolved counts
- [ ] Backlog labeled as observed (snapshots) or retrospective (reconstructed)
- [ ] Geographic comparisons within the same Problem first; cells with fewer than 20 Requests hidden; no addresses or raw coordinates
- [ ] Overdue Queue lists Requests open past the Seven-Day Horizon, including NYPD, separate from any prediction
- [ ] Pages read cached aggregates for the life of a Publication; times shown in New York time
