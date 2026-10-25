output "bucket_name" {
  value = module.core.bucket_name
}

output "collector_function_name" {
  value = module.daily_collector.function_name
}

output "alerts_topic_arn" {
  value = module.daily_collector.alerts_topic_arn
}

output "backfill_repository_url" {
  value = module.backfill.repository_url
}

output "monitor_repository_url" {
  value = module.monitor.repository_url
}

output "dashboard_repository_url" {
  value = module.dashboard.repository_url
}

output "dashboard_url" {
  value = module.dashboard.url
}

output "dashboard_load_balancer_dns_name" {
  value = module.dashboard.load_balancer_dns_name
}

output "dashboard_certificate_validation_records" {
  value = module.dashboard.certificate_validation_records
}

output "dashboard_snowflake_secret_name" {
  value = module.dashboard.snowflake_secret_name
}

output "ci_deploy_role_arn" {
  description = "Repository variable AWS_DEPLOY_ROLE_ARN."
  value       = module.ci_deploy.role_arn
}
