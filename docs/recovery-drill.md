# Recovery drill

The drill instruction.md §14 asks for, run on 2026-10-25 with `uv run --env-file .env python -m warehouse.drill` (`warehouse/drill.py`). It uses the real NYC Open Data source and the test database `NYC311_DEV_TEST`, which it rebuilds from the migrations.

## What it does

1. **Interrupt an extraction.** It collects three NYC-local creation days (2026-10-01 to 10-03) straight through, at 2,000 rows a page. Then it collects them again under another run ID, killing the collector after its third request, the way a process dies: not a retryable source error. Then it reruns that run ID, which resumes.
2. **Compare the Observations** of the two runs: every Request and payload hash, counted per run.
3. **Rebuild the warehouse** from each run in turn: fresh schemas, the run's data files and manifest, processing, and a Publication. Then compare the Current Requests and the published Overview.
4. **Replay an older Observation.** For one Closed Request, stage a copy of its Observation that is Open, extracted three days earlier and with a source `:updated_at` three days earlier, then process it.

## Result

```
1. Collecting 3 creation days from 2026-10-01, 2000 rows a page
   uninterrupted: 34,506 rows in 19 files
   interrupted: killed after 3 requests; 3 files written, no manifest: True
   resumed: 34,506 rows in 19 files, 121 s in all
2. Same Observations: True (34,506 distinct; 0 repeated across the interruption; 0 only uninterrupted, 0 only resumed)
3. Rebuilt: uninterrupted published, resumed published; Current Requests (rows, distinct, Closed) (34506, 34506, 30760) vs (34506, 34506, 30760); same states: True; same Overview: True (3 days)
4. Replayed an older 'Open' Observation of 70591038 (Closed): current state kept: True; history now ['Open', 'Closed']
```

- **Interruption.** The killed run left three complete files, each checkpointed, and no manifest. Without a manifest the readiness check holds a run's files, so nothing partial flows downstream. The drill doesn't exercise that check: Seam 2's `test_files_without_their_manifest_wait_and_nothing_flows_downstream` does.
- **Resume.** The rerun continued the first partition from its stored page cursor. It wrote exactly the same 34,506 Observations: none lost, none repeated.
  - The kill landed between pages, after a checkpoint.
  - The narrower window, a file stored but the process killed before its checkpoint, is covered by Seam 1's `test_file_stored_before_a_crash_is_kept_not_overwritten_and_not_duplicated`.
- **Rebuild.** Built separately, the two runs give identical Current Requests (same `unique_key` × payload hash aggregate) and an identical Overview.
- **Replay.** The older Observation became an earlier Request Version in the Request's history. The Current Request stayed Closed: an older state never overwrites a newer one, whatever order it arrives in.
  - The replayed Observation is synthetic: the stored payload, edited to Open and dated three days earlier.
  - It was loaded without a manifest, through the bulk path. In the daily path, a file without its manifest would wait at the readiness check.

The script exits 1 if any of these checks fails.

## What it does not cover

- **The daily cursor pass.** Its interruption and resume are covered offline, against a fake source (`ingestion/tests/test_cursor.py`), and its continuation by Lambda in `test_reconcile.py`.
- **A loss of RAW.** Rebuilding from S3 after RAW's 90-day retention follows `docs/runbook.md` (*Stale stream*). The drill rebuilds from the run files, which is the same path.
