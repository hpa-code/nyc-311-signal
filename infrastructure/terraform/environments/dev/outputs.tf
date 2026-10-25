output "bucket_name" {
  value = module.core.bucket_name
}

output "prefixes" {
  value = module.core.prefixes
}

output "ingestion_role_arn" {
  value = module.core.ingestion_role_arn
}

output "socrata_secret_arn" {
  value = module.core.socrata_secret_arn
}

output "snowflake_secret_arn" {
  value = module.core.snowflake_secret_arn
}

output "snowflake_read_role_arn" {
  value = module.snowflake_storage_access.role_arn
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

output "backfill_cluster_name" {
  value = module.backfill.cluster_name
}

output "backfill_task_definition" {
  value = module.backfill.task_definition
}

output "backfill_network_configuration" {
  value = module.backfill.network_configuration
}

output "monitor_repository_url" {
  value = module.monitor.repository_url
}
