# 08: Full backfill from 2025-01

**What to build:** Load every citywide Service Request created since 2025-01-01 (about 7M) so analytics and ML have enough history. Interruptions are survivable.

**Blocked by:** 05 (Request Versions, stale-overwrite guard and quarantine)

**Status:** ready-for-agent

**Mirror:** GitHub #9

- [ ] One-off ECS Fargate task runs the same collector in partition mode, one partition per creation month
- [ ] Resumes only incomplete months after interruption
- [ ] Loaded with bulk COPY under load tracking so no file loads twice through COPY and Snowpipe
- [ ] Reconciliation report: source rows, raw rows and distinct Current Requests per month
- [ ] Demo: kill the task mid-run, resume it, and counts reconcile
