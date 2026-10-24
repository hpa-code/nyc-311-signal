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

**2026-10-24 (agent): review fixes.**
Two-axis review of e2ea447. Fixes:
- **Small cells could leak.** `PUB_GEO_CELLS` published each cell's all-Problems count next to its per-Problem counts, so subtracting one from the other gave a count under 20. Cells are now published per Problem only. The map needs one Problem chosen; the borough table still offers All Problems.
- **Date-Only Closures** are now judged by New York calendar day (V014 `REQUEST_FACTS`):
  - On the creation day: counts as 0 hours, no longer as a Closure before creation.
  - On the horizon day: no known outcome, so left out of the 7-day rate.
  - Otherwise: within the horizon if before the horizon day.
- **Horizon boundary.** Overdue and "past the horizon" now mean strictly more than 168 hours. `hours_open` counts whole elapsed hours, not hour boundaries crossed.
- **Missing categories.** The pages now say that missing Problem, agency or borough show as "Unspecified".
- **Tests.**
  - The backlog test asserts only through `APP.PUBLISHED_BACKLOG_DAILY`: a held attempt, then a fixed next-day Publication still shows the earlier day's snapshot unchanged.
  - New cases: the Overdue boundary at 168 h + 1 s, and both Date-Only Closure cases.
- **Smells.**
  - The H3 expression became one function, `CLEAN.NYC_H3_CELL`.
  - `REQUEST_FACTS` computes the 168 h check once (`past_horizon`).
  - `GroupFilter` replaces the problem/agency/borough triple.
  - `dashboard/common.py` → `dashboard/ui.py`, with shared `durations_table`, `horizontal_bars`, `MIN_REQUESTS` and a theme-aware map ramp.
  - The Overdue count comes from SQL (`overdue_count`), not a filter in Python.
  - "Unresolved" → "Still open" (GLOSSARY avoids "resolution").
- **Dev:** V014 applied and published as `20261024T212958-4d2c44f8`. It has no all-Problems cells, 6 same-day Date-Only Closures are now timed, and about 5k horizon-day ones are out of the 7-day rate.
- **Left as is:**
  - Borough groups are not held to the 20 minimum in `PUB_DURATION_STATS` or the daily stats. instruction.md §12 allows "boroughs or aggregated geographic cells with at least 20 requests each", so the minimum binds cells. The page still hides boroughs under 20 as too small to compare.
  - Table names keep the `PUB_` prefix that every per-Publication output uses (`PUB_DAILY_REQUEST_STATS`, not the planning table's `DAILY_REQUEST_STATS`). `DAILY_BACKLOG` matches the plan.
  - The comment `-- unresolved` stays in V013: applied migrations are never edited.
  - Kept, though the ticket did not ask for them: weekly trend lines, the duration breakdown by month and agency, the top-10 agency chart, and Status and Days open on the Overdue Queue. The publish rebuild-on-procedure-change also stays, since deploys need it.
  - `OPS.PUBLISH()` keeps growing by one INSERT, check and prune per output. That is the existing pattern; splitting it would be its own change.
