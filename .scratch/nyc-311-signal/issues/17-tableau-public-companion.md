# 17: Tableau Public companion

**What to build:** A public, interactive Tableau Public workbook showing Request trends, closure and backlog by borough, agency, Problem and channel, built from an aggregate export of the APP views. See ADR 0002.

**Blocked by:** none for a first version; 08 (Full backfill from 2025-01) for the version worth sharing

**Status:** done

**Mirror:** none

- [x] `APP.TABLEAU_DAILY` secure view: counts by creation day, borough, agency, Problem and channel; readable by the dashboard role only
- [x] `tableau/export.py` writes `tableau/out/nyc311_daily.csv` and `nyc311_coverage.csv` through the dashboard role
- [x] Seam 2 test: export contains counts only, no Request detail
- [x] Workbook built in Tableau Public Desktop following `tableau/README.md` (human: GUI authoring)
- [x] Dashboard has date, borough, agency and Problem filters, a cross-filter action, and a "data as of" caption
- [x] Closure-rate views hide cells with fewer than 20 Requests
- [x] Published to Tableau Public; link added to the project docs
- [x] Re-exported and republished after the full backfill (08)

## Comments

**2026-10-25 (agent): published.** https://public.tableau.com/app/profile/hari.acharya3471/viz/NYC311Signal/NYC311SignalRequestsandClosure
- Built in Tableau Public web authoring from the post-backfill export (Publication `20261025T074512-450456ac`: 6,901,995 Requests created 2025-01-01 to 2026-10-24).
- Departures from `tableau/README.md`:
  - No Problem quick filter: on this workbook it is the Top 15 filter, so applying it to all sheets cut every sheet to 15 Problems. Clicking a Top Problems bar filters the dashboard instead.
  - Requests per day is a stacked area, not stacked bars: 662 daily bars render as a smear at dashboard size.
  - The "as of" line is static caption text from `nyc311_coverage.csv`, not a second data source. Edit it on each refresh.
  - Colours are Tableau's default blue and orange; web authoring has no hex input.
  - Layout is Tableau's automatic tiling; the caption sits in the narrow right column.
