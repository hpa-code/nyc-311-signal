data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  prefix = "${var.environment}-nyc311"

  # S3 prefixes. Snowpipe will auto-load only data/; manifests/ and
  # checkpoints/ are control files the collector owns.
  prefixes = {
    data        = "data/"
    manifests   = "manifests/"
    checkpoints = "checkpoints/"
  }
}

# ---------------------------------------------------------------------------
# Observation bucket
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "observations" {
  # Bucket names are global; the account ID keeps this one unique.
  bucket = "${local.prefix}-observations-${data.aws_caller_identity.current.account_id}"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "observations" {
  bucket = aws_s3_bucket.observations.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "observations" {
  bucket = aws_s3_bucket.observations.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "observations" {
  bucket = aws_s3_bucket.observations.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

data "aws_iam_policy_document" "observations_bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.observations.arn,
      "${aws_s3_bucket.observations.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

# Observation files are kept forever but move to cheaper storage after 90 days.
resource "aws_s3_bucket_lifecycle_configuration" "observations" {
  bucket = aws_s3_bucket.observations.id

  rule {
    id     = "data-to-infrequent-access"
    status = "Enabled"

    filter {
      prefix = local.prefixes.data
    }

    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }
  }
}

resource "aws_s3_bucket_policy" "observations" {
  bucket = aws_s3_bucket.observations.id
  policy = data.aws_iam_policy_document.observations_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.observations]
}

# ---------------------------------------------------------------------------
# Secrets
#
# Terraform creates the secret containers only. Values are put out of band
# (aws secretsmanager put-secret-value) so they never enter Terraform state.
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "socrata_app_token" {
  name        = "${var.environment}/nyc311/socrata-app-token"
  description = "NYC Open Data (Socrata) app token for the collector."
}

resource "aws_secretsmanager_secret" "snowflake_service_user" {
  name        = "${var.environment}/nyc311/snowflake-service-user"
  description = "Snowflake key-pair credentials (account, user, private key) for the pipeline service user."
}

# ---------------------------------------------------------------------------
# Ingestion role: assumed by the daily Lambda and the backfill ECS task.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "ingestion_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com", "ecs-tasks.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "ingestion" {
  name               = "${local.prefix}-ingestion"
  assume_role_policy = data.aws_iam_policy_document.ingestion_assume.json
}

data "aws_iam_policy_document" "ingestion" {
  statement {
    sid       = "ListOwnPrefixes"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.observations.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = [for p in values(local.prefixes) : "${p}*"]
    }
  }

  statement {
    sid     = "ReadOwnPrefixes"
    actions = ["s3:GetObject"]
    resources = [
      for p in values(local.prefixes) : "${aws_s3_bucket.observations.arn}/${p}*"
    ]
  }

  # Observation files and manifests are immutable: writes must be
  # conditional (If-None-Match: *), so an existing object is never replaced.
  statement {
    sid     = "CreateImmutableFiles"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.observations.arn}/${local.prefixes.data}*",
      "${aws_s3_bucket.observations.arn}/${local.prefixes.manifests}*",
    ]

    condition {
      test     = "Null"
      variable = "s3:if-none-match"
      values   = ["false"]
    }
  }

  # Checkpoints (and the collector's run lock, stored beside them) are
  # mutable control files, so they may be overwritten and deleted.
  statement {
    sid     = "ManageCheckpoints"
    actions = ["s3:PutObject", "s3:DeleteObject"]
    resources = [
      "${aws_s3_bucket.observations.arn}/${local.prefixes.checkpoints}*",
    ]
  }

  statement {
    sid     = "ReadOwnSecrets"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.socrata_app_token.arn,
      aws_secretsmanager_secret.snowflake_service_user.arn,
    ]
  }
}

resource "aws_iam_role_policy" "ingestion" {
  name   = "${local.prefix}-ingestion"
  role   = aws_iam_role.ingestion.id
  policy = data.aws_iam_policy_document.ingestion.json
}
