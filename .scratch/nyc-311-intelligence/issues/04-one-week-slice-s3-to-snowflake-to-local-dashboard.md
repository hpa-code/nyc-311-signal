# 04: One-week slice: S3 to Snowflake to local dashboard

**What to build:** The week of Observations flows into Snowflake and shows on a local Streamlit Overview page. First complete path from API to dashboard. Establishes Seam 2 (fixture Observations into `NYC311_DEV`, assertions on `APP.*` views).

**Blocked by:** 03 (One-week slice: NYC 311 API to S3)

**Status:** ready-for-agent

**Mirror:** GitHub #5

- [ ] `NYC311_DEV` with RAW, CLEAN, ANALYTICS, ML, OPS and APP schemas, applied through reviewed SQL migrations
- [ ] Storage integration, external stage and JSON file format; bulk COPY loads RAW with source filename, row number, load time, run ID and payload hash
- [ ] First Current Request table, one row per `unique_key`
- [ ] First `APP` overview view; dashboard role is read-only and limited to `APP`
- [ ] Local Streamlit Overview shows daily Request counts and data coverage
- [ ] Seam 2 harness loads fixtures into dev and asserts on `APP` views
- [ ] Demo: page count = manifest count = distinct `unique_key` count
