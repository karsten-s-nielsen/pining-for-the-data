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
