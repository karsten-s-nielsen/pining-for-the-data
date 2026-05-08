# ──────────────────────────────────────────────────────────────────────────────
# Module: GitHub OIDC — Input Variables
# ──────────────────────────────────────────────────────────────────────────────

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "Project name used for IAM role naming and resource scoping"
  type        = string
}

variable "github_repository" {
  description = "GitHub repository in org/repo format for OIDC trust policy"
  type        = string
}

variable "state_bucket" {
  description = "Name of the S3 bucket holding Terraform remote state"
  type        = string
}

variable "state_kms_key_arn" {
  description = "ARN of the KMS key used for state bucket encryption"
  type        = string
}
