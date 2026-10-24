# 09: Analytics pages: Overview, Geographic and Overdue Queue

**What to build:** An analyst can explore submission and closure trends, backlog, geography and currently Overdue Requests from the published data.

**Blocked by:** 07 (Publication gate), 08 (Full backfill from 2025-01)

**Status:** done

**Mirror:** GitHub #10

- [x] Daily Request stats by date, Problem, agency and geography; saved daily backlog snapshots
- [x] Median and p90 durations shown with denominators, closure rates and unresolved counts
- [x] Backlog labeled as observed (snapshots) or retrospective (reconstructed)
- [x] Geographic comparisons within the same Problem first; cells with fewer than 20 Requests hidden; no addresses or raw coordinates
- [x] Overdue Queue lists Requests open past the Seven-Day Horizon, including NYPD, separate from any prediction
- [x] Pages read cached aggregates for the life of a Publication; times shown in New York time

## Comments

**2026-10-24 (agent): built.**
- **Outputs.** `V013__analytics_pages.sql` adds five per-Publication tables, which `OPS.PUBLISH()` builds in its transaction: `PUB_DAILY_REQUEST_STATS` (submissions by creation date and Closures by Closure date, per Problem/agency/borough), `PUB_BACKLOG_DAILY`, `PUB_DURATION_STATS` (a CUBE over creation month, Problem, agency and borough), `PUB_GEO_CELLS` and `PUB_OVERDUE_QUEUE`. The dashboard reads them by ID through `APP.PUBLISHED_*`. `ANALYTICS.REQUEST_FACTS` is a view of the facts they share: hours to Closure, horizon passed, Overdue, and "as of" = the latest extraction.
- **Backlog.** `ANALYTICS.DAILY_BACKLOG` holds one saved snapshot per New York day of the source's latest update. It comes from a Publication that passed its checks. A held attempt's snapshot is dropped, and snapshots are never pruned. The Retrospective series is a running sum of creations and Closures per group, rebuilt every Publication. The chart labels the two series and explains how they differ.
- **Durations.** Median and p90 come with their denominators: Closures with a usable time, the 7-day closure rate over Requests past the horizon, and unresolved counts.
- **Geography.** Comparisons are within one Problem first. `CLEAN.SERVICE_REQUESTS.h3_cell` stores the H3 resolution-8 cell (about 0.7 km²), computed in the merge; locations outside an NYC box get none. Cells with fewer than 20 Requests fold into one `hidden` row per Problem. Coordinates never leave CLEAN. Boroughs with fewer than 20 Requests are hidden on the page too.
- **Overdue Queue.** Requests open past 168 h as of the latest extraction, from every agency including NYPD. It shows Request ID, Problem, agency, borough, status, created time (New York) and days open. The page is paginated and ordered oldest first.
- **Checks added:** daily stats, durations and geo cells each count every Request; the observed snapshot equals the open count; no drawn cell is under 20; Overdue ≤ open.
- **Rebuild on change.** The publish input hash now includes `R__publication`'s migration checksum. Before this, a changed procedure returned `unchanged` until new data arrived.
- **Dev:** V013 applied to `NYC311_DEV`. Publication `20261024T205612-232ba421` built from 6.9M Requests in about 60 s, and every check passed. On Oct 23, 248,693 Requests were observed open against 248,694 retrospective. The Overdue Queue holds 224,026 Requests. 6,763,587 Requests have a cell.
- **Tests:** 4 new Seam 2 tests in `warehouse/tests/test_analytics.py` (Overdue boundary at exactly 168 h with NYPD, durations and denominators, one observed snapshot per day surviving a held attempt, small-cell hiding and no coordinates). The dashboard-role test covers the new views.
