variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
}

variable "data_bucket_arn" {
  description = "ARN of the data bucket whose access events should be logged"
  type        = string
}

variable "data_bucket_kms_key_arn" {
  description = "KMS key ARN used by the data bucket (reused for log encryption)"
  type        = string
}

variable "log_retention_days" {
  description = "Days before audit log objects expire"
  type        = number
  default     = 365
}
