# 02: Scaffold repo and dev infrastructure

**What to build:** A working project skeleton and a dev AWS environment that later slices build on. Applying Terraform for dev produces an empty, locked-down bucket and the secrets the collector will read.

**Blocked by:** 01 (Create AWS Free-plan account and budget alert)

**Status:** ready-for-agent

**Mirror:** GitHub #3

- [ ] Python project with a lockfile; `.env.example` with no real values
- [ ] PR CI runs the Python test suite (even if nearly empty)
- [ ] Terraform dev environment in us-east-2 with `dev-` prefixes: S3 bucket with data, manifests and checkpoints prefixes, encryption on, public access blocked
- [ ] Ingestion IAM role scoped to its own S3 prefixes and its secrets
- [ ] Secrets Manager entries for the Socrata app token and Snowflake credentials
- [ ] Terraform state is not committed and contains no secrets
