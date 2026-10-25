# 14: Deploy dashboard to AWS

**What to build:** The dashboard is live at a public HTTPS URL that a portfolio reviewer can open without logging in. Note: a domain must be bought, which is a human step.

**Blocked by:** 09 (Analytics pages: Overview, Geographic and Overdue Queue)

**Status:** ready-for-agent

**Mirror:** GitHub #15

- [ ] Docker image built and pushed to ECR; ECS Fargate service (0.25 vCPU / 0.5 GB) behind an ALB with a TLS certificate
- [ ] Prod environment (`NYC311` database, `prod-` resources) via Terraform
- [ ] ECS accepts inbound traffic only from the ALB; the task reads Snowflake credentials from Secrets Manager; the browser never receives credentials
- [ ] Read-only dashboard role confirmed: it cannot write or read restricted tables
- [ ] Health check passes; public URL works

## Comments

**2026-10-23 (agent, from 04):** Locally the dashboard connects as `NYC311_SVC` with role `NYC311_DEV_DASHBOARD`. The same key can also use the pipeline role. For deployment, create a dedicated Snowflake user that holds only the dashboard role, with its own key pair in its own secret.

**2026-10-25 (agent, from 13):** Ticket 13's "application health" alarm is deferred to here, because there was no dashboard to watch yet. Add a CloudWatch alarm on the ALB target group's `UnHealthyHostCount` (or `HealthyHostCount` < 1) to the `dev-nyc311-alerts` topic (`module.daily_collector.alerts_topic_arn`), next to the monitor alarms in `modules/monitor`, and add a row for it to `docs/runbook.md`.

**2026-10-25 (agent): code ready, not deployed.** Written overnight under the owner's instruction to do everything except steps that spend money or change security. This ticket's remaining steps cost money (load balancer and task, about $41 a month, plus a domain) or set up credentials, so they wait for the owner.
- **Image** (`dashboard/Dockerfile`):
  - Python 3.13 slim with the hash-checked dashboard group. It runs as a non-root user, with a Docker `HEALTHCHECK` on `/_stcore/health`.
  - Checked locally: an arm64 build serves `ok` on the health endpoint, and every page module imports.
  - `.dockerignore` lets in only the packages the images copy.
- **Terraform** (`modules/dashboard`; until `image_tag` is set, only the ECR repository and the secret exist, and no costly resources):
  - **Service:** Fargate 0.25 vCPU / 0.5 GB on ARM64, in the default VPC's public subnets, with no NAT.
  - **Security groups:** the task accepts 8501 only from the load balancer's security group. Its egress is 443 (Snowflake, ECR, Secrets Manager, logs) plus 80, for Snowflake's certificate revocation checks.
  - **Load balancer:** internet-facing, in two subnets, with an idle timeout of 300 s for Streamlit's websockets.
  - **HTTPS:** with `domain_name`, an ACM certificate (DNS validation records as an output), TLS 1.2+ and an HTTP→HTTPS redirect. Without one, plain HTTP for a first check.
  - **Deploys:** the circuit breaker rolls back a failed one. Terraform ignores later task definitions (CI owns them).
  - **Alarm:** `prod-nyc311-dashboard-unhealthy` (HealthyHostCount < 1 for 10 min), the application-health alarm deferred from ticket 13, with a runbook row.
  - **Credentials:** the task role can read only `prod/nyc311/snowflake-dashboard-user`.
- **Prod** (`environments/prod`, validated): core, the collector, backfill, Snowflake storage access, the monitor, the dashboard and the CI role. The values that only exist after Snowflake's setup come from `terraform.tfvars`.
- **Snowflake prod** (`snowflake/setup_prod.sql`, for the owner to run as ACCOUNTADMIN): roles, the `NYC311` database, the storage integration, and two users:
  - `NYC311_PROD_SVC` (pipeline and dashboard roles), so dev's key never reaches prod;
  - `NYC311_DASHBOARD` (dashboard role only), for the deployed app.
- **Environments:** `NYC311_ENV` selects prod for every entry point (ticket 13 commit).
- **Owner steps,** in order (`infrastructure/terraform/README.md`, "Production and the public dashboard"):
  1. Run the Snowflake setup and the prod migrations.
  2. Fill in `terraform.tfvars` and apply prod without the dashboard.
  3. Put the secrets, push the images and run the prod backfill.
  4. Create the dashboard user's key.
  5. Buy the domain, set `dashboard_image_tag` and `domain_name`, apply, and add the DNS records.
- **Then:** confirm the read-only role in prod (the role test runs against dev), and check that the health check passes and the public URL works.
