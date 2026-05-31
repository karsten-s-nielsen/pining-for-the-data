# --- SNS Topic for Alarm Notifications ---

data "aws_region" "current" {}

resource "aws_sns_topic" "alarms" {
  name              = "${var.project_name}-alarms"
  kms_master_key_id = "alias/aws/sns"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# --- Lambda Error Alarms (one per function) ---

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = toset(var.lambda_function_names)

  alarm_name          = "${each.value}-errors"
  alarm_description   = "Lambda errors on ${each.value}"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}

# --- Lambda Duration P99 Alarm ---

resource "aws_cloudwatch_metric_alarm" "lambda_duration_p99" {
  for_each = toset(var.lambda_function_names)

  alarm_name          = "${each.value}-duration-p99"
  alarm_description   = "Lambda p99 duration > 8s on ${each.value} (timeout is 10s)"
  namespace           = "AWS/Lambda"
  metric_name         = "Duration"
  dimensions          = { FunctionName = each.value }
  extended_statistic  = "p99"
  period              = 300
  evaluation_periods  = 2
  threshold           = 8000
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

# --- Lambda Throttle Alarm ---

resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  for_each = toset(var.lambda_function_names)

  alarm_name          = "${each.value}-throttles"
  alarm_description   = "Lambda throttled on ${each.value}"
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

# --- API Gateway 5xx Alarm ---

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${var.project_name}-api-5xx"
  alarm_description   = "API Gateway 5xx errors"
  namespace           = "AWS/ApiGateway"
  metric_name         = "5xx"
  dimensions          = { ApiId = var.api_gateway_id }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}

# --- CloudWatch Dashboard ---

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = var.project_name

  dashboard_body = jsonencode({
    widgets = concat(
      [{
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "API Gateway Requests"
          region = data.aws_region.current.region
          metrics = [
            ["AWS/ApiGateway", "Count", "ApiId", var.api_gateway_id, { stat = "Sum", period = 300 }],
            ["AWS/ApiGateway", "5xx", "ApiId", var.api_gateway_id, { stat = "Sum", period = 300 }],
            ["AWS/ApiGateway", "4xx", "ApiId", var.api_gateway_id, { stat = "Sum", period = 300 }],
          ]
          view = "timeSeries"
        }
        },
        {
          type   = "metric"
          x      = 12
          y      = 0
          width  = 12
          height = 6
          properties = {
            title  = "API Gateway Latency"
            region = data.aws_region.current.region
            metrics = [
              ["AWS/ApiGateway", "Latency", "ApiId", var.api_gateway_id, { stat = "p99", period = 300 }],
              ["AWS/ApiGateway", "Latency", "ApiId", var.api_gateway_id, { stat = "p50", period = 300 }],
            ]
            view = "timeSeries"
          }
      }],
      [for i, fn in var.lambda_function_names : {
        type   = "metric"
        x      = (i % 2) * 12
        y      = 6 + floor(i / 2) * 6
        width  = 12
        height = 6
        properties = {
          title  = fn
          region = data.aws_region.current.region
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", fn, { stat = "Sum", period = 300 }],
            ["AWS/Lambda", "Errors", "FunctionName", fn, { stat = "Sum", period = 300 }],
            ["AWS/Lambda", "Duration", "FunctionName", fn, { stat = "p99", period = 300 }],
            ["AWS/Lambda", "Throttles", "FunctionName", fn, { stat = "Sum", period = 300 }],
          ]
          view = "timeSeries"
        }
      }]
    )
  })
}
