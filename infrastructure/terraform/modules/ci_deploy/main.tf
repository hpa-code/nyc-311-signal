# Continuous deployment without stored AWS keys: GitHub Actions exchanges its
# OIDC token for this role's short-lived credentials, and only for workflow
# runs on the main branch of one repository. The role can push the dashboard
# image and roll the dashboard service onto a new task definition, nothing
# else (no Terraform, no data, no secrets).

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

variable "github_repository" {
  description = "owner/name of the repository whose main branch may deploy."
  type        = string
}

variable "github_subject_prefix" {
  description = "Start of the token's sub claim (GET /repos/{repo}/actions/oidc/customization/sub -> sub_claim_prefix). With immutable subjects it carries the owner and repository IDs: repo:owner@id/name@id."
  type        = string
}

variable "ecr_repository_arn" {
  type = string
}

variable "ecs_service_arn" {
  description = "The dashboard service; null until it exists (the role then cannot deploy)."
  type        = string
  default     = null
}

variable "github_oidc_provider_arn" {
  description = "The account's GitHub OIDC provider, when another environment already created it; null creates it here."
  type        = string
  default     = null
}

variable "task_role_arns" {
  description = "The dashboard task's execution and task roles, which a new task definition names."
  type        = list(string)
}

locals {
  name              = "${var.environment}-nyc311-ci-deploy"
  oidc_provider_arn = var.github_oidc_provider_arn != null ? var.github_oidc_provider_arn : aws_iam_openid_connect_provider.github[0].arn
}

# One per account; shared by any role trusting GitHub Actions. The first
# environment applied creates it, and the other passes its ARN in.
resource "aws_iam_openid_connect_provider" "github" {
  count          = var.github_oidc_provider_arn == null ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # The deploy job runs in the GitHub environment named after this one...
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["${var.github_subject_prefix}:environment:${var.environment}"]
    }

    # ...and only from the deploy workflow as it is on main, so a branch that
    # names the environment in its own workflow gets nothing.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:job_workflow_ref"
      values   = ["${var.github_repository}/.github/workflows/deploy.yml@refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = local.name
  assume_role_policy   = data.aws_iam_policy_document.assume.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "deploy" {
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "PushDashboardImage"
    actions = [
      "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:CompleteLayerUpload", "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload", "ecr:PutImage", "ecr:UploadLayerPart",
    ]
    resources = [var.ecr_repository_arn]
  }

  # Task definitions cannot be scoped by resource.
  statement {
    sid       = "TaskDefinitions"
    actions   = ["ecs:DescribeTaskDefinition", "ecs:RegisterTaskDefinition"]
    resources = ["*"]
  }

  dynamic "statement" {
    for_each = var.ecs_service_arn == null ? [] : [var.ecs_service_arn]
    content {
      sid       = "RollDashboardService"
      actions   = ["ecs:UpdateService", "ecs:DescribeServices"]
      resources = [statement.value]
    }
  }

  statement {
    sid       = "PassDashboardRoles"
    actions   = ["iam:PassRole"]
    resources = var.task_role_arns

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = local.name
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

output "role_arn" {
  value = aws_iam_role.deploy.arn
}

output "github_oidc_provider_arn" {
  value = local.oidc_provider_arn
}
