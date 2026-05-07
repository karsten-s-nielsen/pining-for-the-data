variable "project_name" {
  description = "Project name for resource naming"
  type        = string
}

variable "alarm_email" {
  description = "Email address for CloudWatch alarm notifications (empty = no email subscription)"
  type        = string
  default     = ""
}

variable "lambda_function_names" {
  description = "List of Lambda function names to monitor"
  type        = list(string)
}

variable "api_gateway_id" {
  description = "API Gateway ID for metrics"
  type        = string
}
