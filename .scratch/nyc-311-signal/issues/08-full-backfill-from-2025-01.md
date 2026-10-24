# 08: Full backfill from 2025-01

**What to build:** Load every citywide Service Request created since 2025-01-01 (about 7M) so analytics and ML have enough history. Interruptions are survivable.

**Blocked by:** 05 (Request Versions, stale-overwrite guard and quarantine)

**Status:** done

**Mirror:** GitHub #9

- [x] One-off ECS Fargate task runs the same collector in partition mode, one partition per creation month
- [x] Resumes only incomplete months after interruption
- [x] Loaded with bulk COPY under load tracking so no file loads twice through COPY and Snowpipe
- [x] Reconciliation report: source rows, raw rows and distinct Current Requests per month
- [x] Demo: kill the task mid-run, resume it, and counts reconcile

## Comments

**2026-10-24 (agent): built and demoed.**
- `ingestion/backfill.py` runs the collector over `creation_month_partitions` from 2025-01 through `--through`, as run `backfill-YYYYMM-YYYYMM` into `data/bulk/`. A rerun with the same `--through` resumes; if a killed task left its lease, it waits up to 20 minutes for it to expire. `--status` prints each month's checkpoint.
- `modules/backfill` (Terraform): ECR `dev-nyc311-collector`, ECS cluster `dev-nyc311`, Fargate task `dev-nyc311-backfill` (ARM64, 0.5 vCPU, 1 GB) under the ingestion role, in the default VPC with a public IP, outbound-only security group, no NAT. Image: `ingestion/Dockerfile`. Ops commands are in `infrastructure/terraform/README.md`.
- `warehouse/backfill.py load` waits for the run's manifest, then bulk-COPYs and processes each month from its own prefix in its own transaction (the warehouse's 600 s statement limit), then publishes once. `report` prints source, manifest, RAW and Current Request counts per month and exits 1 on any difference. Snowpipe never sees `data/bulk/`; COPY load history plus RAW's `source_file` guard make a rerun load nothing.
- **Demo.** Task 1 started about 17:20 UTC and was stopped at 17:26 UTC, 12 files into March 2025. Python as PID 1 ignores SIGTERM, so ECS killed it with SIGKILL after 30 s (exit 137). Its lease stayed held until 17:42:58 UTC. Task 2, started at once, logged `waiting for the lease`, then wrote March's `part-00013` at 17:43:52 UTC. It did not refetch January or February, and no file was written twice. It finished at 18:58 UTC: 701 files, 6,889,517 rows, exit 0.
- **Load:** 11 minutes for 22 months, 0 quarantined. It published `20261024T191225-52d8ed6a`, not stale.
- **Report:** every month from 2025-01 to 2026-10 has source = manifest = RAW = Current Requests (e.g. 2025-01: 348,180). Current Requests total 6,889,517, created 2025-01-01 to 2026-10-23. RAW holds 7,510,623 Observations, the backfill plus the daily passes.
- Left as is: a stop takes 30 s to become a kill (no init process in the image). Harmless, since resume handles either.

**2026-10-24 (agent): review fixes.**
Two-axis review of 8d5fe37. Fixes:
- **Readiness gate (story 39).** `warehouse.backfill load` now checks each month against its manifest before processing it: every file and every row must be in RAW, or it raises `MonthMismatch` and processes nothing of that month. `pipeline.process(only_run=...)` refuses, and changes nothing, if the stream holds Observations of another run. A daily pass Snowpipe loads mid-load stays for `OPS.PROCESS_READY_RUNS`. The check runs inside the processing transaction, where the stream is one snapshot. There are Seam 2 tests for both.
- **Reproducible image.** The Dockerfile installs from `uv.lock` (`uv export`, hash-checked), not a hand pin. ECR tags are now immutable, and the task definition runs `image_tag`, the commit SHA the image was built from. A resumed run can't pick up different code.
- **Report.** RAW rows are counted per data file's partition, not by parsing the payload. The docstring says it is a snapshot for right after the load: later daily passes and source edits show as DIFF.
- **Smells.** `CreationWindow.month`, `RunLayout.partition_prefix`, and one `parse_month` and `month_windows` shared by both CLIs. `LEASE_WAIT` derives from the collector's `LEASE_TTL`, and the README now matches it. `RunLayout.checkpoint_key` was dropped (it only forwarded).
- **Left as is:**
  - The checkpoint checksum is stored but not re-verified. That would mean re-downloading 1.2 GB, and file SHA-256s are already in the manifest.
  - The task runs under the existing ingestion role, which `modules/core` already trusts for ECS tasks, rather than its own role. Same permissions, one role.
