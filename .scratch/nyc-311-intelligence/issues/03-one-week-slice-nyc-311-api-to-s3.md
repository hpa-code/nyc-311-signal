# 03: One-week slice: NYC 311 API to S3

**What to build:** Run the collector for one week of citywide Service Requests and get immutable Observation files plus a manifest in S3 that reconcile with the source. Establishes Seam 1 (fake Socrata HTTP source in, object store out).

**Blocked by:** 02 (Scaffold repo and dev infrastructure)

**Status:** ready-for-agent

**Mirror:** GitHub #4

- [x] Source client fetches with stable ordering and keyset pagination; retries transient errors with backoff and jitter; fails clearly on auth errors and schema breaks
- [x] Each Observation is wrapped with run ID, extraction time, source dataset, schema version, payload hash, `:created_at` and `:updated_at`
- [x] gzip NDJSON files with deterministic identities; a retry never overwrites an existing file
- [x] Per-partition checkpoints; a resumed run only redoes incomplete partitions
- [x] Manifest lists every file and row count; extraction is marked complete only after all files are stored
- [x] Seam 1 tests: page-boundary ties neither skip nor duplicate rows, a retry keeps identities, resume after a crash, missing optional fields do not crash
- [ ] Demo: manifest row total equals the API count for the chosen week

## Comments

**2026-10-22 (agent, from 02):** The dev ingestion role allows `s3:PutObject` under `data/` and `manifests/` only when the request carries `If-None-Match: *`. The collector must write Observation files and manifests with a conditional put, and treat 412 Precondition Failed as "already written". Checkpoints are writable without conditions.

**2026-10-22 (agent):** Collector, source client and object stores are done (`ingestion/`); Seam 1 tests pass. Demo run against the live API into a local-directory store:

```
python -m ingestion.fetch_311 --start 2026-09-01 --days 7 --store local:<dir>
run slice-2026-09-01-7d: 12 files, 71830 rows   (every day: manifest count == API count(*))
```

That gives 71,830 distinct `unique_key`s. The demo box stays open until the same command runs with `--store s3:<dev bucket>`, which needs ticket 01 and a Terraform apply for 02. The S3 adapter is tested against moto only.

Decisions and notes:
- `payload` is the source row without Socrata system fields, and `payload_hash` is taken over it, so an unchanged Request hashes the same when `:updated_at` moves. `:created_at` and `:updated_at` are kept verbatim in the wrapper. `:id` and `:version` are not kept.
- Only an empty page completes a partition. That costs one extra request per partition, but completion no longer depends on page size.
- A row missing `unique_key`, `created_date` or the system fields, or with a malformed `created_date`, fails the run with `SchemaError`, because keyset pagination cannot proceed without them. Field-level quarantine stays in the warehouse (ticket 05).
- Rate limiting is reactive only (429 plus Retry-After, with jittered backoff). Revisit if the full backfill (08) gets throttled.
- instruction.md §17 lists `checkpoints.py`. Checkpointing lives in `collector.py` instead, beside the Collector module named in the spec.
