resource "aws_apigatewayv2_api" "api" {
  name          = "${var.project_name}-api"
  protocol_type = "HTTP"
  description   = "Mock tracking data provider API"
}

resource "aws_apigatewayv2_stage" "v1" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "v1"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 50
    throttling_rate_limit  = 10
  }
}

# --- Lambda Integrations ---

resource "aws_apigatewayv2_integration" "list_providers" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.list_providers_invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_integration" "list_matches" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.list_matches_invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_integration" "get_artifact" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.get_artifact_invoke_arn
  payload_format_version = "2.0"
}

# --- Routes ---

resource "aws_apigatewayv2_route" "list_providers" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "GET /providers"
  target    = "integrations/${aws_apigatewayv2_integration.list_providers.id}"
}

resource "aws_apigatewayv2_route" "list_matches" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "GET /{provider}/matches"
  target    = "integrations/${aws_apigatewayv2_integration.list_matches.id}"
}

resource "aws_apigatewayv2_route" "get_artifact" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "GET /{provider}/matches/{id}/{artifact}"
  target    = "integrations/${aws_apigatewayv2_integration.get_artifact.id}"
}

# --- Lambda Permissions ---

resource "aws_lambda_permission" "list_providers" {
  statement_id  = "AllowHTTPAPI"
  action        = "lambda:InvokeFunction"
  function_name = var.list_providers_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}

resource "aws_lambda_permission" "list_matches" {
  statement_id  = "AllowHTTPAPI"
  action        = "lambda:InvokeFunction"
  function_name = var.list_matches_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}

resource "aws_lambda_permission" "get_artifact" {
  statement_id  = "AllowHTTPAPI"
  action        = "lambda:InvokeFunction"
  function_name = var.get_artifact_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}
