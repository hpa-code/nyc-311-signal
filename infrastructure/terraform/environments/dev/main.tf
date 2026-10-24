terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
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
      Project     = "nyc-311-intelligence"
      Environment = "dev"
      ManagedBy   = "terraform"
    }
  }
}

module "core" {
  source = "../../modules/core"

  environment = "dev"
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
