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
