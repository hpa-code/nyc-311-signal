terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }

  # Local state, gitignored (*.tfstate). Secret values are never managed by
  # Terraform, so state holds ARNs and names only.
  backend "local" {}
}

provider "aws" {
  region = "us-east-2"

  default_tags {
    tags = {
      Project     = "nyc-311-signal"
      Environment = "dev"
      ManagedBy   = "terraform"
    }
  }
}

variable "alert_email" {
  description = "Receives collector alerts (set in terraform.tfvars, which is gitignored)."
  type        = string
  default     = null
}

module "core" {
  source = "../../modules/core"

  environment = "dev"
  # SHOW PIPES IN DATABASE NYC311_DEV -> notification_channel
  snowpipe_queue_arn = "arn:aws:sqs:us-east-2:870900716361:sf-snowpipe-AIDA4VRN2KNEZAUINA3LH-9p8s2apCkypqraRs4ZTMRw"
}

module "daily_collector" {
  source = "../../modules/daily_collector"

  environment         = "dev"
  bucket_name         = module.core.bucket_name
  role_name           = module.core.ingestion_role_name
  role_arn            = module.core.ingestion_role_arn
  socrata_secret_name = module.core.socrata_secret_name
  # Just after the one-week slice was collected (2026-10-23 20:16 UTC).
  initial_cursor = "2026-10-23T20:16:00.000Z"
  alert_email    = var.alert_email
}

module "backfill" {
  source = "../../modules/backfill"

  environment         = "dev"
  bucket_name         = module.core.bucket_name
  role_arn            = module.core.ingestion_role_arn
  socrata_secret_name = module.core.socrata_secret_name
  # Backfill run backfill-202501-202610; the daily cursor covers what follows.
  through = "2026-10"
  # Commit the pushed image was built from (README: Historical backfill).
  image_tag = "6e2be91"
}

module "monitor" {
  source = "../../modules/monitor"

  environment           = "dev"
  alerts_topic_arn      = module.daily_collector.alerts_topic_arn
  snowflake_secret_arn  = module.core.snowflake_secret_arn
  snowflake_secret_name = module.core.snowflake_secret_name
  # Commit the pushed monitor image was built from (README: Monitoring).
  image_tag = "ce4aa14"
}

# Values from `DESC INTEGRATION NYC311_DEV_S3` (snowflake/setup_dev.sql).
# Not secret: Snowflake's IAM user and the external ID that pins this role to
# that one integration.
module "snowflake_storage_access" {
  source = "../../modules/snowflake_storage_access"

  environment            = "dev"
  bucket_arn             = module.core.bucket_arn
  read_prefixes          = [module.core.prefixes.data, module.core.prefixes.manifests]
  snowflake_iam_user_arn = "arn:aws:iam::870900716361:user/bsid2000-s"
  snowflake_external_id  = "XM00257_SFCRole=5_vLPqWT7BJQyVDUaMQLSWbMVrLoc="
}

# The public dashboard, on dev data until prod's pipeline runs (ticket 14):
# dev already holds the backfill, the daily passes and the promoted model.
# image_tag is the commit the pushed image was built from; null keeps the
# load balancer, task and CloudFront off (and free).
module "dashboard" {
  source = "../../modules/dashboard"

  environment      = "dev"
  image_tag        = "b907155"
  alerts_topic_arn = module.daily_collector.alerts_topic_arn
}

# Continuous deployment of the dashboard above (ticket 15), from the GitHub
# environment "dev" (repository variable DEPLOY_ENV = dev). This creates the
# account's GitHub OIDC provider; prod passes its ARN in when it takes over.
module "ci_deploy" {
  source = "../../modules/ci_deploy"

  environment        = "dev"
  github_repository  = "hpa-code/nyc-311-signal"
  ecr_repository_arn = module.dashboard.repository_arn
  ecs_service_arn    = module.dashboard.service_arn
  task_role_arns     = module.dashboard.task_role_arns
}

# Account-wide guard: the AWS credits pay for usage until they run out or
# expire, then the card does. Net cost (credits applied) above $1 in a month
# means that has started. The monthly $10 budget from ticket 01 tracks gross
# spend instead, so it fires every month the dashboard runs. Budgets bill
# from the third one on; this is the second.
resource "aws_budgets_budget" "credits_exhausted" {
  count        = var.alert_email != null ? 1 : 0
  name         = "nyc311-credits-exhausted"
  budget_type  = "COST"
  limit_amount = "1"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_types {
    include_credit = true
    include_refund = true
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
}
