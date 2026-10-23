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
