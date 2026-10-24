# 07: Publication gate

**What to build:** The dashboard only ever shows one complete, validated Publication. When a run fails, it keeps the last valid Publication and shows a stale-data warning.

**Blocked by:** 06 (Daily cursor collection and auto-ingest)

**Status:** done

**Mirror:** GitHub #8

- [x] `OPS.PUBLICATIONS` records each release; outputs are versioned by publication ID
- [x] Task graph: readiness, then validate and dedupe, then merge, then downstream steps, then the publication check, then publish or hold
- [x] The current-Publication pointer moves only after the checks pass
- [x] Dashboard shows the Publication timestamp and a stale-data banner when the latest run failed; missing scores are never filled in (scores: carried to ticket 12, nothing is scored yet)
- [x] Seam 2 test: a partial run never becomes the current Publication
- [x] Demo: force a failure and the dashboard shows the previous Publication with a warning

## Comments

**2026-10-24 (agent): built, deployed to dev, demo done.**
- V011: `OPS.PUBLICATIONS` (published | held | failed, with the checks and input hash), pointer `OPS.CURRENT_PUBLICATION`, and `ANALYTICS.PUB_*` copies of every dashboard output keyed by `publication_id`. `APP.OVERVIEW_DAILY`, `DATA_COVERAGE`, `QUARANTINE_SUMMARY` and `TABLEAU_DAILY` now show only the current Publication. Outputs of the current Publication and the 7 latest attempts are kept.
- `R__publication.sql`: `OPS.PUBLISH()` waits (publishes nothing) while the stream holds unprocessed Observations or a run is still waiting. It skips when nothing changed since the latest attempt, and otherwise builds every output and runs 6 checks in one transaction. The pointer moves only if all pass. A failed check commits the attempt as held and raises `-20002`. An error mid-build rolls back and records the attempt as failed.
- Task graph (`R__readiness.sql`): `OPS.PUBLISH_TASK` after `OPS.READINESS_TASK`, and `OPS.PUBLISH_AFTER_SWEEP_TASK` after `OPS.READINESS_SWEEP_TASK`. Validate, dedupe and merge stay inside the readiness procedure's single transaction (ticket 06). Later downstream steps (analytics, scoring) slot in between as child tasks.
- `APP.PUBLICATION_STATUS` (one row): the current Publication, its published time and data-as-of time, the latest attempt state, and a stale flag with reasons. The reasons are no Publication yet, the latest attempt held or failed, a run stuck for 6 hours, or data more than 36 hours old. The dashboard shows a warning banner and the Publication ID, and caches aggregates per `publication_id`.
- `warehouse.pipeline.run()` now publishes too (backfill, tests). `--ready` runs the readiness check, then publishes.
- "Missing scores are never filled in": nothing to score yet. Ticket 12 must add scores as a `PUB_*` output and add a check.
- Seam 2: 21 pass (4 new in `test_publication.py`). The readiness tests now publish after each check, like the task graph.
- Demo (dev, 2026-10-24 04:50 UTC): Publication `20261024T044938-0d1faa43` published. Then a duplicated Current Request made the next attempt `held` (`one_current_request_per_unique_key: false`), and the dashboard kept showing 71,830 Requests with the stale-data warning. After removing the duplicate, `20261024T045056-66e6fe5e` published and the warning cleared.

**2026-10-24 (agent): review fixes, migration V012.**
Two-axis review of 2357150..ad9b9e8. These are the fixes:
- **Race with a concurrent merge.** The new `OPS.PIPELINE_LOCK` row is updated first thing in every processing transaction (the readiness procedure and `pipeline._process`) and every publishing transaction (`transformations/take_pipeline_lock.sql`). A Publication is therefore never built while Current Requests are merging. The waiting-runs check and the input hash now run under the lock. The end-of-build stream check still catches Snowpipe loads that land mid-build.
- **Held forever on unchanged inputs.** Publishing now skips only when the latest attempt was *published* from the same inputs. After a held or failed attempt it rebuilds, so a data fix or a procedure fix is picked up without waiting for new data. A still-broken state then adds one held row per graph run, which with no new data means only the daily sweep.
- **Mixed Publications in readers.** New `APP.PUBLISHED_OVERVIEW_DAILY`, `PUBLISHED_DATA_COVERAGE` and `PUBLISHED_TABLEAU_DAILY` serve any kept, published Publication by `publication_id`, and never a held one. The dashboard and the Tableau export read one ID from `APP.PUBLICATION_STATUS`, then read by that ID, so their results can't come from two Publications. The export's coverage file now carries `publication_id`.
- **Banner when readiness fails.** `OPS.STEP_STATUS` records the readiness procedure's last success and last failure (a stuck run included). `APP.PUBLICATION_STATUS` adds "the latest readiness check failed" until a later check succeeds.
- ADR 0002 reworded: the Tableau export is behind the gate too. The status is now `done`.
- **Left as judgement calls:**
  - The SQL copied from the old APP views into `R__publication.sql` (applied migrations can't be edited).
  - The list of four `PUB_*` tables repeated in the delete and truncate lines (ticket 12 adds scores there).
  - Bare state strings.
  - Tests importing helpers from other test modules (`rows_of` already did that before 07).
  - Small cells in the Tableau CSV: the ticket 09 threshold is about reliable comparisons, not privacy; see ADR 0002.
  - Alerting on `-20002` and `-20001` task failures stays with ticket 13.
- Seam 2: see the commit. There is a new test for a failed readiness check, and the held test now checks reads by ID.
