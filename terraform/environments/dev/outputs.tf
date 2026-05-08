output "api_url" {
  description = "Base URL for the mock provider API"
  value       = module.api.api_url
}

output "bucket_name" {
  description = "S3 bucket for tracking data"
  value       = module.storage.bucket_name
}

output "owner_token_param_arn" {
  description = "ARN of the SSM parameter holding the owner-tier bearer token"
  value       = aws_ssm_parameter.api_token_owner.arn
}

output "owner_token_param_name" {
  description = "Name of the SSM parameter (used by Lambda env var)"
  value       = aws_ssm_parameter.api_token_owner.name
}

output "audit_bucket_name" {
  description = "Audit log bucket"
  value       = module.audit.audit_bucket_name
}

output "dashboard_url" {
  description = "CloudWatch dashboard URL"
  value       = module.observability.dashboard_url
}

output "alarm_topic_arn" {
  description = "SNS topic ARN for CloudWatch alarm notifications"
  value       = module.observability.alarm_topic_arn
}

output "github_actions_role_arn" {
  description = "IAM role ARN for GitHub Actions OIDC — set as AWS_OIDC_ROLE_ARN repo variable"
  value       = module.github_oidc.role_arn
}
