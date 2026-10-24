# Daily change-cursor collection: a Lambda running the collector, started by
# EventBridge Scheduler at 06:00 UTC (explicit timezone), with CloudWatch
# alarms to an SNS topic when an invocation fails or the daily run is missed.

terraform {
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
}

variable "environment" {
  type = string
}

variable "bucket_name" {
  type = string
}

variable "role_name" {
  description = "Ingestion role (already trusted by Lambda, and allowed the bucket and secrets)."
  type        = string
}

variable "role_arn" {
  type = string
}

variable "socrata_secret_name" {
  type = string
}

variable "initial_cursor" {
  description = "Source :updated_at to start from when no cursor is stored yet."
  type        = string
}

variable "alert_email" {
  description = "Subscribed to the alert topic; null for none."
  type        = string
  default     = null
}

data "aws_region" "current" {}

locals {
  name = "${var.environment}-nyc311-collector"
  # The ingestion package without its tests; boto3 comes with the runtime.
  ingestion_dir   = "${path.module}/../../../../ingestion"
  ingestion_files = [for f in fileset(local.ingestion_dir, "*.py") : f]
}

data "archive_file" "collector" {
  type        = "zip"
  output_path = "${path.module}/build/collector.zip"

  dynamic "source" {
    for_each = local.ingestion_files
    content {
      content  = file("${local.ingestion_dir}/${source.value}")
      filename = "ingestion/${source.value}"
    }
  }
}

resource "aws_cloudwatch_log_group" "collector" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 30
}

resource "aws_lambda_function" "collector" {
  function_name    = local.name
  role             = var.role_arn
  runtime          = "python3.13"
  handler          = "ingestion.lambda_handler.handler"
  filename         = data.archive_file.collector.output_path
  source_code_hash = data.archive_file.collector.output_base64sha256
  timeout          = 900
  memory_size      = 1024

  environment {
    variables = {
      NYC311_BUCKET              = var.bucket_name
      NYC311_SOCRATA_SECRET_NAME = var.socrata_secret_name
      NYC311_INITIAL_CURSOR      = var.initial_cursor
      NYC311_PAGE_SIZE           = "10000"
    }
  }

  depends_on = [aws_cloudwatch_log_group.collector, aws_iam_role_policy.collector_runtime]
}

# A failed invocation is not retried by Lambda: the alarm fires, and the next
# run resumes the unfinished pass from its checkpoint.
resource "aws_lambda_function_event_invoke_config" "collector" {
  function_name                = aws_lambda_function.collector.function_name
  maximum_retry_attempts       = 0
  maximum_event_age_in_seconds = 3600
}

data "aws_iam_policy_document" "collector_runtime" {
  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.collector.arn}:*"]
  }

  # A pass that cannot finish in one invocation continues in the next.
  statement {
    sid       = "ContinueItself"
    actions   = ["lambda:InvokeFunction"]
    resources = ["arn:aws:lambda:${data.aws_region.current.region}:*:function:${local.name}"]
  }
}

resource "aws_iam_role_policy" "collector_runtime" {
  name   = "${local.name}-runtime"
  role   = var.role_name
  policy = data.aws_iam_policy_document.collector_runtime.json
}

# ---------------------------------------------------------------------------
# Schedule
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
  name               = "${local.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.collector.arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "${local.name}-scheduler"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}

# 06:00 UTC: about 4.5 hours after the source's ~01:33 UTC daily publication.
resource "aws_scheduler_schedule" "daily" {
  name                         = "${local.name}-daily"
  schedule_expression          = "cron(0 6 * * ? *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.collector.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({})

    retry_policy {
      maximum_retry_attempts       = 3
      maximum_event_age_in_seconds = 3600
    }
  }
}

# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

resource "aws_sns_topic" "alerts" {
  name = "${var.environment}-nyc311-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == null ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Any failed invocation: an exception, a timeout, or giving up after too
# many continuations.
resource "aws_cloudwatch_metric_alarm" "collector_failed" {
  alarm_name          = "${local.name}-failed"
  alarm_description   = "The daily collector Lambda failed. Its pass resumes on the next run; check the logs."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.collector.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# The scheduler itself failing never reaches the function: alarm when no
# invocation at all happened for 24 consecutive hours.
resource "aws_cloudwatch_metric_alarm" "collector_missed" {
  alarm_name          = "${local.name}-missed"
  alarm_description   = "The daily collector Lambda has not been invoked for 24 hours."
  namespace           = "AWS/Lambda"
  metric_name         = "Invocations"
  dimensions          = { FunctionName = aws_lambda_function.collector.function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 24
  datapoints_to_alarm = 24
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

output "function_name" {
  value = aws_lambda_function.collector.function_name
}

output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}
