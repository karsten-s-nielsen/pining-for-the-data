terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
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

module "functions" {
  source       = "../../modules/functions"
  project_name = var.project_name
  bucket_name  = module.storage.bucket_name
  bucket_arn   = module.storage.bucket_arn
  kms_key_arn  = module.storage.kms_key_arn
  api_token    = var.api_token
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
}
