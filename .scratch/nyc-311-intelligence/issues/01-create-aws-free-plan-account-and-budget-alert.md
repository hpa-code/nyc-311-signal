# 01: Create AWS Free-plan account and budget alert

**What to build:** Set up the accounts and credentials the pipeline needs. Only a human can do this (payment card, identity verification, signups).

**Blocked by:** None (can start immediately)

**Status:** ready-for-human

**Mirror:** GitHub #2

- [ ] AWS account created on the Free plan, used in us-east-2
- [ ] AWS budget alert fires when $150 of the $200 Free-plan credits is consumed
- [ ] Socrata app token registered for the NYC Open Data API
- [ ] Snowflake key pair generated for service authentication, with the public key assigned to a Snowflake service user
- [ ] Account IDs and region recorded somewhere the agent can read (no secrets in Git)
