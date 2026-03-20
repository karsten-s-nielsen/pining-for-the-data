output "list_providers_invoke_arn" {
  description = "Invoke ARN for list_providers Lambda"
  value       = aws_lambda_function.list_providers.invoke_arn
}

output "list_matches_invoke_arn" {
  description = "Invoke ARN for list_matches Lambda"
  value       = aws_lambda_function.list_matches.invoke_arn
}

output "get_artifact_invoke_arn" {
  description = "Invoke ARN for get_artifact Lambda"
  value       = aws_lambda_function.get_artifact.invoke_arn
}

output "list_providers_function_name" {
  description = "Function name for list_providers Lambda"
  value       = aws_lambda_function.list_providers.function_name
}

output "list_matches_function_name" {
  description = "Function name for list_matches Lambda"
  value       = aws_lambda_function.list_matches.function_name
}

output "get_artifact_function_name" {
  description = "Function name for get_artifact Lambda"
  value       = aws_lambda_function.get_artifact.function_name
}
