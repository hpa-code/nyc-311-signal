# Coding standards

Judgement calls a reviewer applies to a diff. Mechanical rules are checks instead: `mypy`, and the offline tests run by `.githooks/pre-commit` and CI (including `warehouse/tests/test_migrations_frozen.py`: applied migrations are never edited).

## Vocabulary

Names, comments and docs use the terms in `GLOSSARY.md`, and none of the words listed under _Avoid_. A source field keeps its API name in backticks (`resolution_description`, `complaint_type`).

## SQL over Current Requests

**Filter with a semi-join.** To restrict rows to a set of Requests (the Modeling Cohort, Requests with a Label), use `WHERE unique_key IN (SELECT unique_key FROM …)`. Use a join only to bring in columns, and only on a key that is unique on the joined side. If a Current Request appears twice, it should show up twice, never four times: the check `one_current_request_per_unique_key` must fail alone. `test_a_failed_check_holds_and_the_previous_publication_stays_visible` checks this, but only in Seam 2.

**One definition per rule.** The Closure, Date-Only and Seven-Day Horizon rules live in `CLEAN.CLOSURE_FACTS`. A new output reads them from there instead of re-deriving them.

## A new per-Publication output

All of these land in the same change:

- a table `ANALYTICS.PUB_<name>` with `publication_id`, in a new `V` migration;
- in `OPS.PUBLISH()` (`R__publication.sql`): the insert, at least one check that ties its totals to another output, and a prune line keyed on `OPS.PUBLICATION_KEEP`;
- a secure view `APP.PUBLISHED_<name>` filtered to published Publications, and its grant to the dashboard role;
- the table in `empty_db`'s truncate list (`warehouse/tests/conftest.py`), and the view in `test_dashboard_role_reads_app_views_and_nothing_else`.

State that outlives a Publication (`ANALYTICS.DAILY_BACKLOG`, `ML.TRAINING_LABELS`) is never pruned. Its rows from a held attempt are deleted before commit.

## Migrations

**Additive only.** A deploy applies pending migrations while the previous dashboard image still serves (`.github/workflows/deploy.yml`), and a Publication may be running. A migration may add tables, columns and views, or replace a view or procedure compatibly. It never drops or renames anything the deployed code still reads. To retire something, stop reading it in one release, then drop it in a later migration.
