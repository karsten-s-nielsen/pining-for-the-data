variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
  default     = "pining-for-the-data"
}

variable "api_token" {
  description = "Bearer token for API authentication (public, documented)"
  type        = string
  sensitive   = true
}

variable "last_rotation" {
  description = "No-op marker bumped during owner- or LIST-token rotation to invalidate the Lambda warm-container token caches (spec §3.5)"
  type        = string
  default     = "initial"
}

variable "alarm_email" {
  description = "Email for CloudWatch alarm notifications (leave empty to skip)"
  type        = string
  default     = ""
}
