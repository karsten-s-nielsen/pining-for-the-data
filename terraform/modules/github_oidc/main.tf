# ──────────────────────────────────────────────────────────────────────────────
# Module: GitHub OIDC — Secretless CI Authentication
# ──────────────────────────────────────────────────────────────────────────────
# Creates an IAM role scoped to a specific GitHub repository so CI can
# authenticate via short-lived OIDC tokens.  The OIDC identity provider
# already exists (created by the luxury-lakehouse repo); its ARN is constructed
# deterministically from the account id — one provider per AWS account.
# ──────────────────────────────────────────────────────────────────────────────

data "aws_caller_identity" "current" {}

# ── GitHub OIDC Provider ARN (constructed, not looked up) ─────────────────────
# We build the ARN from the account id rather than resolving it by URL via the
# `aws_iam_openid_connect_provider` data source. That lookup required
# `iam:ListOpenIDConnectProviders`, which created a bootstrap dead-end: a
# principal could not plan/apply this module without already holding that
# permission (so even fixing the CI role's permissions could not be applied by
# CI). The provider ARN form is stable and well-known, so constructing it
# removes the data-source read and the permission dependency entirely.

locals {
  github_oidc_provider_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
}

# ── IAM Role for GitHub Actions ──────────────────────────────────────────────

resource "aws_iam_role" "github_actions" {
  name = "${var.project_name}-github-actions-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = local.github_oidc_provider_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repository}:*"
        }
      }
    }]
  })
}

# ── Permissions: Terraform state access ───────────────────────────────────────
# S3 state bucket (shared with luxury-lakehouse, scoped to project key prefix),
# DynamoDB lock table, KMS for state encryption.

resource "aws_iam_role_policy" "terraform_state_access" {
  name = "terraform-state-access"
  role = aws_iam_role.github_actions.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3StateAccess"
        Effect = "Allow"
        Action = [
          "s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket",
          "s3:GetBucketVersioning", "s3:GetEncryptionConfiguration"
        ]
        Resource = [
          "arn:aws:s3:::${var.state_bucket}",
          "arn:aws:s3:::${var.state_bucket}/${var.project_name}/*"
        ]
      },
      {
        Sid    = "DynamoDBStateLock"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem",
          "dynamodb:DescribeTable"
        ]
        Resource = "arn:aws:dynamodb:*:${data.aws_caller_identity.current.account_id}:table/${var.project_name}-tflock"
      },
      {
        Sid    = "KMSStateEncryption"
        Effect = "Allow"
        Action = [
          "kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"
        ]
        Resource = [var.state_kms_key_arn]
      },
      {
        Sid      = "STSIdentity"
        Effect   = "Allow"
        Action   = ["sts:GetCallerIdentity"]
        Resource = ["*"]
      }
    ]
  })
}

# ── Permissions: Infrastructure management ────────────────────────────────────
# Lambda, API Gateway, IAM (project roles), S3 (data + audit buckets),
# KMS (data keys), CloudWatch, SNS, SSM, CloudTrail.

resource "aws_iam_role_policy" "infrastructure_management" {
  name = "infrastructure-management"
  role = aws_iam_role.github_actions.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # ── IAM: manage project Lambda roles + self ──────────────────────────
      {
        Sid    = "IAMProjectRoles"
        Effect = "Allow"
        Action = [
          "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:PassRole",
          "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy",
          "iam:AttachRolePolicy", "iam:DetachRolePolicy",
          "iam:ListRolePolicies", "iam:ListAttachedRolePolicies",
          "iam:ListInstanceProfilesForRole",
          "iam:TagRole", "iam:ListRoleTags",
          "iam:UpdateAssumeRolePolicy"
        ]
        Resource = [
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.project_name}-*"
        ]
      },
      {
        Sid      = "IAMReadOIDCProvider"
        Effect   = "Allow"
        Action   = ["iam:GetOpenIDConnectProvider"]
        Resource = [local.github_oidc_provider_arn]
      },
      # ── Lambda ───────────────────────────────────────────────────────────
      {
        Sid      = "LambdaManagement"
        Effect   = "Allow"
        Action   = ["lambda:*"]
        Resource = "arn:aws:lambda:*:${data.aws_caller_identity.current.account_id}:function:${var.project_name}-*"
      },
      # ── API Gateway v2 (no resource-level scoping for all actions) ──────
      {
        Sid      = "APIGateway"
        Effect   = "Allow"
        Action   = ["apigatewayv2:*"]
        Resource = ["*"]
      },
      # ── S3: data + audit buckets ─────────────────────────────────────────
      {
        Sid    = "S3DataBuckets"
        Effect = "Allow"
        Action = ["s3:*"]
        Resource = [
          "arn:aws:s3:::karstenskyt-${var.project_name}",
          "arn:aws:s3:::karstenskyt-${var.project_name}/*",
          "arn:aws:s3:::${var.project_name}-audit-*",
          "arn:aws:s3:::${var.project_name}-audit-*/*"
        ]
      },
      # ── KMS: data encryption keys ───────────────────────────────────────
      # Resource "*" required — kms:CreateKey has no resource-level scoping;
      # existing key ARNs are not known at policy-creation time.
      {
        Sid    = "KMSDataKeys"
        Effect = "Allow"
        Action = [
          "kms:CreateKey", "kms:DescribeKey", "kms:EnableKeyRotation",
          "kms:GetKeyRotationStatus", "kms:GetKeyPolicy",
          "kms:CreateAlias", "kms:DeleteAlias", "kms:UpdateAlias",
          "kms:ListAliases", "kms:ListResourceTags", "kms:TagResource",
          "kms:ScheduleKeyDeletion",
          "kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"
        ]
        Resource = ["*"]
      },
      # ── CloudWatch Logs ──────────────────────────────────────────────────
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = ["logs:*"]
        Resource = [
          "arn:aws:logs:*:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.project_name}-*",
          "arn:aws:logs:*:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.project_name}-*:*",
          "arn:aws:logs:*:${data.aws_caller_identity.current.account_id}:log-group:/aws/apigateway/${var.project_name}-*",
          "arn:aws:logs:*:${data.aws_caller_identity.current.account_id}:log-group:/aws/apigateway/${var.project_name}-*:*"
        ]
      },
      # ── CloudWatch Alarms + Dashboards ───────────────────────────────────
      {
        Sid    = "CloudWatchAlarmsAndDashboards"
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricAlarm", "cloudwatch:DeleteAlarms",
          "cloudwatch:DescribeAlarms", "cloudwatch:ListTagsForResource",
          "cloudwatch:TagResource",
          "cloudwatch:PutDashboard", "cloudwatch:DeleteDashboards",
          "cloudwatch:GetDashboard"
        ]
        Resource = ["*"]
      },
      # ── SNS ──────────────────────────────────────────────────────────────
      {
        Sid      = "SNS"
        Effect   = "Allow"
        Action   = ["sns:*"]
        Resource = "arn:aws:sns:*:${data.aws_caller_identity.current.account_id}:${var.project_name}-*"
      },
      # ── SSM Parameter Store ──────────────────────────────────────────────
      {
        Sid      = "SSM"
        Effect   = "Allow"
        Action   = ["ssm:*"]
        Resource = "arn:aws:ssm:*:${data.aws_caller_identity.current.account_id}:parameter/${var.project_name}/*"
      },
      # ── CloudTrail ──────────────────────────────────────────────────────
      {
        Sid      = "CloudTrail"
        Effect   = "Allow"
        Action   = ["cloudtrail:*"]
        Resource = "arn:aws:cloudtrail:*:${data.aws_caller_identity.current.account_id}:trail/${var.project_name}-*"
      }
    ]
  })
}

# ── Permissions: Terraform plan-refresh reads ─────────────────────────────────
# `terraform plan` refreshes existing state on every PR. Several of those reads
# are account-level list/describe actions that AWS only authorizes at
# Resource "*" (they have no per-resource ARN form), so the resource-scoped
# grants in `infrastructure_management` do not cover them. Kept as a separate,
# clearly-named inline policy so the read surface is auditable in isolation.
#
# (`iam:ListOpenIDConnectProviders` was previously required here for the
# `aws_iam_openid_connect_provider` data-source lookup; that data source has been
# replaced by a constructed ARN, so the action is no longer needed and is dropped.)
resource "aws_iam_role_policy" "plan_refresh_reads" {
  name = "terraform-plan-refresh-reads"
  role = aws_iam_role.github_actions.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "PlanRefreshReads"
        Effect = "Allow"
        Action = [
          "ssm:DescribeParameters",
          "logs:DescribeLogGroups",
          "cloudtrail:DescribeTrails",
        ]
        Resource = ["*"]
      },
      {
        Sid    = "APIGatewayRead"
        Effect = "Allow"
        Action = ["apigateway:GET"]
        Resource = [
          "arn:aws:apigateway:*::/apis",
          "arn:aws:apigateway:*::/apis/*",
        ]
      }
    ]
  })
}
