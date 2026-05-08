terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.44"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      ManagedBy = "Claude"
      Project   = var.project_name
    }
  }
}

module "storage" {
  source       = "../../modules/storage"
  project_name = var.project_name
}

resource "aws_ssm_parameter" "api_token_owner" {
  name        = "/${var.project_name}/api_token_owner"
  description = "Owner-tier bearer token for the mock provider API"
  type        = "SecureString"
  key_id      = module.storage.kms_key_arn
  value       = "placeholder-set-via-cli-after-first-apply"

  lifecycle {
    ignore_changes = [value]
  }
}

module "functions" {
  source                 = "../../modules/functions"
  project_name           = var.project_name
  bucket_name            = module.storage.bucket_name
  bucket_arn             = module.storage.bucket_arn
  kms_key_arn            = module.storage.kms_key_arn
  api_token              = var.api_token
  owner_token_param_arn  = aws_ssm_parameter.api_token_owner.arn
  owner_token_param_name = aws_ssm_parameter.api_token_owner.name
  last_rotation          = var.last_rotation
}

module "api" {
  source                       = "../../modules/api"
  project_name                 = var.project_name
  list_providers_invoke_arn    = module.functions.list_providers_invoke_arn
  list_matches_invoke_arn      = module.functions.list_matches_invoke_arn
  get_artifact_invoke_arn      = module.functions.get_artifact_invoke_arn
  list_providers_function_name = module.functions.list_providers_function_name
  list_matches_function_name   = module.functions.list_matches_function_name
  get_artifact_function_name   = module.functions.get_artifact_function_name
  list_players_invoke_arn      = module.functions.list_players_invoke_arn
  list_players_function_name   = module.functions.list_players_function_name
  get_player_invoke_arn        = module.functions.get_player_invoke_arn
  get_player_function_name     = module.functions.get_player_function_name
  health_invoke_arn            = module.functions.health_invoke_arn
  health_function_name         = module.functions.health_function_name
}

module "audit" {
  source                  = "../../modules/audit"
  project_name            = var.project_name
  data_bucket_arn         = module.storage.bucket_arn
  data_bucket_kms_key_arn = module.storage.kms_key_arn
}

# ── Module: GitHub OIDC ──────────────────────────────────────────────────────
# IAM role for secretless GitHub Actions CI.  References the existing OIDC
# identity provider (created by luxury-lakehouse) — one per AWS account.
# KMS key alias resolves the shared state bucket's encryption key.

data "aws_kms_alias" "state_bucket_key" {
  name = "alias/luxury-lakehouse-terraform-state-dev"
}

module "github_oidc" {
  source = "../../modules/github_oidc"

  project_name      = var.project_name
  github_repository = "karsten-s-nielsen/pining-for-the-data"
  state_bucket      = "karstenskyt-terraform-state"
  state_kms_key_arn = data.aws_kms_alias.state_bucket_key.target_key_arn
}

module "observability" {
  source         = "../../modules/observability"
  project_name   = var.project_name
  alarm_email    = var.alarm_email
  api_gateway_id = module.api.api_id
  lambda_function_names = [
    module.functions.list_providers_function_name,
    module.functions.list_matches_function_name,
    module.functions.get_artifact_function_name,
    module.functions.list_players_function_name,
    module.functions.get_player_function_name,
    module.functions.health_function_name,
  ]
}
