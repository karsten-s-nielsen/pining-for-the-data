variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
}

variable "bucket_name" {
  description = "S3 bucket name for tracking data"
  type        = string
}

variable "bucket_arn" {
  description = "S3 bucket ARN for tracking data"
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key ARN for data encryption"
  type        = string
}

variable "api_token" {
  description = "Bearer token for API authentication"
  type        = string
  sensitive   = true
}
