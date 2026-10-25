# Pipeline monitoring: an hourly Lambda reads APP.MONITOR from Snowflake (as
# the read-only dashboard role) and publishes CloudWatch metrics under
# NYC311/<environment>; alarms on them notify the alerts topic. The Lambda is
# a container image in ECR (it needs the Snowflake connector). Until
# image_tag is set, only the repository exists.

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

variable "alerts_topic_arn" {
  type = string
}

variable "snowflake_secret_arn" {
  type = string
}

variable "snowflake_secret_name" {
  type = string
}

variable "image_tag" {
  description = "Commit SHA of the monitor image to run; null until one is pushed."
  type        = string
  default     = null
}

locals {
  name      = "${var.environment}-nyc311-monitor"
  namespace = "NYC311/${var.environment}"
  enabled   = var.image_tag != null
}

resource "aws_ecr_repository" "monitor" {
  name                 = local.name
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "monitor" {
  repository = aws_ecr_repository.monitor.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 3 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 3 }
      action       = { type = "expire" }
    }]
  })
}

# ---------------------------------------------------------------------------
# Lambda
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "monitor" {
  name               = local.name
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_cloudwatch_log_group" "monitor" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 30
}

data "aws_iam_policy_document" "monitor" {
  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.monitor.arn}:*"]
  }

  statement {
    sid       = "ReadSnowflakeCredentials"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.snowflake_secret_arn]
  }

  statement {
    sid       = "PublishMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = [local.namespace]
    }
  }
}

resource "aws_iam_role_policy" "monitor" {
  name   = local.name
  role   = aws_iam_role.monitor.id
  policy = data.aws_iam_policy_document.monitor.json
}

resource "aws_lambda_function" "monitor" {
  count         = local.enabled ? 1 : 0
  function_name = local.name
  role          = aws_iam_role.monitor.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.monitor.repository_url}:${var.image_tag}"
  architectures = ["arm64"]
  timeout       = 120
  memory_size   = 512

  environment {
    variables = {
      NYC311_ENV                   = var.environment
      NYC311_SNOWFLAKE_SECRET_NAME = var.snowflake_secret_name
    }
  }

  depends_on = [aws_cloudwatch_log_group.monitor, aws_iam_role_policy.monitor]
}

resource "aws_lambda_function_event_invoke_config" "monitor" {
  count                        = local.enabled ? 1 : 0
  function_name                = aws_lambda_function.monitor[0].function_name
  maximum_retry_attempts       = 0
  maximum_event_age_in_seconds = 600
}

# ---------------------------------------------------------------------------
# Schedule: hourly, at :15
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  count              = local.enabled ? 1 : 0
  name               = "${local.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

resource "aws_iam_role_policy" "scheduler" {
  count = local.enabled ? 1 : 0
  name  = "${local.name}-scheduler"
  role  = aws_iam_role.scheduler[0].id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "lambda:InvokeFunction", Resource = aws_lambda_function.monitor[0].arn }]
  })
}

resource "aws_scheduler_schedule" "hourly" {
  count                        = local.enabled ? 1 : 0
  name                         = "${local.name}-hourly"
  schedule_expression          = "cron(15 * * * ? *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.monitor[0].arn
    role_arn = aws_iam_role.scheduler[0].arn
    input    = jsonencode({})

    retry_policy {
      maximum_retry_attempts       = 2
      maximum_event_age_in_seconds = 600
    }
  }
}

# ---------------------------------------------------------------------------
# Alarms, on the metrics the monitor publishes (APP.MONITOR)
# ---------------------------------------------------------------------------

locals {
  alarms = {
    publication-stale = {
      metric      = "PublicationAgeHours"
      comparison  = "GreaterThanOrEqualToThreshold"
      threshold   = 36
      missing     = "breaching"
      description = "No complete Publication for 36 hours (or the monitor reports nothing). The dashboard shows stale data."
    }
    publication-held = {
      metric      = "PublicationAttemptFailed"
      comparison  = "GreaterThanOrEqualToThreshold"
      threshold   = 1
      missing     = "notBreaching"
      description = "The latest Publication attempt was held or failed; the dashboard keeps the previous one. See OPS.PUBLICATIONS.checks."
    }
    processing-failing = {
      metric      = "ProcessingFailing"
      comparison  = "GreaterThanOrEqualToThreshold"
      threshold   = 1
      missing     = "notBreaching"
      description = "The readiness check failed since it last succeeded. See OPS.STEP_STATUS and OPS.PIPELINE_RUNS."
    }
    processing-stalled = {
      metric      = "ProcessingAgeHours"
      comparison  = "GreaterThanOrEqualToThreshold"
      threshold   = 72
      missing     = "notBreaching"
      description = "No Observations processed for 3 days. RAW.NEW_OBSERVATIONS goes stale after 14 unconsumed days: see docs/runbook.md."
    }
    stuck-runs = {
      metric      = "StuckRuns"
      comparison  = "GreaterThanOrEqualToThreshold"
      threshold   = 1
      missing     = "notBreaching"
      description = "A collector run has waited more than 6 hours to reconcile with its manifest."
    }
    source-stale = {
      metric      = "SourceLagHours"
      comparison  = "GreaterThanOrEqualToThreshold"
      threshold   = 48
      missing     = "notBreaching"
      description = "The latest cursor run succeeded, but the newest change it found was over 48 hours old: the source may have stopped publishing."
    }
    missed-changes = {
      metric      = "MissedChanges"
      comparison  = "GreaterThanOrEqualToThreshold"
      threshold   = 1
      missing     = "notBreaching"
      description = "The weekly reconciliation found changes the change cursor missed (now taken in). See APP.RUN_HEALTH."
    }
    reconciliation-stale = {
      metric      = "ReconciliationAgeDays"
      comparison  = "GreaterThanOrEqualToThreshold"
      threshold   = 8
      missing     = "notBreaching"
      description = "No reconciliation run processed for 8 days."
    }
    scoring-incomplete = {
      metric      = "ScoringCoverage"
      comparison  = "LessThanThreshold"
      threshold   = 1
      missing     = "notBreaching"
      description = "The latest Publication left eligible Requests without an Admission Score: no approved model, or a scoring fault."
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "signal" {
  for_each          = local.enabled ? local.alarms : {}
  alarm_name        = "${local.name}-${each.key}"
  alarm_description = each.value.description
  namespace         = local.namespace
  metric_name       = each.value.metric
  statistic         = each.value.comparison == "LessThanThreshold" ? "Minimum" : "Maximum"
  # The monitor publishes once an hour, at :15. Two of the last three hours
  # must breach: a crossing has to persist, and for publication-stale (missing
  # data breaching) the monitor must be silent for two hours, not just the
  # current one before :15.
  period              = 3600
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  threshold           = each.value.threshold
  comparison_operator = each.value.comparison
  treat_missing_data  = each.value.missing
  alarm_actions       = [var.alerts_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "monitor_failed" {
  count               = local.enabled ? 1 : 0
  alarm_name          = "${local.name}-failed"
  alarm_description   = "The monitor Lambda failed; pipeline alarms may be blind. Check its logs."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.monitor[0].function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alerts_topic_arn]
}

output "repository_url" {
  value = aws_ecr_repository.monitor.repository_url
}
