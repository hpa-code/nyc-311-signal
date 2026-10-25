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
