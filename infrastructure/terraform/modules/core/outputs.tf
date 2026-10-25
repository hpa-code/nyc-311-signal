output "bucket_name" {
  value = aws_s3_bucket.observations.bucket
}

output "bucket_arn" {
  value = aws_s3_bucket.observations.arn
}

output "prefixes" {
  value = local.prefixes
}

output "ingestion_role_arn" {
  value = aws_iam_role.ingestion.arn
}

output "socrata_secret_name" {
  value = aws_secretsmanager_secret.socrata_app_token.name
}

output "ingestion_role_name" {
  value = aws_iam_role.ingestion.name
}

output "socrata_secret_arn" {
  value = aws_secretsmanager_secret.socrata_app_token.arn
}

output "snowflake_secret_arn" {
  value = aws_secretsmanager_secret.snowflake_service_user.arn
}

output "snowflake_secret_name" {
  value = aws_secretsmanager_secret.snowflake_service_user.name
}
