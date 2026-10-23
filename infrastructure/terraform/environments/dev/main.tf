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
