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
