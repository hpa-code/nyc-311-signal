# 10: Labels and Modeling Cohort

**What to build:** Every eligible Service Request gets a trustworthy Label or an explicit, counted reason for having none, and the Modeling Cohort is defined (NYPD excluded per ADR 0001).

**Blocked by:** 08 (Full backfill from 2025-01)

**Status:** ready-for-agent

**Mirror:** GitHub #11

- [ ] Closure at or before 168h gives 0; after 168h gives 1; confirmed open after 168h gives 1
- [ ] Labels are assigned only from a trusted Observation after the horizon plus the reporting allowance (starting at 2 days)
- [ ] Date-Only Closures are compared by New York calendar day; one on the horizon day gets no Label
- [ ] Reopened Requests, Closures before creation and status/closure conflicts are excluded and counted by reason
- [ ] Duplicate Closures are kept as 0 and their share is reported
- [ ] Modeling Cohort excludes NYPD
- [ ] Seam 2 boundary tests cover each rule above
