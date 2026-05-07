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

variable "list_players_invoke_arn" {
  description = "Invoke ARN for the list_players Lambda"
  type        = string
}

variable "list_players_function_name" {
  description = "Function name for the list_players Lambda"
  type        = string
}

variable "get_player_invoke_arn" {
  description = "Invoke ARN for the get_player Lambda"
  type        = string
}

variable "get_player_function_name" {
  description = "Function name for the get_player Lambda"
  type        = string
}

variable "health_invoke_arn" {
  description = "Invoke ARN for the health Lambda"
  type        = string
}

variable "health_function_name" {
  description = "Function name for the health Lambda"
  type        = string
}
