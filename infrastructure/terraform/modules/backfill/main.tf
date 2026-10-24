# One-off historical backfill: the collector as a container image in ECR,
# run by hand as an ECS Fargate task (no service, no schedule). The task runs
# under the ingestion role, in a public subnet of the default VPC with a
# public IP and egress only, so it needs no NAT gateway.

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

variable "bucket_name" {
  type = string
}

variable "role_arn" {
  description = "Ingestion role (already trusted by ECS tasks, and allowed the bucket and secrets)."
  type        = string
}

variable "socrata_secret_name" {
  type = string
}

variable "image_tag" {
  description = "Commit SHA of the collector image to run. Tags are immutable, so a resumed run uses the same code."
  type        = string
}

variable "through" {
  description = "Last creation month to backfill, YYYY-MM. Fixed, so a rerun resumes the same run."
  type        = string
}

locals {
  name = "${var.environment}-nyc311-backfill"
}

resource "aws_ecr_repository" "collector" {
  name                 = "${var.environment}-nyc311-collector"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "collector" {
  repository = aws_ecr_repository.collector.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 3 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 3 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_ecs_cluster" "main" {
  name = "${var.environment}-nyc311"
}

resource "aws_cloudwatch_log_group" "backfill" {
  name              = "/ecs/${local.name}"
  retention_in_days = 30
}

# ECS itself pulls the image and ships the logs.
data "aws_iam_policy_document" "execution_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.execution_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_ecs_task_definition" "backfill" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = var.role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([{
    name      = "backfill"
    image     = "${aws_ecr_repository.collector.repository_url}:${var.image_tag}"
    essential = true
    command   = ["--through", var.through]
    environment = [
      { name = "NYC311_BUCKET", value = var.bucket_name },
      { name = "NYC311_SOCRATA_SECRET_NAME", value = var.socrata_secret_name },
      { name = "PYTHONUNBUFFERED", value = "1" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.backfill.name
        awslogs-region        = data.aws_region.current.region
        awslogs-stream-prefix = "backfill"
      }
    }
  }])
}

data "aws_region" "current" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "public" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }

  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

resource "aws_security_group" "backfill" {
  name        = local.name
  description = "Backfill task: outbound only (Socrata, S3, Secrets Manager)"
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

output "repository_url" {
  value = aws_ecr_repository.collector.repository_url
}

output "cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "task_definition" {
  value = aws_ecs_task_definition.backfill.family
}

# For `aws ecs run-task --network-configuration`.
output "network_configuration" {
  value = "awsvpcConfiguration={subnets=[${join(",", data.aws_subnets.public.ids)}],securityGroups=[${aws_security_group.backfill.id}],assignPublicIp=ENABLED}"
}
