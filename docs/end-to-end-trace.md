# End-to-end trace of one Request

Service Request `70856306` (DOB, Building/Use, Bronx), traced through dev on 2026-10-25. It shows an Admission Score kept unchanged after a later Observation Closes the Request, as instruction.md §19 asks.

| Step | Where | What it holds |
|---|---|---|
| 1. Observation in S3 | `s3://dev-nyc311-observations-…/data/daily/extract_date=2026-10-24/run_id=cursor-20261024T060016Z/updated-after-20261023T141600.000Z/part-00054.ndjson.gz` | Extracted 2026-10-24 06:13 UTC by the daily cursor pass. Status `Open`, source `:updated_at` 01:47 UTC, payload hash `2cc45ad0…`. The backfill saw the same payload at 18:58 UTC (`data/bulk/…/part-00023`). |
| 2. RAW | `RAW.RAW_311_OBSERVATIONS` | Both Observations, loaded by Snowpipe (daily) and bulk COPY (backfill), each with its file and run. |
| 3. Request Version | `CLEAN.REQUEST_VERSIONS` | One version `2cc45ad0…` (Open), valid from the 06:13 extraction, last seen at 18:58: the two identical Observations collapse into one. |
| 4. Feature snapshot | `ML.FEATURE_SNAPSHOTS` | Frozen by Publication `20261025T040009-b919258b`. First Observed 2026-10-24 06:13 UTC, about 35 hours after creation (2026-10-22 14:58 New York, 18:58 UTC), so eligible. Prediction cutoff 2026-10-24 18:58. Source payload `2cc45ad0…`. Features: Building/Use, DOB, Bronx, channel UNKNOWN, hour 14, Thursday, October. |
| 5. Admission Score | `ML.REQUEST_PREDICTIONS` | 0.832 from `model-20261025T035838` (`features-v1`), scored 2026-10-25 04:01 UTC in that same Publication. |
| 6. Dashboard | Risk Queue, then Request lookup | Listed in the Risk Queue while it was open. The lookup shows the score with its model and feature versions, cutoff, scoring time and Publication. |
| 7. A later Closure | `cursor-20261025T060016Z`, `…/extract_date=2026-10-25/…/part-00054.ndjson.gz` | Extracted 2026-10-25 06:18 UTC with status `Closed`, a Date-Only Closure on 2026-10-23, and source `:updated_at` 2026-10-25 01:33. It becomes a second Request Version (`f0201879…`), and the Current Request is now Closed. |
| 8. Score kept | `ML.REQUEST_PREDICTIONS`, `ML.FEATURE_SNAPSHOTS` | Unchanged: the same score, model, cutoff and source payload `2cc45ad0…`. The Closure is not used to re-score. |
| 9. Label | `ML.TRAINING_LABELS` | `not_matured` until 240 hours after creation have been observed. It will then be 0: the Closure fell before the horizon day. That gives a prospective check of this score. |

What the trace shows beyond the happy path:

- **The source's first state was inconsistent.** It said `Open` while carrying a `closed_date` (2026-10-22 14:58:39, equal to the creation time). Had the outcome matured in that state, the Label rules would have excluded it as `closure_time_while_open`.
- **The score is a confident miss.** DOB Building/Use Requests were mostly Delayed in training, but this one was Closed the next day. The Risk Queue says that predicted delay is not urgency for exactly this kind of case.

To reproduce the trace for any Request, run these queries as the pipeline role: `RAW.RAW_311_OBSERVATIONS WHERE observation:payload:unique_key = '<id>'`, then `CLEAN.REQUEST_VERSIONS`, `CLEAN.SERVICE_REQUESTS`, `ML.FEATURE_SNAPSHOTS`, `ML.REQUEST_PREDICTIONS` and `ML.TRAINING_LABELS` by `unique_key`.
