# Mock Provider API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the AWS mock provider API (Terraform + Lambda + upload CLI) so luxury-lakehouse adapters can test against realistic provider endpoints.

**Architecture:** Three Lambda handlers behind API Gateway, serving presigned S3 URLs. Provider-prefixed paths (`/v1/metrica/...`, `/v1/respovision/...`) mimic real provider APIs. Static bearer token auth. S3 with KMS encryption and versioning.

**Tech Stack:** Terraform (AWS provider), Python 3.12 (Lambda + boto3), API Gateway REST, S3, KMS, DynamoDB (state lock)

**Spec:** [`docs/superpowers/specs/2026-03-19-mock-provider-api-design.md`](../specs/2026-03-19-mock-provider-api-design.md)

**AWS access required:** Tasks 1-6 can be written and tested locally without AWS credentials. Tasks 7-8 require AWS access.

---

### Task 1: Shared Terraform Configuration

**Files:**
- Create: `terraform/shared/versions.tf`
- Create: `terraform/environments/dev/variables.tf`
- Create: `terraform/environments/dev/terraform.tfvars.example`

- [ ] **Step 1: Write provider version pins**

```hcl
# terraform/shared/versions.tf
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
```

- [ ] **Step 2: Write environment variables**

```hcl
# terraform/environments/dev/variables.tf
variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
  default     = "pining-for-the-data"
}

variable "api_token" {
  description = "Bearer token for API authentication (public, documented)"
  type        = string
  sensitive   = true
}
```

- [ ] **Step 3: Write tfvars example**

```hcl
# terraform/environments/dev/terraform.tfvars.example
aws_region   = "us-east-1"
project_name = "pining-for-the-data"
api_token    = "test-token-pining-for-the-data"
```

- [ ] **Step 4: Validate syntax**

Run: `cd terraform/environments/dev && terraform fmt -check -recursive ../../`
Expected: No formatting errors

- [ ] **Step 5: Commit**

```bash
git add terraform/shared/ terraform/environments/dev/variables.tf terraform/environments/dev/terraform.tfvars.example
git commit -m "feat(terraform): add shared versions and environment variables"
```

---

### Task 2: State Bootstrap Module

**Files:**
- Create: `terraform/modules/state/main.tf`
- Create: `terraform/modules/state/variables.tf`
- Create: `terraform/modules/state/outputs.tf`

- [ ] **Step 1: Write state module**

```hcl
# terraform/modules/state/variables.tf
variable "project_name" {
  type = string
}

variable "aws_region" {
  type = string
}
```

```hcl
# terraform/modules/state/main.tf
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
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "state" {
  bucket = "${var.project_name}-tfstate-${data.aws_caller_identity.current.account_id}"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "lock" {
  name         = "${var.project_name}-tflock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }
}
```

```hcl
# terraform/modules/state/outputs.tf
output "state_bucket" {
  value = aws_s3_bucket.state.id
}

output "lock_table" {
  value = aws_dynamodb_table.lock.name
}
```

- [ ] **Step 2: Validate syntax**

Run: `terraform -chdir=terraform/modules/state fmt -check && terraform -chdir=terraform/modules/state validate`

- [ ] **Step 3: Commit**

```bash
git add terraform/modules/state/
git commit -m "feat(terraform): add state bootstrap module (S3 + DynamoDB)"
```

---

### Task 3: Storage Module

**Files:**
- Create: `terraform/modules/storage/main.tf`
- Create: `terraform/modules/storage/variables.tf`
- Create: `terraform/modules/storage/outputs.tf`

- [ ] **Step 1: Write storage module**

```hcl
# terraform/modules/storage/variables.tf
variable "project_name" {
  type = string
}

variable "aws_region" {
  type = string
}
```

```hcl
# terraform/modules/storage/main.tf
data "aws_caller_identity" "current" {}

resource "aws_kms_key" "data" {
  description             = "KMS key for ${var.project_name} data bucket"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

resource "aws_kms_alias" "data" {
  name          = "alias/${var.project_name}-data"
  target_key_id = aws_kms_key.data.key_id
}

resource "aws_s3_bucket" "data" {
  bucket = "${var.project_name}-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
```

```hcl
# terraform/modules/storage/outputs.tf
output "bucket_name" {
  value = aws_s3_bucket.data.id
}

output "bucket_arn" {
  value = aws_s3_bucket.data.arn
}

output "kms_key_arn" {
  value = aws_kms_key.data.arn
}
```

- [ ] **Step 2: Validate syntax**

Run: `terraform -chdir=terraform/modules/storage fmt -check && terraform -chdir=terraform/modules/storage validate`

- [ ] **Step 3: Commit**

```bash
git add terraform/modules/storage/
git commit -m "feat(terraform): add storage module (S3 + KMS)"
```

---

### Task 4: Lambda Handlers

**Files:**
- Create: `terraform/modules/functions/src/shared.py`
- Create: `terraform/modules/functions/src/list_providers.py`
- Create: `terraform/modules/functions/src/list_matches.py`
- Create: `terraform/modules/functions/src/get_artifact.py`
- Create: `src/tests/test_lambda_handlers.py`

- [ ] **Step 1: Write shared auth helper**

```python
# terraform/modules/functions/src/shared.py
"""Shared utilities for Lambda handlers."""

from __future__ import annotations

import json
import os


def validate_token(event: dict) -> str | None:
    """Validate bearer token from Authorization header.

    Returns None if valid, or an error response body if invalid.
    """
    token = os.environ.get("API_TOKEN", "")
    headers = event.get("headers") or {}
    # API Gateway lowercases header names
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return _error_response(401, "Missing or malformed Authorization header")
    if auth[7:] != token:
        return _error_response(401, "Invalid token")
    return None


def json_response(status_code: int, body: dict) -> dict:
    """Build API Gateway proxy response."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }


def redirect_response(url: str) -> dict:
    """Build 302 redirect response."""
    return {
        "statusCode": 302,
        "headers": {
            "Location": url,
            "Access-Control-Allow-Origin": "*",
        },
        "body": "",
    }


def _error_response(status_code: int, message: str) -> dict:
    return json_response(status_code, {"error": message})
```

- [ ] **Step 2: Write list_providers handler**

```python
# terraform/modules/functions/src/list_providers.py
"""GET /v1/providers — list supported tracking data providers."""

from __future__ import annotations

import json
import os

import boto3

from shared import json_response, validate_token

s3 = boto3.client("s3")
BUCKET = os.environ.get("DATA_BUCKET", "")


def handler(event: dict, context: object) -> dict:
    auth_error = validate_token(event)
    if auth_error:
        return auth_error

    try:
        obj = s3.get_object(Bucket=BUCKET, Key="providers.json")
        providers = json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        providers = {"providers": []}

    return json_response(200, providers)
```

- [ ] **Step 3: Write list_matches handler**

```python
# terraform/modules/functions/src/list_matches.py
"""GET /v1/{provider}/matches — list available games for a provider."""

from __future__ import annotations

import json
import os

import boto3

from shared import json_response, validate_token

s3 = boto3.client("s3")
BUCKET = os.environ.get("DATA_BUCKET", "")


def handler(event: dict, context: object) -> dict:
    auth_error = validate_token(event)
    if auth_error:
        return auth_error

    provider = event.get("pathParameters", {}).get("provider", "")
    if not provider:
        return json_response(400, {"error": "Missing provider parameter"})

    key = f"{provider}/matches.json"
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        matches = json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        return json_response(404, {"error": f"Provider '{provider}' not found"})

    return json_response(200, matches)
```

- [ ] **Step 4: Write get_artifact handler**

```python
# terraform/modules/functions/src/get_artifact.py
"""GET /v1/{provider}/matches/{id}/{artifact} — serve a tracking artifact."""

from __future__ import annotations

import os

import boto3

from shared import json_response, redirect_response, validate_token

s3 = boto3.client("s3")
BUCKET = os.environ.get("DATA_BUCKET", "")
PRESIGNED_EXPIRY = int(os.environ.get("PRESIGNED_EXPIRY", "3600"))


def handler(event: dict, context: object) -> dict:
    auth_error = validate_token(event)
    if auth_error:
        return auth_error

    params = event.get("pathParameters") or {}
    provider = params.get("provider", "")
    match_id = params.get("id", "")
    artifact = params.get("artifact", "")

    if not all([provider, match_id, artifact]):
        return json_response(400, {"error": "Missing required path parameters"})

    # Scan for artifact file by prefix (artifact.txt, artifact.json, artifact.csv, etc.)
    prefix = f"{provider}/{match_id}/{artifact}"
    response = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix, MaxKeys=5)

    contents = response.get("Contents", [])
    # Filter to files that match artifact name exactly (not just prefix substring)
    matching = [
        obj["Key"] for obj in contents
        if obj["Key"].rsplit("/", 1)[-1].rsplit(".", 1)[0] == artifact
    ]

    if not matching:
        return json_response(404, {
            "error": "Artifact not found",
            "provider": provider,
            "match_id": match_id,
            "artifact": artifact,
        })

    key = matching[0]
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET, "Key": key},
        ExpiresIn=PRESIGNED_EXPIRY,
    )

    return redirect_response(url)
```

- [ ] **Step 5: Write tests for Lambda handlers**

```python
# src/tests/test_lambda_handlers.py
"""Tests for Lambda handler logic (no AWS needed — mocked S3)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add Lambda src to path so handlers can import shared
LAMBDA_SRC = Path(__file__).parent.parent.parent / "terraform" / "modules" / "functions" / "src"
sys.path.insert(0, str(LAMBDA_SRC))

import os

os.environ["API_TOKEN"] = "test-token"
os.environ["DATA_BUCKET"] = "test-bucket"


class TestValidateToken:
    def test_valid_token(self) -> None:
        from shared import validate_token
        event = {"headers": {"authorization": "Bearer test-token"}}
        assert validate_token(event) is None

    def test_missing_header(self) -> None:
        from shared import validate_token
        event = {"headers": {}}
        result = validate_token(event)
        assert result is not None
        assert result["statusCode"] == 401

    def test_wrong_token(self) -> None:
        from shared import validate_token
        event = {"headers": {"authorization": "Bearer wrong-token"}}
        result = validate_token(event)
        assert result is not None
        assert result["statusCode"] == 401

    def test_no_bearer_prefix(self) -> None:
        from shared import validate_token
        event = {"headers": {"authorization": "Basic abc123"}}
        result = validate_token(event)
        assert result is not None
        assert result["statusCode"] == 401


class TestListProviders:
    @patch("list_providers.s3")
    def test_returns_providers(self, mock_s3: MagicMock) -> None:
        from list_providers import handler
        body = json.dumps({"providers": ["metrica", "respovision"]}).encode()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body))}

        event = {"headers": {"authorization": "Bearer test-token"}}
        result = handler(event, None)
        assert result["statusCode"] == 200
        assert "metrica" in json.loads(result["body"])["providers"]

    def test_rejects_no_auth(self) -> None:
        from list_providers import handler
        result = handler({"headers": {}}, None)
        assert result["statusCode"] == 401


class TestListMatches:
    @patch("list_matches.s3")
    def test_returns_matches(self, mock_s3: MagicMock) -> None:
        from list_matches import handler
        body = json.dumps({"provider": "metrica", "matches": [{"id": "game_03"}]}).encode()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body))}

        event = {
            "headers": {"authorization": "Bearer test-token"},
            "pathParameters": {"provider": "metrica"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        assert json.loads(result["body"])["matches"][0]["id"] == "game_03"

    @patch("list_matches.s3")
    def test_unknown_provider_returns_404(self, mock_s3: MagicMock) -> None:
        from list_matches import handler
        from botocore.exceptions import ClientError
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey
        # Use a simpler approach: patch the exception class
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey()

        event = {
            "headers": {"authorization": "Bearer test-token"},
            "pathParameters": {"provider": "unknown"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 404


class TestGetArtifact:
    @patch("get_artifact.s3")
    def test_returns_redirect(self, mock_s3: MagicMock) -> None:
        from get_artifact import handler
        mock_s3.list_objects_v2.return_value = {
            "Contents": [{"Key": "metrica/game_03/tracking.txt"}]
        }
        mock_s3.generate_presigned_url.return_value = "https://s3.example.com/presigned"

        event = {
            "headers": {"authorization": "Bearer test-token"},
            "pathParameters": {"provider": "metrica", "id": "game_03", "artifact": "tracking"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 302
        assert result["headers"]["Location"] == "https://s3.example.com/presigned"

    @patch("get_artifact.s3")
    def test_artifact_not_found(self, mock_s3: MagicMock) -> None:
        from get_artifact import handler
        mock_s3.list_objects_v2.return_value = {"Contents": []}

        event = {
            "headers": {"authorization": "Bearer test-token"},
            "pathParameters": {"provider": "metrica", "id": "game_99", "artifact": "tracking"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 404

    @patch("get_artifact.s3")
    def test_filters_by_exact_artifact_name(self, mock_s3: MagicMock) -> None:
        from get_artifact import handler
        mock_s3.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "metrica/game_03/tracking.txt"},
                {"Key": "metrica/game_03/tracking_summary.json"},
            ]
        }
        mock_s3.generate_presigned_url.return_value = "https://s3.example.com/presigned"

        event = {
            "headers": {"authorization": "Bearer test-token"},
            "pathParameters": {"provider": "metrica", "id": "game_03", "artifact": "tracking"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 302
        # Should match tracking.txt, not tracking_summary.json
        mock_s3.generate_presigned_url.assert_called_once()
        call_args = mock_s3.generate_presigned_url.call_args
        assert call_args[1]["Params"]["Key"] == "metrica/game_03/tracking.txt"
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest src/tests/test_lambda_handlers.py -v`
Expected: All pass

- [ ] **Step 7: Lint Lambda source**

Run: `uv run ruff check terraform/modules/functions/src/`
Expected: Clean (may need per-file-ignores for boto3 import pattern)

- [ ] **Step 8: Commit**

```bash
git add terraform/modules/functions/src/ src/tests/test_lambda_handlers.py
git commit -m "feat: add Lambda handlers for mock provider API"
```

---

### Task 5: Functions and API Terraform Modules

**Files:**
- Create: `terraform/modules/functions/main.tf`
- Create: `terraform/modules/functions/variables.tf`
- Create: `terraform/modules/functions/outputs.tf`
- Create: `terraform/modules/api/main.tf`
- Create: `terraform/modules/api/variables.tf`
- Create: `terraform/modules/api/outputs.tf`

- [ ] **Step 1: Write functions module (Lambda + IAM)**

The functions module creates a zip archive from `src/`, deploys three Lambda functions, and creates an IAM role with S3 read + KMS decrypt permissions.

Key resources:
- `data "archive_file"` to zip `src/` directory
- `aws_iam_role` with `AssumeRole` for Lambda
- `aws_iam_role_policy` for S3 GetObject, ListBucket + KMS Decrypt
- Three `aws_lambda_function` resources sharing the same zip
- Variables: `bucket_arn`, `kms_key_arn`, `api_token`, `project_name`
- Outputs: `list_providers_arn`, `list_providers_invoke_arn`, same for each function

- [ ] **Step 2: Write API module (API Gateway + routes)**

The API module creates a REST API with these routes:
- `GET /v1/providers` -> `list_providers` Lambda
- `GET /v1/{provider}/matches` -> `list_matches` Lambda
- `GET /v1/{provider}/matches/{id}/{artifact}` -> `get_artifact` Lambda

Key resources:
- `aws_api_gateway_rest_api`
- `aws_api_gateway_resource` chain: `v1` -> `providers`, `{provider}` -> `matches` -> `{id}` -> `{artifact}`
- `aws_api_gateway_method` (GET) + `aws_api_gateway_integration` (AWS_PROXY) per endpoint
- `aws_api_gateway_deployment` + `aws_api_gateway_stage` ("v1")
- `aws_lambda_permission` for API Gateway invocation per function
- Variables: `project_name`, and invoke ARNs for each Lambda
- Outputs: `api_url`

- [ ] **Step 3: Validate both modules**

Run: `terraform -chdir=terraform/modules/functions fmt -check && terraform -chdir=terraform/modules/api fmt -check`

- [ ] **Step 4: Commit**

```bash
git add terraform/modules/functions/main.tf terraform/modules/functions/variables.tf terraform/modules/functions/outputs.tf
git add terraform/modules/api/main.tf terraform/modules/api/variables.tf terraform/modules/api/outputs.tf
git commit -m "feat(terraform): add functions and API Gateway modules"
```

---

### Task 6: Upload CLI + Environment Composition

**Files:**
- Create: `src/mock_api/upload.py`
- Create: `src/tests/test_upload.py`
- Modify: `pyproject.toml` (add `pining-upload` entry point + `aws` optional dep)
- Create: `terraform/environments/dev/main.tf`
- Create: `terraform/environments/dev/outputs.tf`
- Create: `terraform/environments/dev/backend.tf`

- [ ] **Step 1: Write upload CLI module**

`src/mock_api/upload.py` — uploads game artifacts to S3, updates `matches.json` and `providers.json` indexes.

Key functions:
- `upload_game(game_dir, provider, game_id, bucket)` — upload files, update indexes
- `_update_matches_json(s3, bucket, provider, game_id, artifacts)` — read-modify-write matches.json
- `_update_providers_json(s3, bucket, provider)` — add provider if new
- `main()` — argparse CLI entry point

- [ ] **Step 2: Write tests for upload CLI (mocked S3)**

Tests in `src/tests/test_upload.py`:
- `test_upload_detects_artifacts` — given a directory with tracking.txt + roster.json, detects both
- `test_update_matches_json_adds_game` — empty matches.json gets new game entry
- `test_update_matches_json_updates_existing` — re-upload replaces artifact list
- `test_update_providers_json_adds_new` — empty providers.json gets provider added

- [ ] **Step 3: Run tests**

Run: `uv run pytest src/tests/test_upload.py -v`
Expected: All pass

- [ ] **Step 4: Add entry point and optional dependency to pyproject.toml**

Add to `[project.optional-dependencies]`:
```toml
aws = [
    "boto3>=1.34.0",
]
```

Add to `[project.scripts]`:
```toml
pining-upload = "mock_api.upload:main"
```

- [ ] **Step 5: Write environment composition (main.tf)**

`terraform/environments/dev/main.tf` composes all modules:
```hcl
provider "aws" {
  region = var.aws_region
}

module "storage" {
  source       = "../../modules/storage"
  project_name = var.project_name
  aws_region   = var.aws_region
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
  source                        = "../../modules/api"
  project_name                  = var.project_name
  list_providers_invoke_arn     = module.functions.list_providers_invoke_arn
  list_matches_invoke_arn       = module.functions.list_matches_invoke_arn
  get_artifact_invoke_arn       = module.functions.get_artifact_invoke_arn
  list_providers_function_name  = module.functions.list_providers_function_name
  list_matches_function_name    = module.functions.list_matches_function_name
  get_artifact_function_name    = module.functions.get_artifact_function_name
}
```

- [ ] **Step 6: Write backend.tf**

```hcl
# terraform/environments/dev/backend.tf
# After running terraform/modules/state, fill in these values:
terraform {
  backend "s3" {
    bucket         = "REPLACE-WITH-STATE-BUCKET-NAME"
    key            = "pining-for-the-data/dev/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "REPLACE-WITH-LOCK-TABLE-NAME"
    encrypt        = true
  }
}
```

- [ ] **Step 7: Write outputs.tf**

```hcl
# terraform/environments/dev/outputs.tf
output "api_url" {
  description = "Base URL for the mock provider API"
  value       = module.api.api_url
}

output "bucket_name" {
  description = "S3 bucket for tracking data"
  value       = module.storage.bucket_name
}
```

- [ ] **Step 8: Lint and test everything**

Run: `uv run ruff check src/ && uv run pytest src/tests/ -v`
Expected: All clean, all tests pass

- [ ] **Step 9: Commit**

```bash
git add src/mock_api/upload.py src/tests/test_upload.py pyproject.toml
git add terraform/environments/dev/
git commit -m "feat: add upload CLI and Terraform environment composition"
```

---

### Task 7: Bootstrap Documentation

**Files:**
- Create: `terraform/docs/setup.md`
- Modify: `README.md` (add Mock API section)
- Modify: `CLAUDE.md` (add pining-upload entry point)

- [ ] **Step 1: Write setup.md**

Step-by-step guide covering:
1. Prerequisites (AWS CLI, Terraform, AWS account)
2. Bootstrap state infrastructure (`terraform/modules/state`)
3. Configure backend.tf with state bucket details
4. Configure terraform.tfvars
5. Deploy (`terraform init && terraform apply`)
6. Upload first game data (`pining-upload`)
7. Test the API (`curl` examples)
8. Forker instructions (what to change)

- [ ] **Step 2: Update README.md**

Add a "Mock Provider API" section between Distribution and Quick Start with the API endpoints table and a link to `terraform/docs/setup.md`.

- [ ] **Step 3: Update CLAUDE.md**

Add `pining-upload` to CLI Entry Points section.

- [ ] **Step 4: Commit**

```bash
git add terraform/docs/setup.md README.md CLAUDE.md
git commit -m "docs: add mock API setup guide and update README"
```

---

### Task 8: Deploy and Smoke Test (REQUIRES AWS ACCESS)

- [ ] **Step 1: Bootstrap state**

```bash
cd terraform/modules/state
terraform init
terraform apply -var="project_name=pining-for-the-data" -var="aws_region=us-east-1"
```

Note the `state_bucket` and `lock_table` outputs.

- [ ] **Step 2: Configure and deploy**

```bash
cd terraform/environments/dev
# Edit backend.tf with state bucket and lock table from step 1
# Copy terraform.tfvars.example to terraform.tfvars and fill in
terraform init
terraform apply
```

Note the `api_url` output.

- [ ] **Step 3: Upload test data**

```bash
# Use Metrica sample data or Game 03 data
pining-upload /path/to/game_03/ --provider metrica --game-id game_03
```

- [ ] **Step 4: Smoke test**

```bash
TOKEN="test-token-pining-for-the-data"
API="https://<api-url>"

# Discovery
curl -s -H "Authorization: Bearer $TOKEN" "$API/v1/providers" | python -m json.tool

# List matches
curl -s -H "Authorization: Bearer $TOKEN" "$API/v1/metrica/matches" | python -m json.tool

# Get artifact (should redirect)
curl -s -L -H "Authorization: Bearer $TOKEN" "$API/v1/metrica/matches/game_03/tracking" -o tracking.txt
ls -la tracking.txt

# Auth rejection
curl -s "$API/v1/providers"  # should return 401
```

- [ ] **Step 5: Commit any fixes from smoke test**

```bash
git add -A
git commit -m "fix: adjustments from smoke testing mock API"
```

---

### Task Summary

| Task | Description | AWS Required |
|------|-------------|-------------|
| 1 | Shared Terraform config | No |
| 2 | State bootstrap module | No |
| 3 | Storage module (S3 + KMS) | No |
| 4 | Lambda handlers + tests | No |
| 5 | Functions + API Gateway Terraform modules | No |
| 6 | Upload CLI + environment composition | No |
| 7 | Bootstrap documentation | No |
| 8 | Deploy and smoke test | **Yes** |
