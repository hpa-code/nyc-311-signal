# 10: Labels and Modeling Cohort

**What to build:** Every eligible Service Request gets a trustworthy Label or an explicit, counted reason for having none, and the Modeling Cohort is defined (NYPD excluded per ADR 0001).

**Blocked by:** 08 (Full backfill from 2025-01)

**Status:** done

**Mirror:** GitHub #11

- [x] Closure at or before 168h gives 0; after 168h gives 1; confirmed open after 168h gives 1
- [x] Labels are assigned only from a trusted Observation after the horizon plus the reporting allowance (starting at 2 days)
- [x] Date-Only Closures are compared by New York calendar day; one on the horizon day gets no Label
- [x] Reopened Requests, Closures before creation and status/closure conflicts are excluded and counted by reason
- [x] Duplicate Closures are kept and labeled by timing (ADR 0003), and their share is reported
- [x] Modeling Cohort excludes NYPD
- [x] Seam 2 boundary tests cover each rule above

## Comments

**2026-10-24 (agent): built.**
- **Outputs.** `V015__labels_and_modeling_cohort.sql`:
  - `ML.MODELING_COHORT`: Current Requests whose agency is not NYPD.
  - `ML.TRAINING_LABELS`: one row per cohort Request, with `label` (0/1) or `no_label_reason`, plus `horizon_at_utc`, `matured_at_utc`, `observed_through`, `reporting_allowance_hours` and the `date_only_closure`/`duplicate_closure` flags. It holds no Closure times.
  - `ANALYTICS.PUB_LABEL_SUMMARY` (read through `APP.PUBLISHED_LABEL_SUMMARY`): every Current Request counted once per Publication, by agency, Label and reason. NYPD shows as `outside_modeling_cohort`.
- **Trusted Observation.** `observed_through` is the latest processed extraction (`MAX(extracted_at)` of Current Requests). The change cursor re-fetches every changed Request, so the Current Request is the source's state as of that extraction. A Label needs `observed_through >= created + 168 h + 48 h`. The allowance is set in the view's `policy` CTE.
- **Reasons, first match wins:** `not_matured`, `reopened` (some Request Version Closed and a later one not), `closure_before_creation`, `closed_without_closure_time`, `closure_time_while_open`, `date_only_closure_on_horizon_day`.
- **Duplicate Closure** means status Closed and `resolution_description` contains "duplicate" (HPD, DEP, DOT, TLC and DCWP wording). It is labeled like any other Closure, so one within the horizon is 0. Added to GLOSSARY.
- **Checks added:** labels count every Request; each row has a Label or a reason; no NYPD in the cohort.
- **Rebuild on change.** The publish input hash now covers every migration's checksum, not only `R__publication`'s. A change to a view such as `ML.TRAINING_LABELS` now rebuilds the outputs.
- **Dev:** V015 applied and published as `20261024T223312-686e8b25` in 74 s, with every check passing.
  - 3,690,883 labeled: 2,413,133 are 0 and 1,277,750 are 1 (34.6% Delayed).
  - Duplicate Closures are 266,835 of those labeled (7.2%), including 8,757 closed late.
  - No Label: 50,225 `not_matured`, 22,008 `closure_time_while_open`, 6,031 `closed_without_closure_time`, 5,064 `date_only_closure_on_horizon_day` (DOB 3,884), 749 `closure_before_creation`, 0 `reopened`.
  - 3,114,557 NYPD Requests are outside the cohort.
- **Tests:** `warehouse/tests/test_labels.py`, 2 Seam 2 tests:
  - Boundaries: exactly 168 h, then 1 s later; open; allowance met to the second, then 1 s short; Date-Only before, on and after the horizon day, and on the creation day; each exception; a Duplicate Closure; NYPD. The summary counts are asserted too.
  - An old Request seen only by an early extraction stays `not_matured`. A later pass that re-fetches only the reopened Request excludes it as `reopened`, and confirms the unchanged one open (1).
  - Dashboard-role test: reads the new view and is refused `ML.TRAINING_LABELS`.
- **For ticket 11:**
  - Filter on `matured_at_utc <= cutoff` for labels knowable at a training date.
  - Snapshot the labels used, since the view follows the latest data.
  - The model card can read the duplicate share and per-agency exclusions from `APP.PUBLISHED_LABEL_SUMMARY`.
- **Limitations:**
  - Reopenings are seen only from Request Versions. The backfill captured one state per Request, and dev holds only 56 Requests with more than one version.
  - Cohort membership follows the Current Request's agency, not the agency at First Observed.

**2026-10-24 (owner + agent): Duplicate Closures decided.**
- This criterion used to say "Duplicate Closures are kept as 0". It now says they are kept and labeled by timing. See ADR 0003: a late duplicate (one past 168 h) is 1, the same as any other Closure.
- HPD's "building-wide condition, original still open" duplicates count.
- GLOSSARY's Duplicate Closure entry is updated.
- No code change: V015 already behaves this way.

**2026-10-24 (agent): review fixes.**
Two-axis review of 30ce12c. Fixes, in `V016__label_versions.sql`:
- **Label versions are kept** (instruction §8 and §10, "preserve label versions").
  - `ML.TRAINING_LABELS` is now a table, one row per Request per change in Label, reason or allowance. `OPS.PUBLISH()` appends the changed rows and removes them again if the attempt is held. It never prunes.
  - Each row carries `observed_through` (when it became knowable) and provenance: `source_payload_hash`, `source_extracted_at` and `publication_id`.
  - Matured Labels known at time T: per Request, the latest version with `observed_through <= T` and a Label.
  - `ML.CURRENT_LABELS` is the old view: today's Labels.
- **Allowance is 72 h**, decided by the owner after measuring from `:created_at`. Since June 2026, 4–8% of Requests reached the source more than 48 h after creation, and about 1% more than 72 h. The figures are in `docs/source-inspection.md`; instruction §10 is updated.
- **Stale state.** A Request with a Quarantined Observation at least as new as its Current Request now has no Label, with reason `quarantined_observation`.
- **Shared Closure rules.** `CLEAN.CLOSURE_FACTS` holds the Date-Only, before-creation and horizon rules. `ANALYTICS.REQUEST_FACTS` and the Labels both read it, so they can't drift. `REQUEST_FACTS.as_of_utc` is now the latest processed extraction, the same "now" as the Labels. Inside a Publication this is no different.
- **Flags.** `date_only_closure` is never NULL. "Not a duplicate" wording is not a Duplicate Closure.
- **New check `labels_find_their_request_version`**, so a missed join can't silently undercount Duplicate Closures.
- **Doc.** instruction §10 now matches ADR 0003, with the duplicate share reported overall and within each Label.
- **Smells.**
  - The `reopened` CTE computes the Closed flag once.
  - The check alias is now `label_and_reason_both_or_neither`.
  - The summary's `date_only_closures` column was dropped: the horizon-day reason counts them per agency.
  - The ticket now says `resolution_description` rather than "resolution text". V015's comment keeps the old wording, because applied migrations are never edited.
- **Tests.**
  - The boundaries moved to 72 h.
  - New cases: a quarantined newer Observation, and "not a duplicate" wording.
  - The second test now runs three passes. It checks that an unchanged Request is confirmed by a later pass, and that a reopening adds a version while the earlier `0` stays.
- **Dev:** V016 applied and published as `20261025T011225-820dadec` in 114 s, with every check passing.
  - Labeled: 2,409,779 are 0 and 1,275,108 are 1 (34.6% Delayed). Duplicate Closures are 257,884 of the 0s and 8,755 of the 1s.
  - No Label: 56,499 `not_matured`, 21,756 `closure_time_while_open`, 6,031 `closed_without_closure_time`, 5,058 `date_only_closure_on_horizon_day`, 729 `closure_before_creation`, 0 `quarantined_observation`.
  - `ML.TRAINING_LABELS` holds 3,774,960 first versions, one per cohort Request.
  - The Overdue Queue is unchanged at 224,026, so the `REQUEST_FACTS` rewrite kept its behaviour.
- **Left as is:**
  - The all-migrations publish input hash. Label rules live in views that other migrations change, and these must rebuild the outputs.
  - `APP.PUBLISHED_LABEL_SUMMARY` stays readable by the dashboard role. The Model Performance page (ticket 11) shows the counts.
  - Reason strings stay plain literals, the usual SQL practice.
  - Test helpers are imported from sibling test modules, as `test_analytics.py` already does.
  - Gaps between the backfill and the daily cursor are a cursor-continuity problem. That belongs to ticket 13's weekly reconciliation.
  - `observed_through` advances only when a pass changes some Request. Every real daily pass does.
  - Versions start from this Publication. Earlier history (the backfill) can't be rebuilt point-in-time; the spec already calls retrospective evaluation approximate.
