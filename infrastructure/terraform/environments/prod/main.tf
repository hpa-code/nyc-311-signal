# Production: the NYC311 Snowflake database and prod- AWS resources, in the
# same account and region as dev (instruction.md §13). Only prod runs the
# daily collector once it exists; disable dev's schedule then (ticket 06).
#
# Values that only exist after Snowflake's prod setup (snowflake/setup_prod.sql)
# come from terraform.tfvars (gitignored): see infrastructure/terraform/README.md.

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

  # Local state, gitignored (*.tfstate), as in dev.
  backend "local" {}
}

provider "aws" {
  region = "us-east-2"

  default_tags {
    tags = {
      Project     = "nyc-311-signal"
      Environment = "prod"
      ManagedBy   = "terraform"
    }
  }
}

variable "alert_email" {
  description = "Receives alerts."
  type        = string
  default     = null
}

variable "snowpipe_queue_arn" {
  description = "SHOW PIPES IN DATABASE NYC311 -> notification_channel."
  type        = string
}

variable "snowflake_iam_user_arn" {
  description = "DESC INTEGRATION NYC311_PROD_S3 -> STORAGE_AWS_IAM_USER_ARN."
  type        = string
}

variable "snowflake_external_id" {
  description = "DESC INTEGRATION NYC311_PROD_S3 -> STORAGE_AWS_EXTERNAL_ID."
  type        = string
}

variable "initial_cursor" {
  description = "Source :updated_at the first cursor pass starts from: just before the prod backfill's extraction."
  type        = string
}

variable "backfill_through" {
  description = "Last creation month of the prod backfill, YYYY-MM."
  type        = string
}

variable "collector_image_tag" {
  description = "Commit SHA of the collector image the prod backfill runs."
  type        = string
}

variable "monitor_image_tag" {
  description = "Commit SHA of the monitor image to run; null until one is pushed."
  type        = string
  default     = null
}

variable "dashboard_image_tag" {
  description = "Commit SHA of the dashboard image to start the service with; null keeps it off (and free)."
  type        = string
  default     = null
}

variable "domain_name" {
  description = "Public name of the dashboard; null serves plain HTTP on the load balancer's name."
  type        = string
  default     = null
}

module "core" {
  source = "../../modules/core"

  environment        = "prod"
  snowpipe_queue_arn = var.snowpipe_queue_arn
}

module "daily_collector" {
  source = "../../modules/daily_collector"

  environment         = "prod"
  bucket_name         = module.core.bucket_name
  role_name           = module.core.ingestion_role_name
  role_arn            = module.core.ingestion_role_arn
  socrata_secret_name = module.core.socrata_secret_name
  initial_cursor      = var.initial_cursor
  alert_email         = var.alert_email
}

module "backfill" {
  source = "../../modules/backfill"

  environment         = "prod"
  bucket_name         = module.core.bucket_name
  role_arn            = module.core.ingestion_role_arn
  socrata_secret_name = module.core.socrata_secret_name
  through             = var.backfill_through
  image_tag           = var.collector_image_tag
}

module "snowflake_storage_access" {
  source = "../../modules/snowflake_storage_access"

  environment            = "prod"
  bucket_arn             = module.core.bucket_arn
  read_prefixes          = [module.core.prefixes.data, module.core.prefixes.manifests]
  snowflake_iam_user_arn = var.snowflake_iam_user_arn
  snowflake_external_id  = var.snowflake_external_id
}

module "monitor" {
  source = "../../modules/monitor"

  environment           = "prod"
  alerts_topic_arn      = module.daily_collector.alerts_topic_arn
  snowflake_secret_arn  = module.core.snowflake_secret_arn
  snowflake_secret_name = module.core.snowflake_secret_name
  image_tag             = var.monitor_image_tag
}

module "dashboard" {
  source = "../../modules/dashboard"

  environment      = "prod"
  image_tag        = var.dashboard_image_tag
  domain_name      = var.domain_name
  alerts_topic_arn = module.daily_collector.alerts_topic_arn
}

module "ci_deploy" {
  source = "../../modules/ci_deploy"

  environment        = "prod"
  github_repository  = "hpa-code/nyc-311-signal"
  ecr_repository_arn = module.dashboard.repository_arn
  ecs_service_arn    = module.dashboard.service_arn
  task_role_arns     = module.dashboard.task_role_arns
}
