output "api_url" {
  description = "Base URL for the mock provider API"
  value       = module.api.api_url
}

output "bucket_name" {
  description = "S3 bucket for tracking data"
  value       = module.storage.bucket_name
}
