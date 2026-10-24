# AWS Organization policies

Account 778031418236 is a member of organization `o-c640tvtq4m`, managed from account 744488454542. That organization was set up with managed-account guardrails, and one of them is the resource control policy `ManagedAccountResourceControlPolicy`. It denies `sts:*`, `s3:*` and other actions to any principal outside the organization. That blocks Snowflake's IAM user from assuming `dev-nyc311-snowflake-read`, whatever that role's trust policy says.

- `ManagedAccountResourceControlPolicy.original.json`: the policy as found on 2026-10-23.
- `ManagedAccountResourceControlPolicy.snowflake-exception.json`: the policy in use. Only for `sts:*`, it exempts Snowflake's IAM user `arn:aws:iam::870900716361:user/bsid2000-s` (from `DESC INTEGRATION NYC311_DEV_S3`). Every other action stays denied to it. Once Snowflake has assumed the role, its S3 reads run as that role's session, which is inside the organization.

The policy is edited by hand in the management account (Organizations → Policies → Resource control policies). It is not managed by Terraform. AWS automation may restore the original. If Snowflake stages start failing with `not authorized to perform: sts:AssumeRole`, check here first.
