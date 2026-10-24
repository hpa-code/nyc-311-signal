# 04: One-week slice: S3 to Snowflake to local dashboard

**What to build:** The week of Observations flows into Snowflake and shows on a local Streamlit Overview page. First complete path from API to dashboard. Establishes Seam 2 (fixture Observations into `NYC311_DEV`, assertions on `APP.*` views).

**Blocked by:** 03 (One-week slice: NYC 311 API to S3)

**Status:** done

**Mirror:** GitHub #5

- [x] `NYC311_DEV` with RAW, CLEAN, ANALYTICS, ML, OPS and APP schemas, applied through reviewed SQL migrations
- [x] Storage integration, external stage and JSON file format; bulk COPY loads RAW with source filename, row number, load time, run ID and payload hash
- [x] First Current Request table, one row per `unique_key`
- [x] First `APP` overview view; dashboard role is read-only and limited to `APP`
- [x] Local Streamlit Overview shows daily Request counts and data coverage
- [x] Seam 2 harness loads fixtures into dev and asserts on `APP` views
- [x] Demo: page count = manifest count = distinct `unique_key` count

## Comments

**2026-10-23 (agent):** Done. API → S3 → Snowflake → local Streamlit Overview works end to end.

- Account-level objects are in `snowflake/setup_dev.sql`, run once by the owner as ACCOUNTADMIN: the X-Small `NYC311_WH` warehouse (60 s auto-suspend, 600 s statement timeout), roles `NYC311_DEV_PIPELINE` and `NYC311_DEV_DASHBOARD`, databases `NYC311_DEV` and `NYC311_DEV_TEST`, and storage integration `NYC311_DEV_S3`.
- Migrations V001–V005 are applied by `warehouse/migrate.py` (checksummed; edited or duplicate versions are refused). Bulk COPY loads go through `warehouse/pipeline.py`, and the MERGE into `CLEAN.SERVICE_REQUESTS` lets the latest observed version win by `:updated_at`, then `extracted_at`, then `payload_hash`.
- The dashboard role reads secure `APP` views only, with a 60 s statement timeout. Seam 2 asserts it cannot read RAW/CLEAN/OPS or create objects.
- AWS: the Snowflake read role (`dev-nyc311-snowflake-read`) is applied. The org RCP needed an sts-only exception for Snowflake's IAM user; see `docs/aws-org-policies/README.md`.
- Demo, read through the dashboard role: page total = manifest rows = distinct `unique_key` = **71,830**.
- Seam 2 has three live tests and is skipped in CI without Snowflake credentials. Adding CI credentials is future work.

Deviations and deferrals:
- The dashboard shares `NYC311_SVC` (one key pair) with the pipeline, via role switching with no secondary roles. Ticket 14 should give the dashboard its own Snowflake user.
- Python lives in `warehouse/` and `dashboard/`, and SQL in `snowflake/`. A top-level Python package named `snowflake` would shadow the connector.
- The migration runner has no lock. Snowflake DDL auto-commits, so a migration that fails halfway must be fixed forward by hand.
