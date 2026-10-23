# 15: Continuous deployment on merge

**What to build:** Merging to main ships the dashboard safely and repeatably, without long-lived credentials.

**Blocked by:** 14 (Deploy dashboard to AWS)

**Status:** ready-for-agent

**Mirror:** GitHub #16

- [ ] GitHub Actions authenticates to AWS with OIDC (short-lived credentials)
- [ ] On merge: build the image tagged with the commit SHA, push to ECR, deploy, run the health check
- [ ] PR CI also runs SQL checks against fixtures and a Docker build
- [ ] SQL migrations deploy in a compatible order; model promotion and backfills are not part of deployment
