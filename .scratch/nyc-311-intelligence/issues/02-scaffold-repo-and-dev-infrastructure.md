# 02: Scaffold repo and dev infrastructure

**What to build:** A working project skeleton and a dev AWS environment that later slices build on. Applying Terraform for dev produces an empty, locked-down bucket and the secrets the collector will read.

**Blocked by:** 01 (Create AWS Free-plan account and budget alert)

**Status:** ready-for-agent

**Mirror:** GitHub #3

- [x] Python project with a lockfile; `.env.example` with no real values
- [x] PR CI runs the Python test suite (even if nearly empty)
- [ ] Terraform dev environment in us-east-2 with `dev-` prefixes: S3 bucket with data, manifests and checkpoints prefixes, encryption on, public access blocked
- [x] Ingestion IAM role scoped to its own S3 prefixes and its secrets
- [x] Secrets Manager entries for the Socrata app token and Snowflake credentials
- [x] Terraform state is not committed and contains no secrets

## Comments

**2026-10-22 (agent):** Code complete and `terraform validate` passes; not yet applied because ticket 01 (AWS account) is still open. The Terraform-dev box stays unchecked until `terraform apply` in `infrastructure/terraform/environments/dev` succeeds. Secret values are put out of band (see `infrastructure/terraform/README.md`). Secret names use the Secrets Manager path style `dev/nyc311/...` rather than a `dev-` prefix. The ingestion role allows PutObject on `data/` and `manifests/` only with `If-None-Match`, which makes those files immutable.
