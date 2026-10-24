# 17: Tableau Public companion

**What to build:** A public, interactive Tableau Public workbook showing Request trends, closure and backlog by borough, agency, Problem and channel, built from an aggregate export of the APP views. See ADR 0002.

**Blocked by:** none for a first version; 08 (Full backfill from 2025-01) for the version worth sharing

**Status:** ready-for-human

**Mirror:** none

- [x] `APP.TABLEAU_DAILY` secure view: counts by creation day, borough, agency, Problem and channel; readable by the dashboard role only
- [x] `tableau/export.py` writes `tableau/out/nyc311_daily.csv` and `nyc311_coverage.csv` through the dashboard role
- [x] Seam 2 test: export contains counts only, no Request detail
- [ ] Workbook built in Tableau Public Desktop following `tableau/README.md` (human: GUI authoring)
- [ ] Dashboard has date, borough, agency and Problem filters, a cross-filter action, and a "data as of" caption
- [ ] Closure-rate views hide cells with fewer than 20 Requests
- [ ] Published to Tableau Public; link added to the project docs
- [ ] Re-exported and republished after the full backfill (08)
