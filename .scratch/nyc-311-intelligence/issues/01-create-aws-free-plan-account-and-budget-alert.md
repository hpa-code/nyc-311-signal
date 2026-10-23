# 01: Create AWS Free-plan account and budget alert

**What to build:** Set up the accounts and credentials the pipeline needs. Only a human can do this (payment card, identity verification, signups).

**Blocked by:** None (can start immediately)

**Status:** done

**Mirror:** GitHub #2

- [x] AWS account created on the Free plan, used in us-east-2
- [x] AWS budget alert at $10 per month (changed from "$150 of the $200 credits consumed"; see comment)
- [x] Socrata app token registered for the NYC Open Data API
- [x] Snowflake key pair generated for service authentication, with the public key assigned to a Snowflake service user
- [x] Account IDs and region recorded somewhere the agent can read (no secrets in Git)

## Comments

**2026-10-23 (agent):** Done with the owner.
- AWS account `778031418236`, region us-east-2. CLI profile `nyc311` uses IAM user `hari-admin` with an access key. IAM Identity Center was unavailable on this account, and the user has no console sign-in, so there is no MFA on the IAM user; root should have MFA.
- Budget: the owner chose a **$10 per month** cost alert instead of the $150-of-$200-credits alert. It will fire every month once the dashboard is always on (~$37 per month from week 4). This was accepted knowingly. Spec story 49 and instruction.md were updated to match.
- Socrata app token verified against the API (HTTP 200).
- Snowflake account `BAFEDNH-HT04789` in `AWS_US_EAST_2`. Service user `NYC311_SVC` (TYPE = SERVICE) with an RSA public key; a key-pair login was verified. The private key is at `~/.nyc311/snowflake_rsa_key.p8`, outside the repo.
- Non-secret IDs and local fallbacks are in the gitignored `.env`.
