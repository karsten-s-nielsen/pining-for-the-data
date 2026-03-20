variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
}

variable "list_providers_invoke_arn" {
  description = "Invoke ARN for list_providers Lambda"
  type        = string
}

variable "list_matches_invoke_arn" {
  description = "Invoke ARN for list_matches Lambda"
  type        = string
}

variable "get_artifact_invoke_arn" {
  description = "Invoke ARN for get_artifact Lambda"
  type        = string
}

variable "list_providers_function_name" {
  description = "Function name for list_providers Lambda"
  type        = string
}

variable "list_matches_function_name" {
  description = "Function name for list_matches Lambda"
  type        = string
}

variable "get_artifact_function_name" {
  description = "Function name for get_artifact Lambda"
  type        = string
}
