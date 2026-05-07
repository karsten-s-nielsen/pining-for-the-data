resource "aws_apigatewayv2_api" "api" {
  name          = "${var.project_name}-api"
  protocol_type = "HTTP"
  description   = "Mock tracking data provider API"

  cors_configuration {
    allow_origins = []
    allow_methods = ["GET"]
    allow_headers = ["Authorization", "Content-Type"]
    max_age       = 3600
  }
}

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${var.project_name}-api"
  retention_in_days = 30
}

resource "aws_apigatewayv2_stage" "v1" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "v1"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 50
    throttling_rate_limit  = 10
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    format = jsonencode({
      requestId          = "$context.requestId"
      ip                 = "$context.identity.sourceIp"
      requestTime        = "$context.requestTime"
      httpMethod         = "$context.httpMethod"
      routeKey           = "$context.routeKey"
      status             = "$context.status"
      protocol           = "$context.protocol"
      responseLength     = "$context.responseLength"
      integrationLatency = "$context.integrationLatency"
      responseLatency    = "$context.responseLatency"
    })
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
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/v1/GET/providers"
}

resource "aws_lambda_permission" "list_matches" {
  statement_id  = "AllowHTTPAPI"
  action        = "lambda:InvokeFunction"
  function_name = var.list_matches_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/v1/GET/*/matches"
}

resource "aws_lambda_permission" "get_artifact" {
  statement_id  = "AllowHTTPAPI"
  action        = "lambda:InvokeFunction"
  function_name = var.get_artifact_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/v1/GET/*/matches/*/*"
}

# --- /players resource (spec §6) ---

resource "aws_apigatewayv2_integration" "list_players" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.list_players_invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_integration" "get_player" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.get_player_invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "list_players" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "GET /{provider}/players"
  target    = "integrations/${aws_apigatewayv2_integration.list_players.id}"
}

resource "aws_apigatewayv2_route" "get_player" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "GET /{provider}/players/{id}"
  target    = "integrations/${aws_apigatewayv2_integration.get_player.id}"
}

resource "aws_lambda_permission" "list_players" {
  statement_id  = "AllowHTTPAPI"
  action        = "lambda:InvokeFunction"
  function_name = var.list_players_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/v1/GET/*/players"
}

resource "aws_lambda_permission" "get_player" {
  statement_id  = "AllowHTTPAPI"
  action        = "lambda:InvokeFunction"
  function_name = var.get_player_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/v1/GET/*/players/*"
}

# --- Health Check ---

resource "aws_apigatewayv2_integration" "health" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.health_invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "health" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "GET /health"
  target    = "integrations/${aws_apigatewayv2_integration.health.id}"
}

resource "aws_lambda_permission" "health" {
  statement_id  = "AllowHTTPAPI"
  action        = "lambda:InvokeFunction"
  function_name = var.health_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/v1/GET/health"
}
