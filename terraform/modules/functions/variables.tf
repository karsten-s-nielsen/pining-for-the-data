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

variable "owner_token_param_arn" {
  description = "ARN of the SSM parameter holding the owner-tier API token"
  type        = string
}

variable "owner_token_param_name" {
  description = "Name of the SSM parameter holding the owner-tier API token"
  type        = string
}

variable "last_rotation" {
  description = "No-op marker used to invalidate the warm-container _get_owner_token cache during a rotation. Bump on every owner-token rotation; spec §3.5."
  type        = string
  default     = "initial"
}
