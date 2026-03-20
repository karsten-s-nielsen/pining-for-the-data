output "api_url" {
  description = "Base URL for the mock provider API"
  value       = aws_apigatewayv2_stage.v1.invoke_url
}

output "api_id" {
  description = "HTTP API ID"
  value       = aws_apigatewayv2_api.api.id
}
