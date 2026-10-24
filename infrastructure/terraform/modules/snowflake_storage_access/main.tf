# AWS side of a Snowflake storage integration: a role Snowflake's own IAM user
# assumes (with the integration's external ID) to read Observation data and
# manifests. Nothing else in the bucket is readable, and nothing is writable.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

variable "environment" {
  type = string
}

variable "bucket_arn" {
  type = string
}

variable "read_prefixes" {
  description = "Bucket prefixes Snowflake may read, e.g. [\"data/\", \"manifests/\"]."
  type        = list(string)
}

variable "snowflake_iam_user_arn" {
  description = "STORAGE_AWS_IAM_USER_ARN from DESC INTEGRATION."
  type        = string
}

variable "snowflake_external_id" {
  description = "STORAGE_AWS_EXTERNAL_ID from DESC INTEGRATION."
  type        = string
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [var.snowflake_iam_user_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [var.snowflake_external_id]
    }
  }
}

resource "aws_iam_role" "snowflake_read" {
  name               = "${var.environment}-nyc311-snowflake-read"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "read" {
  statement {
    sid       = "LocateBucket"
    actions   = ["s3:GetBucketLocation"]
    resources = [var.bucket_arn]
  }

  statement {
    sid       = "ListReadablePrefixes"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = [for p in var.read_prefixes : "${p}*"]
    }
  }

  statement {
    sid       = "ReadObjects"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = [for p in var.read_prefixes : "${var.bucket_arn}/${p}*"]
  }
}

resource "aws_iam_role_policy" "snowflake_read" {
  name   = "${var.environment}-nyc311-snowflake-read"
  role   = aws_iam_role.snowflake_read.id
  policy = data.aws_iam_policy_document.read.json
}

output "role_arn" {
  value = aws_iam_role.snowflake_read.arn
}
