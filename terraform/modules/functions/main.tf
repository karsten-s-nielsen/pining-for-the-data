data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/src"
  output_path = "${path.module}/lambda.zip"
}

# --- IAM ---

resource "aws_iam_role" "lambda" {
  name = "${var.project_name}-lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy" "lambda_s3" {
  name = "${var.project_name}-lambda-s3"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket",
        ]
        Resource = [
          var.bucket_arn,
          "${var.bucket_arn}/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = "kms:Decrypt"
        Resource = var.kms_key_arn
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# --- Lambda Functions ---

resource "aws_lambda_function" "list_providers" {
  function_name                  = "${var.project_name}-list-providers"
  role                           = aws_iam_role.lambda.arn
  handler                        = "list_providers.handler"
  runtime                        = "python3.12"
  memory_size                    = 128
  timeout                        = 10
  reserved_concurrent_executions = 5
  filename                       = data.archive_file.lambda_zip.output_path
  source_code_hash               = data.archive_file.lambda_zip.output_base64sha256

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      API_TOKEN   = var.api_token
      DATA_BUCKET = var.bucket_name
    }
  }
}

resource "aws_lambda_function" "list_matches" {
  function_name                  = "${var.project_name}-list-matches"
  role                           = aws_iam_role.lambda.arn
  handler                        = "list_matches.handler"
  runtime                        = "python3.12"
  memory_size                    = 128
  timeout                        = 10
  reserved_concurrent_executions = 5
  filename                       = data.archive_file.lambda_zip.output_path
  source_code_hash               = data.archive_file.lambda_zip.output_base64sha256

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      API_TOKEN   = var.api_token
      DATA_BUCKET = var.bucket_name
    }
  }
}

resource "aws_lambda_function" "get_artifact" {
  function_name                  = "${var.project_name}-get-artifact"
  role                           = aws_iam_role.lambda.arn
  handler                        = "get_artifact.handler"
  runtime                        = "python3.12"
  memory_size                    = 128
  timeout                        = 10
  reserved_concurrent_executions = 5
  filename                       = data.archive_file.lambda_zip.output_path
  source_code_hash               = data.archive_file.lambda_zip.output_base64sha256

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      API_TOKEN        = var.api_token
      DATA_BUCKET      = var.bucket_name
      PRESIGNED_EXPIRY = "3600"
    }
  }
}

# --- CloudWatch Log Groups ---

resource "aws_cloudwatch_log_group" "list_providers" {
  name              = "/aws/lambda/${aws_lambda_function.list_providers.function_name}"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "list_matches" {
  name              = "/aws/lambda/${aws_lambda_function.list_matches.function_name}"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "get_artifact" {
  name              = "/aws/lambda/${aws_lambda_function.get_artifact.function_name}"
  retention_in_days = 30
}

# --- X-Ray Tracing ---

resource "aws_iam_role_policy_attachment" "xray" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}
