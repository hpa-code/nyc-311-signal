# Terraform

One folder per environment under `environments/` (`dev`, later `prod`), both in us-east-2, sharing modules under `modules/`.

## Apply dev

```sh
cd infrastructure/terraform/environments/dev
terraform init
terraform plan -out=tfplan   # review before applying
terraform apply tfplan
```

State is local and gitignored. The observation bucket has `prevent_destroy`; removing it needs a deliberate code change.

## Secret values

Terraform creates the Secrets Manager entries but never their values, so state holds no secrets. Put the values after the first apply:

```sh
aws secretsmanager put-secret-value --region us-east-2 \
  --secret-id dev/nyc311/socrata-app-token \
  --secret-string '{"app_token":"..."}'

aws secretsmanager put-secret-value --region us-east-2 \
  --secret-id dev/nyc311/snowflake-service-user \
  --secret-string file://$HOME/nyc311-snowflake-secret.json   # {"account","user","private_key"}; keep outside the repo
```

## Daily collector

`modules/daily_collector` packages `ingestion/*.py` into a Lambda (`dev-nyc311-collector`), which EventBridge Scheduler starts daily at 06:00 UTC. A failed invocation, or none for 24 hours, raises a CloudWatch alarm on the `dev-nyc311-alerts` SNS topic. The alert email comes from `alert_email` in `terraform.tfvars` (gitignored). AWS emails a confirmation link the first time.

The first pass starts from `initial_cursor`. After that, the cursor lives in the bucket at `checkpoints/cursor/311.json`. To run a pass by hand:

```sh
aws lambda invoke --region us-east-2 --function-name dev-nyc311-collector \
  --invocation-type Event --payload '{}' /dev/null
```

## Snowpipe notifications

New objects under `data/` notify the Snowpipe `RAW.OBSERVATIONS_PIPE`. Its SQS queue ARN comes from `SHOW PIPES IN DATABASE NYC311_DEV` (`notification_channel`) and is set as `snowpipe_queue_arn` in `environments/dev/main.tf`. The queue is owned by Snowflake, one per Snowflake account, so a recreated pipe usually keeps the same ARN.
