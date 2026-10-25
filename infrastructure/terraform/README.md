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

A second schedule, `dev-nyc311-collector-reconcile`, invokes it daily at 08:00 UTC with `{"plan": "reconcile"}`. On Sundays that starts the week's Reconciliation Pass (`ingestion/reconcile.py`, run ID `reconcile-<year>W<week>`). On any day it resumes a pass left unfinished, because a pass's loaded files hold up processing until its manifest exists. Otherwise it does nothing. To run it by hand, pass that payload instead of `{}`.

## Monitoring

`modules/monitor` holds an hourly container-image Lambda, `dev-nyc311-monitor`, that reads `APP.MONITOR` as the read-only dashboard role (credentials from `dev/nyc311/snowflake-service-user`) and publishes metrics under the CloudWatch namespace `NYC311/dev`. Alarms on them notify the alerts topic; `docs/runbook.md` says what each means. Until `image_tag` is set, only its ECR repository exists. Build and push the image as for the backfill, then set `image_tag` in `environments/dev/main.tf` and apply:

```sh
TAG=$(git rev-parse --short HEAD)
REPO=$(terraform -chdir=infrastructure/terraform/environments/dev output -raw monitor_repository_url)
aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin "${REPO%%/*}"
docker build --platform linux/arm64 --provenance=false -f monitoring/Dockerfile -t "${REPO}:${TAG}" .
docker push "${REPO}:${TAG}"
```

Only alarmed signals go to CloudWatch, keeping within its 10 free custom metrics; run volumes are in `OPS.RUN_METRICS` and on the Pipeline Health page.

## Snowpipe notifications

New objects under `data/daily/` (daily cursor passes) notify the Snowpipe `RAW.OBSERVATIONS_PIPE`. Slices and backfill write `data/bulk/`, which only bulk COPY (`python -m warehouse.pipeline`) loads, so no file is loaded by both. Its SQS queue ARN comes from `SHOW PIPES IN DATABASE NYC311_DEV` (`notification_channel`) and is set as `snowpipe_queue_arn` in `environments/dev/main.tf`. The queue is owned by Snowflake, one per Snowflake account, so a recreated pipe usually keeps the same ARN.

## Historical backfill

`modules/backfill` holds the collector image (ECR `dev-nyc311-collector`), the `dev-nyc311` ECS cluster and the one-off Fargate task `dev-nyc311-backfill`, which runs `python -m ingestion.backfill --through 2026-10` under the ingestion role: run `backfill-202501-202610`, one partition per creation month, written to `data/bulk/`. Nothing schedules it.

Build and push the image, tagged with the commit it is built from (Apple silicon builds arm64 natively, which the task uses). Tags are immutable, so a resumed run never picks up different code. Then set `image_tag` in `environments/dev/main.tf` to that SHA and apply, which registers a new task definition revision:

```sh
cd ../../../..   # repo root, on a clean commit
TAG=$(git rev-parse --short HEAD)
REPO=$(terraform -chdir=infrastructure/terraform/environments/dev output -raw backfill_repository_url)
aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin "${REPO%%/*}"
docker build --platform linux/arm64 -f ingestion/Dockerfile -t "${REPO}:${TAG}" .
docker push "${REPO}:${TAG}"
```

Start it, and stop it (ECS sends SIGTERM, then SIGKILL after 30 s):

```sh
TF="terraform -chdir=infrastructure/terraform/environments/dev output -raw"
aws ecs run-task --region us-east-2 --cluster "$($TF backfill_cluster_name)" --launch-type FARGATE \
  --task-definition "$($TF backfill_task_definition)" \
  --network-configuration "$($TF backfill_network_configuration)" --query 'tasks[0].taskArn' --output text
aws ecs stop-task --region us-east-2 --cluster dev-nyc311 --task <task-arn>
```

Starting it again resumes: complete months are skipped and the interrupted month continues from its last stored page. A killed task never releases its lease, so the new task first waits for it to expire (the lease lasts 16 minutes; it waits up to 20). Progress, at any time:

```sh
uv run --env-file .env python -m ingestion.backfill --through 2026-10 --store s3:$(terraform -chdir=infrastructure/terraform/environments/dev output -raw bucket_name) --status
```

Logs are in CloudWatch under `/ecs/dev-nyc311-backfill`. Once the run's manifest exists, load and reconcile it with `python -m warehouse.backfill load|report --through 2026-10`.
