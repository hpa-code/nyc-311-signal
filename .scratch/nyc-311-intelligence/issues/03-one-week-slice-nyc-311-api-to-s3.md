# 03: One-week slice: NYC 311 API to S3

**What to build:** Run the collector for one week of citywide Service Requests and get immutable Observation files plus a manifest in S3 that reconcile with the source. Establishes Seam 1 (fake Socrata HTTP source in, object store out).

**Blocked by:** 02 (Scaffold repo and dev infrastructure)

**Status:** ready-for-agent

**Mirror:** GitHub #4

- [ ] Source client fetches with stable ordering and keyset pagination; retries transient errors with backoff and jitter; fails clearly on auth errors and schema breaks
- [ ] Each Observation is wrapped with run ID, extraction time, source dataset, schema version, payload hash, `:created_at` and `:updated_at`
- [ ] gzip NDJSON files with deterministic identities; a retry never overwrites an existing file
- [ ] Per-partition checkpoints; a resumed run only redoes incomplete partitions
- [ ] Manifest lists every file and row count; extraction is marked complete only after all files are stored
- [ ] Seam 1 tests: page-boundary ties neither skip nor duplicate rows, a retry keeps identities, resume after a crash, missing optional fields do not crash
- [ ] Demo: manifest row total equals the API count for the chosen week

## Comments

**2026-10-22 (agent, from 02):** The dev ingestion role allows `s3:PutObject` under `data/` and `manifests/` only when the request carries `If-None-Match: *`. The collector must write Observation files and manifests with a conditional put, and treat 412 Precondition Failed as "already written". Checkpoints are writable without conditions.
