# The public dashboard: one Fargate task (0.25 vCPU / 0.5 GB, ARM64) behind
# an internet-facing Application Load Balancer, in the default VPC's public
# subnets with a public IP and no NAT gateway. The task accepts traffic only
# from the load balancer, and reads Snowflake key-pair credentials (a user
# holding only the dashboard role) from Secrets Manager at run time; the
# browser only ever talks to Streamlit.
#
# Until image_tag is set, only the image repository exists: the load
# balancer and task cost money (instruction.md §16), so starting them is a
# deliberate apply. With domain_name set, the load balancer serves HTTPS with
# an ACM certificate (validated through DNS records you add where the domain
# is hosted) and redirects HTTP; without it, plain HTTP for a first check.

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

variable "image_tag" {
  description = "Commit SHA of the dashboard image to run; null until one is pushed."
  type        = string
  default     = null
}

variable "domain_name" {
  description = "Public name of the dashboard (e.g. nyc311.example.com); null serves plain HTTP."
  type        = string
  default     = null
}

variable "alerts_topic_arn" {
  type = string
}

locals {
  name    = "${var.environment}-nyc311-dashboard"
  enabled = var.image_tag != null
  https   = local.enabled && var.domain_name != null
  port    = 8501
}

data "aws_region" "current" {}

# Key-pair credentials of a Snowflake user that holds only the dashboard role
# ({"account", "user", "private_key"}); the value is put by hand, never by
# Terraform (README: Secret values).
resource "aws_secretsmanager_secret" "snowflake_dashboard_user" {
  name        = "${var.environment}/nyc311/snowflake-dashboard-user"
  description = "Snowflake key-pair credentials for the dashboard-only user (read-only APP views)."
}

resource "aws_ecr_repository" "dashboard" {
  name                 = local.name
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "dashboard" {
  repository = aws_ecr_repository.dashboard.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 5 images, so a rollback can redeploy a recent one"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 5 }
      action       = { type = "expire" }
    }]
  })
}

# ---------------------------------------------------------------------------
# Network: default VPC, public subnets, the task reachable only from the ALB
# ---------------------------------------------------------------------------

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

resource "aws_security_group" "alb" {
  count       = local.enabled ? 1 : 0
  name        = "${local.name}-alb"
  description = "Dashboard load balancer: HTTP and HTTPS from anywhere"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = local.port
    to_port     = local.port
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.default.cidr_block]
  }
}

resource "aws_security_group" "task" {
  count       = local.enabled ? 1 : 0
  name        = "${local.name}-task"
  description = "Dashboard task: Streamlit from the load balancer only; outbound to Snowflake, ECR, Secrets Manager, logs"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    from_port       = local.port
    to_port         = local.port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb[0].id]
  }

  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # The Snowflake connector checks certificate revocation (OCSP, CRL) over HTTP.
  egress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ---------------------------------------------------------------------------
# Load balancer
# ---------------------------------------------------------------------------

resource "aws_lb" "dashboard" {
  count              = local.enabled ? 1 : 0
  name               = local.name
  load_balancer_type = "application"
  internal           = false
  security_groups    = [aws_security_group.alb[0].id]
  # The minimum of two: each subnet adds a public IPv4 address, billed hourly.
  subnets = slice(sort(data.aws_subnets.public.ids), 0, 2)
  # Streamlit keeps a websocket open per browser tab.
  idle_timeout = 300
}

resource "aws_lb_target_group" "dashboard" {
  count       = local.enabled ? 1 : 0
  name        = local.name
  port        = local.port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = data.aws_vpc.default.id

  health_check {
    path                = "/_stcore/health"
    matcher             = "200"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  deregistration_delay = 30
}

resource "aws_acm_certificate" "dashboard" {
  count             = local.https ? 1 : 0
  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

# Waits until the validation records (output certificate_validation_records)
# are published at the domain's DNS host.
resource "aws_acm_certificate_validation" "dashboard" {
  count           = local.https ? 1 : 0
  certificate_arn = aws_acm_certificate.dashboard[0].arn
}

resource "aws_lb_listener" "https" {
  count             = local.https ? 1 : 0
  load_balancer_arn = aws_lb.dashboard[0].arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.dashboard[0].certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.dashboard[0].arn
  }
}

resource "aws_lb_listener" "http" {
  count             = local.enabled ? 1 : 0
  load_balancer_arn = aws_lb.dashboard[0].arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = local.https ? "redirect" : "forward"
    target_group_arn = local.https ? null : aws_lb_target_group.dashboard[0].arn

    dynamic "redirect" {
      for_each = local.https ? [1] : []
      content {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }
}

# ---------------------------------------------------------------------------
# Task and service
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "dashboard" {
  name              = "/ecs/${local.name}"
  retention_in_days = 30
}

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# ECS itself pulls the image and ships the logs.
resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# The app itself: read its Snowflake credentials, nothing else.
resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "task" {
  statement {
    sid       = "ReadSnowflakeCredentials"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.snowflake_dashboard_user.arn]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "${local.name}-task"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

resource "aws_ecs_cluster" "dashboard" {
  count = local.enabled ? 1 : 0
  name  = local.name
}

resource "aws_ecs_task_definition" "dashboard" {
  count                    = local.enabled ? 1 : 0
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([{
    name         = "dashboard"
    image        = "${aws_ecr_repository.dashboard.repository_url}:${var.image_tag}"
    essential    = true
    portMappings = [{ containerPort = local.port, protocol = "tcp" }]
    environment = [
      { name = "NYC311_ENV", value = var.environment },
      { name = "NYC311_SNOWFLAKE_SECRET_NAME", value = aws_secretsmanager_secret.snowflake_dashboard_user.name },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.dashboard.name
        awslogs-region        = data.aws_region.current.region
        awslogs-stream-prefix = "dashboard"
      }
    }
  }])
}

resource "aws_ecs_service" "dashboard" {
  count                             = local.enabled ? 1 : 0
  name                              = local.name
  cluster                           = aws_ecs_cluster.dashboard[0].id
  task_definition                   = aws_ecs_task_definition.dashboard[0].arn
  desired_count                     = 1
  launch_type                       = "FARGATE"
  health_check_grace_period_seconds = 60

  network_configuration {
    subnets          = slice(sort(data.aws_subnets.public.ids), 0, 2)
    security_groups  = [aws_security_group.task[0].id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.dashboard[0].arn
    container_name   = "dashboard"
    container_port   = local.port
  }

  # A deployment whose task never turns healthy rolls back to the last one.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # After the first apply, CI (.github/workflows/deploy.yml) registers new
  # task definition revisions; Terraform does not roll them back.
  lifecycle {
    ignore_changes = [task_definition]
  }

  depends_on = [aws_lb_listener.http]
}

# Application health (deferred from ticket 13): no healthy task behind the
# load balancer for 10 minutes.
resource "aws_cloudwatch_metric_alarm" "unhealthy" {
  count               = local.enabled ? 1 : 0
  alarm_name          = "${local.name}-unhealthy"
  alarm_description   = "No healthy dashboard task behind the load balancer. See docs/runbook.md."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HealthyHostCount"
  dimensions          = { LoadBalancer = aws_lb.dashboard[0].arn_suffix, TargetGroup = aws_lb_target_group.dashboard[0].arn_suffix }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [var.alerts_topic_arn]
}

output "repository_url" {
  value = aws_ecr_repository.dashboard.repository_url
}

output "repository_arn" {
  value = aws_ecr_repository.dashboard.arn
}

output "service_arn" {
  value = local.enabled ? aws_ecs_service.dashboard[0].id : null
}

output "task_role_arns" {
  description = "The task's execution and task roles, which CI passes when it registers a task definition."
  value       = [aws_iam_role.execution.arn, aws_iam_role.task.arn]
}

output "snowflake_secret_name" {
  value = aws_secretsmanager_secret.snowflake_dashboard_user.name
}

output "url" {
  value = local.enabled ? (local.https ? "https://${var.domain_name}" : "http://${aws_lb.dashboard[0].dns_name}") : null
}

output "load_balancer_dns_name" {
  description = "Point domain_name at this (CNAME, or an alias record)."
  value       = local.enabled ? aws_lb.dashboard[0].dns_name : null
}

output "certificate_validation_records" {
  description = "Add these CNAME records at the domain's DNS host so the certificate can be issued."
  value = local.https ? [
    for o in aws_acm_certificate.dashboard[0].domain_validation_options : { name = o.resource_record_name, type = o.resource_record_type, value = o.resource_record_value }
  ] : []
}
