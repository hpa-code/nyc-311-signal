# Spec: NYC 311 Intelligence end-to-end pipeline and dashboard

Status: ready-for-agent
Mirror: GitHub #1

## Problem Statement

NYC publishes every 311 Service Request as open data, but the raw feed answers none of the questions an analyst actually has: which new Requests are likely to sit unclosed past seven days, which Problems and boroughs close slowly, how the open backlog moves over time, and whether any prediction about this can be trusted. The data changes after creation (Closures arrive days or months later), publishes once a day with a lag, has date-only Closure times for some agencies, and is dominated by NYPD Requests that almost never become Delayed. A naive notebook would leak future information into its model, overstate its accuracy, and break the first time the API fails.

The project owner also needs this to work as a portfolio piece: a reviewer must be able to open a live URL and see a trustworthy, automated, honestly evaluated system, not a one-off analysis.

## Solution

An automated daily pipeline and public dashboard, "NYC 311 Intelligence":

- A collector fetches every changed Service Request each day using the source's `:updated_at` change cursor, stores immutable Observations in S3, and survives failures without gaps or duplicates.
- Snowflake turns Observations into Request Versions and one Current Request per `unique_key`, builds analytics, assigns Labels only when trustworthy, and scores new Modeling Cohort Requests once with an approved model (the Admission Score).
- Every day's output becomes visible only as a complete, validated Publication. If anything fails, the dashboard keeps showing the last valid Publication with a stale-data notice.
- A public Streamlit dashboard on ECS Fargate shows trends, backlog, a Risk Queue (predicted Delayed), a separate Overdue Queue (observed), model performance against baselines, and pipeline freshness, while exposing no addresses or pinpoint locations.

Vocabulary follows `GLOSSARY.md`. Architecture and policy detail live in `instruction.md`; source facts live in `docs/source-inspection.md`; ADR 0001 records the NYPD exclusion.

## User Stories

### Portfolio reviewer

1. As a portfolio reviewer, I want a public HTTPS URL that loads without a login, so that I can evaluate the project in seconds.
2. As a portfolio reviewer, I want an Overview page with new Requests, open backlog, closure trends, and data coverage, so that I understand the scale and scope immediately.
3. As a portfolio reviewer, I want to see when the latest Publication was produced, so that I can tell the pipeline actually runs daily.
4. As a portfolio reviewer, I want a Model Performance page comparing the model against constant, Problem, and agency-only baselines, so that I can judge whether the model adds anything.
5. As a portfolio reviewer, I want metrics computed on a later held-out period with sample sizes and date ranges, so that I can trust they are not inflated.
6. As a portfolio reviewer, I want stated limitations (NYPD excluded, Date-Only Closures, approximate retrospective evaluation), so that I see honest reporting rather than marketing.
7. As a portfolio reviewer, I want a Pipeline Health page showing the last successful run and Publication status, so that I can see operational maturity.
8. As a portfolio reviewer, I want a README with architecture, screenshots, setup, metrics, and limitations, so that I can understand the system without running it.
9. As a portfolio reviewer, I want to see that infrastructure is reproducible from Git, so that I know the system is not hand-built.

### Analyst (dashboard user)

10. As an analyst, I want a Risk Queue of recent Modeling Cohort Requests ordered by Admission Score, so that I can review the Requests most likely to become Delayed.
11. As an analyst, I want each Risk Queue row to show Request ID, Problem, agency, borough, created date, and Admission Score, so that I have enough context without exposing private details.
12. As an analyst, I want a clear note beside the Risk Queue that predicted delay is not urgency or deserved priority, so that I do not misuse it.
13. As an analyst, I want a separate Overdue Queue of Requests observed open past their Seven-Day Horizon, so that I never confuse a prediction with an observed fact.
14. As an analyst, I want the Overdue Queue to include NYPD Requests, so that observed backlog is complete even though NYPD is outside the Modeling Cohort.
15. As an analyst, I want borough and Problem comparisons that compare within the same Problem first, so that differences are not just Problem mix.
16. As an analyst, I want every duration chart to show closure rates and unresolved counts alongside median and p90 durations, so that slow open Requests do not make durations look artificially short.
17. As an analyst, I want backlog history labeled as observed (from saved daily snapshots) or retrospective (reconstructed), so that I know which one I am reading.
18. As an analyst, I want a geographic view aggregated to boroughs or cells with at least 20 Requests, so that I can see patterns without pinpoint locations.
19. As an analyst, I want a Request lookup showing selected fields, observation freshness, and Admission Score provenance (model version, feature version, scoring time), so that I can audit any single prediction.
20. As an analyst, I want a visible stale-data message when the latest run failed, so that I never mistake old data for fresh data.
21. As an analyst, I want missing scores shown as missing rather than filled with a made-up probability, so that the queue never lies.
22. As an analyst, I want local New York times on the dashboard, so that times match how I think about the city.
23. As an analyst, I want unknown or new Problems kept and shown rather than silently dropped, so that coverage is complete.
24. As an analyst, I want dashboard pages to load quickly from cached aggregates, so that exploring does not cost warehouse time on every click.

### Project owner / operator

25. As the operator, I want collection to run daily at 06:00 UTC on a schedule, so that it lands after the source's ~01:33 UTC publication without me doing anything.
26. As the operator, I want the daily collector to fetch rows whose `:updated_at` is past the stored cursor minus a 6-hour overlap, so that every new Request and every change to an old Request is captured.
27. As the operator, I want the cursor advanced only after that run's files are durably stored, so that a crash never skips data.
28. As the operator, I want a weekly reconciliation pass over older creation-date partitions that alerts when it finds changes the cursor missed, so that I learn if the cursor assumption breaks.
29. As the operator, I want a resumable historical backfill from 2025-01-01, partitioned by creation month and run as a one-off ECS Fargate task, so that loading ~7M rows survives interruptions.
30. As the operator, I want each partition's query bounds, page cursor, row count, checksum, and completion state checkpointed, so that only incomplete partitions rerun after failure.
31. As the operator, I want transient HTTP errors, throttling, and server errors retried with exponential backoff and jitter, so that brief source outages self-heal.
32. As the operator, I want authentication failures and incompatible schema changes to fail loudly, so that bad data is never silently ingested.
33. As the operator, I want a lease that prevents two collectors from running at once, so that overlapping schedules never corrupt checkpoints.
34. As the operator, I want each Observation wrapped with run ID, extraction time, source dataset, schema version, payload hash, `:created_at`, and `:updated_at`, so that every row is traceable and replayable.
35. As the operator, I want Observation files to be immutable with deterministic identities, so that retries never overwrite a file Snowpipe may already have loaded.
36. As the operator, I want a run manifest listing every expected file and row count, so that the warehouse can prove a run loaded completely.
37. As the operator, I want Snowpipe to auto-load only the data prefix (not manifests or checkpoints), so that control files never pollute RAW.
38. As the operator, I want the backfill loaded via bulk COPY with coordinated tracking, so that historical files are not loaded twice through two paths.
39. As the operator, I want a readiness task that starts downstream work only when a run's manifest reconciles with loaded files and counts, so that partial runs never flow downstream.
40. As the operator, I want repeated identical Observations to collapse into one Request Version, so that the 6-hour overlap and replays never inflate counts.
41. As the operator, I want an older Observation arriving late to never overwrite a newer Current Request, so that replays and out-of-order loads are safe.
42. As the operator, I want malformed Observations (missing identifier, invalid required dates, incompatible types) quarantined with a reason, so that I can diagnose and replay them.
43. As the operator, I want Publications versioned by publication ID with a current-Publication pointer moved only after all outputs pass validation, so that charts and scores always come from the same complete run.
44. As the operator, I want an alert when no complete Publication exists for 36 hours, so that I notice a silently stuck pipeline.
45. As the operator, I want monitoring of extracted rows, loaded rows, distinct Requests, updated Requests, quarantined rows, runtime, scoring coverage, and unknown-Problem rate, so that drift and breakage are visible.
46. As the operator, I want source freshness tracked separately from job success, so that a successful job that fetched stale data still raises a warning.
47. As the operator, I want a recovery drill (interrupt extraction, resume, rebuild, compare against an uninterrupted run, replay an old Observation), so that I can demonstrate recovery.
48. As the operator, I want S3 to keep every Observation file (moved to cheaper storage after 90 days) and Snowflake RAW to keep 90 days, so that replay stays possible while storage stays cheap.
49. As the operator, I want an AWS budget alert when $150 of the $200 Free-plan credits are consumed, so that I can upgrade or shut down before the account closes.
50. As the operator, I want small Snowflake warehouses with 60-second auto-suspend and query timeouts, so that post-trial cost stays around $10–20 per month.
51. As the operator, I want serverless (Snowpipe) consumption monitored separately from warehouse consumption, so that loading cost does not hide.
52. As the operator, I want documented shutdown instructions, so that I can stop optional infrastructure when not needed.
53. As the operator, I want dev (`NYC311_DEV`, `dev-` AWS prefixes) and prod (`NYC311`, `prod-`) in one account each, so that I can test safely without paying for separate accounts.
54. As the operator, I want all AWS resources in us-east-2 alongside the Snowflake account, so that loading avoids cross-region transfer.
55. As the operator, I want the Socrata app token, Snowflake key-pair credentials, and other secrets in Secrets Manager, so that no secret lives in code, images, logs, or Terraform state.

### ML (owner as modeler)

56. As the modeler, I want a Label assigned only from a trusted Observation taken after the Seven-Day Horizon plus a reporting allowance, so that training never uses guessed outcomes.
57. As the modeler, I want a Closure at or before 168 hours labeled 0 and after it labeled 1, and an open Request confirmed open after 168 hours labeled 1, so that the target matches its definition.
58. As the modeler, I want Date-Only Closures compared by New York calendar day, with ones on the horizon day given no Label, so that imprecise times never create wrong Labels.
59. As the modeler, I want Reopened Requests, Closures before creation, and status/closure conflicts excluded with counted reasons, so that exceptions are explicit.
60. As the modeler, I want duplicate Closures kept as Closed (label 0) and their share reported in the model card, so that the administrative target stays honest.
61. As the modeler, I want NYPD Requests excluded from the Modeling Cohort, so that trivially easy negatives do not inflate PR-AUC and lift (ADR 0001).
62. As the modeler, I want features limited to fields known at submission (Problem, agency, borough, channel, creation hour, weekday, month; Problem Detail and location type after stability checks), so that there is no leakage.
63. As the modeler, I want Closure date, final status, resolution text, and time-to-closure structurally blocked from the feature set, so that leakage cannot happen by accident.
64. As the modeler, I want frozen feature snapshots captured at First Observation, so that prospective evaluation uses exactly what was known then.
65. As the modeler, I want chronological train/validation/test splits by creation time with a gap of at least the horizon plus allowance, and all Observations of a Request in one split, so that evaluation reflects the future.
66. As the modeler, I want encoders, imputers, and Problem mappings fit on training data only, with unseen Problems handled explicitly, so that preprocessing does not leak.
67. As the modeler, I want constant-prevalence, historical-Problem, and agency-only baselines, so that any model must beat obvious rules to count.
68. As the modeler, I want logistic regression first, with Random Forest or XGBoost only if they materially improve useful metrics, so that complexity is earned.
69. As the modeler, I want thresholds and calibration chosen on validation data only and the test period used once, so that reported metrics are unbiased.
70. As the modeler, I want prevalence, precision/recall, PR-AUC, precision/recall/lift at top 10% (with defined tie handling), Brier score, and per-agency/Problem/borough metrics with sample sizes, so that performance is fully described.
71. As the modeler, I want models registered with preprocessing, feature schema, dependencies, version, and metrics, and promoted manually with the previous version kept for rollback, so that scoring is reproducible and reversible.
72. As the modeler, I want Admission Scores computed once per Request within 72 hours of creation, below the horizon, and never silently recomputed, so that the Risk Queue reflects admission-time knowledge.
73. As the modeler, I want each score stored with Request ID, model version, feature version, prediction cutoff, scoring time, score, and publication ID, with idempotent retries, so that scores are auditable and never duplicated.
74. As the modeler, I want prospective evaluation on frozen first-observed inputs once their Labels mature, so that I can report real-world performance after deployment.

### Developer / agent

75. As a developer, I want the collector testable against a fake Socrata HTTP source and a local object store, so that ingestion behavior is verified quickly without cloud cost.
76. As a developer, I want the warehouse pipeline testable by loading fixture Observations into `NYC311_DEV` and asserting `APP.*` views and Publication state, so that one seam verifies every SQL rule.
77. As a developer, I want pull-request CI to run Python tests, SQL checks against fixtures, and a Docker build, so that regressions are caught before merge.
78. As a developer, I want merges to build an image tagged with the commit SHA, push to ECR, deploy, and health-check, using short-lived CI credentials, so that deployment is repeatable and safe.
79. As a developer, I want SQL migrations reviewed and applied in a compatible order, so that schema changes do not break running tasks.
80. As a developer, I want Terraform with protected persistent data and reviewed plans, so that a destructive change cannot slip through.
81. As a developer, I want model promotion and large backfills kept separate from application deployment, so that a dashboard deploy never retrains or reloads data.

## Implementation Decisions

**Modules**

- **Source client**: wraps the Socrata SODA API. Interface: fetch a page for a query (cursor range or creation-date window) with stable ordering (`:updated_at, unique_key` for the cursor; `created_date, unique_key` for partitions) and keyset pagination; returns rows plus `:created_at`/`:updated_at`. Owns retry, backoff, rate limiting, and the app token. Fails hard on auth errors and schema incompatibility.
- **Collector**: one deep module used by the daily Lambda, the weekly reconciliation, and the backfill ECS task. Interface: run(plan) → manifest, where a plan is either a cursor pass or a set of creation-month partitions. Internally it handles checkpoints, the lease, deterministic file identities, Observation wrapping and payload hashing, gzip NDJSON writing, manifests, and cursor advancement. Depends on the source client and an object store interface (S3 in prod, local or moto in tests).
- **Warehouse pipeline (SQL + Snowflake Tasks)**: storage integration, stage, file format, auto-ingest pipe (data prefix only), a control-file path for manifests, a readiness task, then a task graph: validate/quarantine → dedupe to Request Versions → merge Current Requests (latest observed wins, ordered by source `:updated_at` then extraction time and a deterministic tie-breaker, never by arrival time) → analytics and features in parallel → scoring → publication check → publish or hold.
- **ML package (Snowpark/Python)**: labels, features, train, evaluate, register, predict. Labels and features are computed in Snowflake from Request Versions; training and evaluation are deliberate, manually run workflows; daily scoring is a task step that invokes the approved registered model.
- **Dashboard (Streamlit)**: reads only `APP.*` views through a read-only role and a bounded warehouse. Parameterized, paginated queries; caches aggregates for the lifetime of a Publication.
- **Infrastructure (Terraform)**: S3 bucket with data/manifests/checkpoints prefixes, encryption, public access blocked, lifecycle rule; IAM roles for ingestion, Snowflake integration, ECS task, and CI deployment (OIDC); Lambda; EventBridge Scheduler (explicit timezone); ECS cluster, Fargate service and one-off backfill task definition; ECR; ALB with TLS; Secrets Manager; CloudWatch alarms; budget alert. `dev`/`prod` environment folders in us-east-2.

**Storage layout**

- S3 object paths partitioned by extract date and run ID for data; separate manifests and checkpoints prefixes. Files are immutable.
- Snowflake schemas: RAW, CLEAN, ANALYTICS, ML, OPS, APP. Key tables (grain in parentheses): `RAW.RAW_311_OBSERVATIONS` (one Observation, 90-day retention), `CLEAN.REQUEST_VERSIONS` (one changed state), `CLEAN.SERVICE_REQUESTS` (one Current Request), `CLEAN.QUARANTINED_RECORDS`, `ANALYTICS.DAILY_REQUEST_STATS`, `ANALYTICS.DAILY_BACKLOG` (saved daily snapshots), `ML.FEATURE_SNAPSHOTS`, `ML.TRAINING_LABELS`, `ML.REQUEST_PREDICTIONS`, `ML.MODEL_EVALUATION`, `OPS.PIPELINE_RUNS`, `OPS.DATA_QUALITY_RESULTS`, `OPS.PUBLICATIONS`, `APP.*` views.

**Key rules (decided)**

- Scope: all agencies citywide, Requests created from 2025-01-01, unsampled. First milestone proves the full path on a one-week slice.
- Change capture: `:updated_at` cursor with a 6-hour overlap plus weekly reconciliation; no 30-day re-fetch, no known-open-ID export.
- Timestamps: extraction, ingestion, and scoring in UTC; source dates are NYC local floating timestamps, parsed as `America/New_York` with originals preserved. Seven-Day Horizon = 168 elapsed hours from validated creation time.
- Labels: as in user stories 56–60. Reporting allowance starts at two days and is re-measured from `:created_at`.
- Modeling Cohort excludes NYPD (ADR 0001); analytics and the Overdue Queue include it.
- Admission Score eligibility: Modeling Cohort, First Observed within 72 hours of creation, still below the horizon.
- Public data: Risk Queue fields limited to Request ID, Problem, agency, borough, created date, and Admission Score; geography aggregated with a minimum of 20 per cell; no addresses, ZIP codes in the queue, or raw coordinates anywhere public; no raw addresses in downloads.
- Accounts and cost: Snowflake trial (then a paid account at roughly $10–20 per month), AWS Free plan with $200 credits and a budget alert at $150 consumed, dashboard always on from week 4 (~$37 per month), with Streamlit Community Cloud as the fallback host.

## Testing Decisions

**What makes a good test**: it exercises external behavior through a seam and asserts on outputs a user or downstream system would observe (files and manifests written; rows in `APP.*` views; Publication state), never on internal function calls or intermediate implementation details. Tests use small fixtures free of unnecessary location detail.

**Seam 1: collector boundary.** Fake Socrata HTTP source in; files, manifests, and checkpoints in a local or moto object store out. Covers:

- Pagination neither skips nor duplicates at page boundaries, including ties on `:updated_at`.
- The cursor advances only after durable writes; the overlap re-fetches without loss.
- Retries preserve deterministic file identities; a resumed backfill reruns only incomplete partitions.
- Throttling and server errors back off and recover; auth failures and schema breaks fail clearly.
- The lease prevents overlapping runs.
- Missing optional fields do not crash collection.

**Seam 2: warehouse boundary.** Fixture Observation files loaded into `NYC311_DEV` RAW, then the full task graph runs, then assertions on `APP.*` views and `OPS.PUBLICATIONS`. Covers:

- Duplicate and out-of-order Observations leave exactly one correct Current Request per `unique_key`; an older replayed Observation never overwrites a newer state.
- Malformed rows are quarantined with reasons.
- Label boundaries: a Closure exactly at 168h → 0; later → 1; young Requests and stale outcomes → no Label; a Date-Only Closure on the horizon day → no Label; Reopened Requests excluded.
- NYPD is absent from Modeling Cohort outputs and the Risk Queue, and present in the Overdue Queue and analytics.
- Feature snapshots contain no post-cutoff fields or future Labels; training and evaluation Request IDs never overlap.
- Scoring retries are idempotent.
- A partial or failed run never becomes the current Publication; the previous Publication remains visible.
- The dashboard role cannot write or read restricted tables.

**Outside the seams**: ML metrics are measured and reported, not asserted. The dashboard gets a post-deploy health check plus the permission test above. The recovery drill is a manual, documented demonstration.

**Prior art**: none yet; the repository has no code. These two seams establish the pattern.

## Out of Scope

- Real-time streaming, mobile apps, an LLM chatbot, automatic dispatch of city workers, and an online prediction API.
- Re-scoring older open Requests (a different question, such as "will this stay open seven more days?").
- Official decision-making: the application supports analysis and human review only.
- Causal claims about service quality or model explanations presented as causes.
- Separate AWS or Snowflake accounts per environment.
- Step Functions, Kafka, Spark, or extra warehouses before measured need.
- Scheduled automatic retraining (manual promotion first; later work).

## Further Notes

- The Snowflake trial ends around late November 2026, close to the end of the four-week plan; Snowflake-dependent work is front-loaded.
- The AWS Free-plan account closes six months after signup or when credits run out; upgrade before then to keep the demo URL alive.
- Source facts (publication at ~01:33 UTC, 23–47h creation-to-First-Observed lag, `:updated_at` behavior, date-only Closure shares, exception counts) are in `docs/source-inspection.md`, and should be re-checked after deployment.
- Retrospective evaluation is approximate, because historical exports may contain corrected agency, Problem, or location values; prospective evaluation on frozen inputs is the trustworthy measure.
