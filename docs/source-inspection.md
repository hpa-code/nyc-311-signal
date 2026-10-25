# Source inspection: NYC 311 (erm2-nwe9)

Inspected 2026-10-22 via the Socrata metadata and SODA endpoints. Figures are point-in-time evidence, not guarantees; re-check before relying on them.

## Schema

48 columns. Fields relevant to this project:

| API field | Type | Notes |
|---|---|---|
| `unique_key` | text | No duplicates within Sep 2026 |
| `created_date` | floating timestamp | No timezone: NYC local time. Only 8 of 323,630 (Sep 2026) at exactly midnight |
| `closed_date` | floating timestamp | 92% populated (Sep 2026). Often date-only at midnight for some agencies (see below) |
| `complaint_type` | text | Display name "Problem (formerly Complaint Type)". 175 distinct in Sep 2026 |
| `descriptor` | text | Display name "Problem Detail (formerly Descriptor)". 770 distinct in Sep 2026 |
| `descriptor_2` | text | "Additional Details"; not yet assessed |
| `agency` | text | |
| `borough` | text | Always populated; 397 "Unspecified" in Sep 2026 |
| `location_type` | text | 93% populated |
| `incident_zip` | text | 99% populated |
| `latitude`/`longitude` | number | 98% populated |
| `status` | text | Closed, In Progress, Open, Assigned, Pending, Unspecified |
| `open_data_channel_type` | text | ONLINE / PHONE / MOBILE / UNKNOWN. Known at submission: feature candidate |
| `due_date` | timestamp | 0.3% populated: unusable |
| `resolution_action_updated_date` | timestamp | 97% populated |
| `resolution_description` | text | Restricted; label diagnostics only |

System fields `:created_at` and `:updated_at` are available (see below).

## Publication timing

- The source publishes one batch per day at about **01:33 UTC** (21:33 New York time, during daylight saving).
- `:created_at` is the time a row first appeared in the dataset, and it persists across later updates. Exception: rows created before about late 2025 show `2025-12-26`, from a bulk reload.
- A batch includes Requests created up to roughly 03:00 New York time on the same calendar day. Example: the 2026-10-22 01:33 UTC batch contained Requests created up to 2026-10-21 02:49 local.
- Creation-to-publication lag is therefore roughly **19–43 hours**. With collection at 06:00 UTC, creation-to-First-Observed is about **23–47 hours** when no runs fail.

## Publication lag, re-measured from `:created_at` (2026-10-24)

The lag is measured as the time from `created_date` (New York time) to `:created_at`, for 3.1M Requests created from 2026-01-15 to 2026-10-23. Earlier rows carry the 2025-12-26 reload date.

| Created | p95 | p99 | Over 48 h | Over 72 h |
|---|---|---|---|---|
| Jan–May 2026 | 42 h | 45–78 h | 0.7–2.2% | 0.3–1.3% |
| Jun–Oct 2026 | 45–54 h | 67–73 h | 4.3–8.1% | 0.7–1.1% |

The reporting allowance for Labels is therefore 72 h, not the starting 48 h.

The lag for Closures could not be measured directly: `:updated_at` moves with every later edit, so it gives only an upper bound. Creation lag stands in for it.

## Change tracking

- `:updated_at` changes when a row changes. Example: a Request created 2026-07-31 and closed 2026-10-19 kept `:created_at` 2026-08-02 but has `:updated_at` 2026-10-22 01:33 UTC.
- About 564,000 rows had `:updated_at` ≥ 2026-10-21 (two daily batches, roughly 280,000 per day). Not every update necessarily changes a modeled field.

## Data-quality exceptions (Sep 2026, 323,630 Requests)

| Case | Count |
|---|---|
| `closed_date` before `created_date` | 213 |
| Status not Closed but `closed_date` present | 4,072 |
| Status Closed with no `closed_date` | 0 |
| Borough "Unspecified" | 397 |

## Closure-time precision (Aug 2026, closed Requests)

`closed_date` at exactly 00:00:00 suggests date-only precision:

| Agency | Closed | Midnight closures |
|---|---|---|
| DOB | 12,239 | 10,209 (83%) |
| DSNY | 29,599 | 917 (3%) |
| DOT | 21,607 | 540 (2.5%) |
| DEP | 21,459 | 87 |
| NYPD | 149,900 | 11 |
| HPD, DPR, DOHMH | — | 0 |

## Volume

- September 2026: 323,630 Requests (about 10,800 per day).
- Agency share: NYPD 47%, HPD 17%, DSNY 9%, DOT 7%, DEP 6%, DPR 6%, DOB 3%.
