# 15: Continuous deployment on merge

**What to build:** Merging to main ships the dashboard safely and repeatably, without long-lived credentials.

**Blocked by:** 14 (Deploy dashboard to AWS)

**Status:** ready-for-agent

**Mirror:** GitHub #16

- [ ] GitHub Actions authenticates to AWS with OIDC (short-lived credentials)
- [ ] On merge: build the image tagged with the commit SHA, push to ECR, deploy, run the health check
- [ ] PR CI also runs SQL checks against fixtures and a Docker build
- [ ] SQL migrations deploy in a compatible order; model promotion and backfills are not part of deployment

## Comments

**2026-10-25 (agent): code ready, not applied.** Creating the OIDC provider and the deploy role changes the account's IAM trust, so it waits for the owner.
- **PR CI** (`.github/workflows/ci.yml`):
  - **Warehouse tests:** the Seam 2 SQL checks against fixtures in `NYC311_DEV_TEST`, run when the repository secrets hold the dev service user's key, one run at a time.
  - **Docker builds:** collector, monitor and dashboard images, plus the dashboard health check.
  - **Terraform:** validate for dev and prod.
- **Deploy on merge** (`.github/workflows/deploy.yml`, GitHub environment `prod`):
  1. OIDC credentials.
  2. Build and push the arm64 image, tagged with the commit SHA.
  3. Prod migrations, which are additive by rule (new section in `CODING_STANDARDS.md`) and applied only once the image exists.
  4. Render and deploy the task definition.
  5. Confirm the primary deployment runs the new revision (an ECS rollback also ends "stable").
  6. Health check.
  - Model promotion and backfills are never part of it. The job is skipped until `AWS_DEPLOY_ROLE_ARN` is set.
- **OIDC role** (`modules/ci_deploy`, in `environments/prod`):
  - Trust: audience `sts.amazonaws.com`, subject `environment:prod`, and `job_workflow_ref` pinned to `deploy.yml` on `main`.
  - Permissions: push to the dashboard repository, register and describe task definitions, update and describe the dashboard service, and pass only the dashboard's two roles to ECS tasks.
  - The account had no GitHub OIDC provider; the module creates it.
- **Owner steps:**
  - Apply prod (with ticket 14).
  - Create the GitHub environment `prod`, limited to main, with its variables and the prod user's secrets.
  - Put the dev user's key in repository secrets for PR CI.
- **Left as is:**
  - PR CI builds x86 images; arm64 is first built at deploy.
  - A newer CI run replaces a pending older one in the warehouse-tests queue.
  - Service and cluster names are written into `deploy.yml` (they follow `<env>-nyc311-dashboard`).
