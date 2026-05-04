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
  description = "No-op marker bumped during owner-token rotation to invalidate Lambda warm-container cache (spec §3.5)"
  type        = string
  default     = "initial"
}
