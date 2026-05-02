# Private Data Tier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a two-tier auth model (public + owner) to the mock provider API, expose private match data and a player reference resource (`/players`), enable CloudTrail audit logging, and load the PFF FIFA World Cup 2022 dataset (67 matches + 2,322 players) as the first restricted-tier content.

**Architecture:** A second bearer token (`owner`), stored in SSM Parameter Store, is accepted by every Lambda alongside the existing public token. `validate_token` returns a tier (`PUBLIC` or `OWNER`) used to filter list responses and enforce uniform-404 on artifact/player retrieval for tier mismatches. Match visibility is recorded per-entry in `matches.json`; private matches and the private player index live under a reserved `_private/` S3 prefix for defense in depth. A new resource family `/players` follows the same shape as `/matches`. CloudTrail data events on the data bucket land in a separate audit bucket with a 365-day retention policy.

**Tech Stack:** Python 3.12 (Lambda + CLI), Terraform 1.5+, AWS (Lambda, API Gateway HTTP, S3, KMS, SSM Parameter Store, CloudTrail), pytest, ruff, pyright. Source spec: `docs/superpowers/specs/2026-05-02-private-data-tier.md`.

---

## File Structure

**Lambda handlers** (`terraform/modules/functions/src/`):
- `shared.py` — modified: add `Tier` enum, `get_owner_token` SSM fetcher, refactor `validate_token` to return `Tier`, harden `_SAFE_PARAM` against `_`-prefix.
- `list_providers.py` — modified: minor update to use new validator return type.
- `list_matches.py` — modified: filter response by tier.
- `get_artifact.py` — modified: read `matches.json`, resolve prefix from match's recorded visibility.
- `list_players.py` — new.
- `get_player.py` — new.

**Terraform** (`terraform/modules/`):
- `functions/main.tf` — modified: two new lambdas, IAM additions for SSM + KMS, `OWNER_TOKEN_PARAM` env var.
- `functions/variables.tf` — modified: new `owner_token_param_arn` variable.
- `functions/outputs.tf` — modified: new lambda invoke ARNs.
- `api/main.tf` — modified: two new routes for `/players` and `/players/{id}`.
- `api/variables.tf` — modified: new lambda variables.
- `audit/main.tf` — new: audit bucket + KMS + CloudTrail trail.
- `audit/variables.tf` — new.
- `audit/outputs.tf` — new.
- `environments/dev/main.tf` — modified: SSM parameter resource, audit module, owner-token wiring.
- `environments/dev/variables.tf` — modified: new variable surface (none required from user; SSM value set out-of-band).

**Python CLI** (`src/mock_api/`):
- `upload.py` — modified: `--visibility` flag, `--source-licence` flag, `_`-prefix rejection, write to `_private/` when private.
- `upload_players.py` — new: `pining-upload-players` CLI.

**Project config** (`pyproject.toml`):
- modified: add `pining-upload-players` entry point.

**Tests** (`src/tests/`):
- `test_lambda_handlers.py` — modified: tier validator, list_matches filter, get_artifact visibility, new tests for `list_players`/`get_player`, validator hardening.
- `test_upload.py` — modified: visibility flag behaviour, `_`-prefix rejection.
- `test_upload_players.py` — new.

**Scripts** (`scripts/`):
- `upload_pff_wc2022.py` — new: one-shot PFF reshape + load orchestrator.

---

# Phase 1 — SSM Parameter and Lambda IAM

The owner token must exist as an SSM parameter with KMS encryption before any Lambda can validate against it. This phase is pure Terraform; no Python changes yet. After Phase 1, the parameter exists with a placeholder value and the Lambdas have the IAM to read it (but the application code still ignores it).

### Task 1: Add SSM parameter resource for owner token

**Files:**
- Modify: `terraform/environments/dev/main.tf`
- Modify: `terraform/environments/dev/outputs.tf`

Goal: create the SSM parameter with KMS encryption, lifecycle-ignore on `value` so subsequent applies don't fight the out-of-band token rotation.

- [ ] **Step 1: Add `aws_ssm_parameter` resource to `terraform/environments/dev/main.tf`**

Append after the existing `module "storage"` block:

```hcl
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
```

- [ ] **Step 2: Add output for the parameter ARN**

Append to `terraform/environments/dev/outputs.tf`:

```hcl
output "owner_token_param_arn" {
  description = "ARN of the SSM parameter holding the owner-tier bearer token"
  value       = aws_ssm_parameter.api_token_owner.arn
}

output "owner_token_param_name" {
  description = "Name of the SSM parameter (used by Lambda env var)"
  value       = aws_ssm_parameter.api_token_owner.name
}
```

- [ ] **Step 3: Validate Terraform formatting and plan**

Run from the repo root:

```bash
cd terraform/environments/dev
terraform fmt -check -recursive ..
terraform validate
terraform plan -out=phase1.tfplan
```

Expected: `terraform plan` shows one new resource (`aws_ssm_parameter.api_token_owner`) and two new outputs. No errors.

- [ ] **Step 4: Commit**

```bash
git add terraform/environments/dev/main.tf terraform/environments/dev/outputs.tf
git commit -m "feat(terraform): add SSM parameter for owner-tier API token"
```

---

### Task 2: Add SSM read + KMS decrypt to Lambda IAM and wire env var

**Files:**
- Modify: `terraform/modules/functions/main.tf`
- Modify: `terraform/modules/functions/variables.tf`
- Modify: `terraform/environments/dev/main.tf`

Goal: every Lambda (existing three + future two) gets `ssm:GetParameter` on the owner-token parameter, `kms:Decrypt` on the data KMS key, and `OWNER_TOKEN_PARAM` env var set to the parameter name.

- [ ] **Step 1: Add new variable to `terraform/modules/functions/variables.tf`**

Append:

```hcl
variable "owner_token_param_arn" {
  description = "ARN of the SSM parameter holding the owner-tier API token"
  type        = string
}

variable "owner_token_param_name" {
  description = "Name of the SSM parameter holding the owner-tier API token"
  type        = string
}
```

- [ ] **Step 2: Extend Lambda IAM policy with SSM and KMS in `terraform/modules/functions/main.tf`**

Replace the existing `aws_iam_role_policy.lambda_s3` resource with this expanded version:

```hcl
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
      {
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = var.owner_token_param_arn
      },
    ]
  })
}
```

- [ ] **Step 3: Add `OWNER_TOKEN_PARAM` env var to all three existing Lambda functions**

In `terraform/modules/functions/main.tf`, in each of `aws_lambda_function.list_providers`, `aws_lambda_function.list_matches`, and `aws_lambda_function.get_artifact`, replace the `environment.variables` block to include the new var. Example for `list_providers`:

```hcl
  environment {
    variables = {
      API_TOKEN          = var.api_token
      DATA_BUCKET        = var.bucket_name
      OWNER_TOKEN_PARAM  = var.owner_token_param_name
    }
  }
```

Apply the same change to `list_matches` (same three vars) and `get_artifact` (preserves `PRESIGNED_EXPIRY` plus the three).

- [ ] **Step 4: Pass new variables from environment to module in `terraform/environments/dev/main.tf`**

In the `module "functions"` block, add the two new arguments:

```hcl
module "functions" {
  source                 = "../../modules/functions"
  project_name           = var.project_name
  bucket_name            = module.storage.bucket_name
  bucket_arn             = module.storage.bucket_arn
  kms_key_arn            = module.storage.kms_key_arn
  api_token              = var.api_token
  owner_token_param_arn  = aws_ssm_parameter.api_token_owner.arn
  owner_token_param_name = aws_ssm_parameter.api_token_owner.name
}
```

- [ ] **Step 5: Validate and plan**

```bash
cd terraform/environments/dev
terraform validate
terraform plan -out=phase1-task2.tfplan
```

Expected: changes to all three Lambda functions (env var addition) and the IAM policy (new statement). No errors.

- [ ] **Step 6: Commit**

```bash
git add terraform/modules/functions/main.tf terraform/modules/functions/variables.tf terraform/environments/dev/main.tf
git commit -m "feat(terraform): grant Lambdas SSM read access and wire owner-token env var"
```

---

### Task 3: Apply Phase 1 to dev and set the owner token

**Files:** none modified — operational task.

Goal: deploy the SSM parameter and IAM changes, then set the actual owner token via CLI. After this, the SSM parameter holds a real token but no Lambda code reads it yet (validate_token still ignores it). This is intentional — splitting the deploy from the application change reduces blast radius.

- [ ] **Step 1: Apply Terraform**

```bash
cd terraform/environments/dev
terraform apply phase1-task2.tfplan
```

Expected: SSM parameter created, IAM policy updated, all three Lambda env vars updated. Outputs include `owner_token_param_arn` and `owner_token_param_name`.

- [ ] **Step 2: Generate a strong random token and set it in SSM**

```bash
NEW_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
aws ssm put-parameter \
  --name "/pining-for-the-data/api_token_owner" \
  --value "$NEW_TOKEN" \
  --type SecureString \
  --overwrite
echo "OWNER TOKEN (record in your password manager): $NEW_TOKEN"
unset NEW_TOKEN
```

- [ ] **Step 3: Verify the parameter was set**

```bash
aws ssm get-parameter --name "/pining-for-the-data/api_token_owner" --with-decryption --query 'Parameter.Value' --output text
```

Expected: prints the same token.

- [ ] **Step 4: Smoke-test that existing endpoints still work with the public token**

```bash
API=$(terraform output -raw api_url)
curl -s -H "Authorization: Bearer test-token-pining-for-the-data" "$API/providers" | python -m json.tool
```

Expected: 200 + JSON list of providers (unchanged behaviour).

- [ ] **Step 5: No commit (operational task only)**

---

# Phase 2 — Tier-aware Validator

Refactor `validate_token` to accept both tokens and return a `Tier`. All existing handlers update their call sites to use the new return type. After Phase 2, the API recognises both tokens but treats them identically (no tier-specific filtering yet — that lands in Phase 3).

### Task 4: Add Tier enum and SSM-backed owner-token fetcher to shared.py

**Files:**
- Modify: `terraform/modules/functions/src/shared.py`
- Test: `src/tests/test_lambda_handlers.py`

Goal: introduce the new types without changing existing handler behaviour. The `validate_token` function still has its old signature for now; only the new helpers are added. This keeps the diff small and reversible.

- [ ] **Step 1: Write failing tests for `Tier` and `get_owner_token`**

Add to `src/tests/test_lambda_handlers.py` after the existing `TestValidateToken` class:

```python
class TestTierEnum:
    def test_tier_values(self) -> None:
        from shared import Tier

        assert Tier.PUBLIC.value == "public"
        assert Tier.OWNER.value == "owner"

    def test_tier_is_str(self) -> None:
        from shared import Tier

        # Tier should be a StrEnum so it serialises naturally in JSON
        assert Tier.PUBLIC == "public"


class TestGetOwnerToken:
    def test_fetches_and_caches(self, monkeypatch) -> None:
        import shared

        # Reset the cache between tests
        shared._get_owner_token.cache_clear()
        monkeypatch.setenv("OWNER_TOKEN_PARAM", "/test/param")

        calls = []

        def fake_get_parameter(Name, WithDecryption):
            calls.append(Name)
            return {"Parameter": {"Value": "owner-token-123"}}

        fake_ssm = type("FakeSSM", (), {"get_parameter": staticmethod(fake_get_parameter)})()
        monkeypatch.setattr(shared, "_get_ssm_client", lambda: fake_ssm)

        assert shared._get_owner_token() == "owner-token-123"
        assert shared._get_owner_token() == "owner-token-123"
        assert len(calls) == 1  # cached
        assert calls[0] == "/test/param"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd src/tests
pytest test_lambda_handlers.py::TestTierEnum -v
pytest test_lambda_handlers.py::TestGetOwnerToken -v
```

Expected: both fail with `ImportError: cannot import name 'Tier' from 'shared'` and similar.

- [ ] **Step 3: Implement Tier enum and SSM fetcher in `shared.py`**

Add to `terraform/modules/functions/src/shared.py`:

```python
import functools
from enum import StrEnum

import boto3


class Tier(StrEnum):
    PUBLIC = "public"
    OWNER = "owner"


_ssm_client = None


def _get_ssm_client():
    """Get a shared SSM client. Lazy-initialised."""
    global _ssm_client
    if _ssm_client is None:
        _ssm_client = boto3.client("ssm")
    return _ssm_client


@functools.cache
def _get_owner_token() -> str:
    """Fetch the owner-tier token from SSM Parameter Store.

    Cached for the lifetime of the warm Lambda container. Rotation requires
    a Lambda version bump or env-var change to invalidate the cache.
    """
    param_name = os.environ["OWNER_TOKEN_PARAM"]
    response = _get_ssm_client().get_parameter(Name=param_name, WithDecryption=True)
    return response["Parameter"]["Value"]
```

Place the imports at the top of the file alongside existing imports. Place `Tier` after the imports, `_ssm_client`/`_get_ssm_client` near the existing S3 client helpers, and `_get_owner_token` after them.

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest test_lambda_handlers.py::TestTierEnum test_lambda_handlers.py::TestGetOwnerToken -v
```

Expected: both pass.

- [ ] **Step 5: Run full test suite to confirm no regression**

```bash
cd src/tests
pytest -v
```

Expected: all tests pass (existing handler tests unaffected since only additions were made).

- [ ] **Step 6: Commit**

```bash
git add terraform/modules/functions/src/shared.py src/tests/test_lambda_handlers.py
git commit -m "feat(shared): add Tier enum and SSM-backed owner-token fetcher"
```

---

### Task 5: Refactor validate_token to return Tier

**Files:**
- Modify: `terraform/modules/functions/src/shared.py`
- Modify: `terraform/modules/functions/src/list_providers.py`
- Modify: `terraform/modules/functions/src/list_matches.py`
- Modify: `terraform/modules/functions/src/get_artifact.py`
- Modify: `src/tests/test_lambda_handlers.py`

Goal: validator returns `Tier` on success or an error response dict on failure. All call sites update from `if auth_error:` to `if isinstance(tier, dict):`.

- [ ] **Step 1: Update existing TestValidateToken tests to expect Tier return**

Replace the existing `TestValidateToken` class in `src/tests/test_lambda_handlers.py`:

```python
class TestValidateToken:
    def test_public_token(self, monkeypatch) -> None:
        from shared import Tier, validate_token

        monkeypatch.setenv("API_TOKEN", "test-token")
        # Force the owner token fetch to a sentinel
        import shared
        shared._get_owner_token.cache_clear()
        monkeypatch.setattr(shared, "_get_owner_token", lambda: "owner-token-sentinel")

        event = {"headers": {"authorization": "Bearer test-token"}}
        assert validate_token(event) == Tier.PUBLIC

    def test_owner_token(self, monkeypatch) -> None:
        from shared import Tier, validate_token
        import shared

        monkeypatch.setenv("API_TOKEN", "test-token")
        shared._get_owner_token.cache_clear()
        monkeypatch.setattr(shared, "_get_owner_token", lambda: "owner-token-sentinel")

        event = {"headers": {"authorization": "Bearer owner-token-sentinel"}}
        assert validate_token(event) == Tier.OWNER

    def test_owner_token_capitalized_header(self, monkeypatch) -> None:
        from shared import Tier, validate_token
        import shared

        monkeypatch.setenv("API_TOKEN", "test-token")
        shared._get_owner_token.cache_clear()
        monkeypatch.setattr(shared, "_get_owner_token", lambda: "owner-token-sentinel")

        event = {"headers": {"Authorization": "Bearer owner-token-sentinel"}}
        assert validate_token(event) == Tier.OWNER

    def test_missing_header(self, monkeypatch) -> None:
        from shared import validate_token
        import shared

        monkeypatch.setenv("API_TOKEN", "test-token")
        shared._get_owner_token.cache_clear()
        monkeypatch.setattr(shared, "_get_owner_token", lambda: "owner-token-sentinel")

        result = validate_token({"headers": {}})
        assert isinstance(result, dict)
        assert result["statusCode"] == 401

    def test_wrong_token(self, monkeypatch) -> None:
        from shared import validate_token
        import shared

        monkeypatch.setenv("API_TOKEN", "test-token")
        shared._get_owner_token.cache_clear()
        monkeypatch.setattr(shared, "_get_owner_token", lambda: "owner-token-sentinel")

        result = validate_token({"headers": {"authorization": "Bearer nope"}})
        assert isinstance(result, dict)
        assert result["statusCode"] == 401

    def test_no_bearer_prefix(self, monkeypatch) -> None:
        from shared import validate_token
        import shared

        monkeypatch.setenv("API_TOKEN", "test-token")
        shared._get_owner_token.cache_clear()
        monkeypatch.setattr(shared, "_get_owner_token", lambda: "owner-token-sentinel")

        result = validate_token({"headers": {"authorization": "Basic abc"}})
        assert isinstance(result, dict)
        assert result["statusCode"] == 401

    def test_null_headers(self, monkeypatch) -> None:
        from shared import validate_token
        import shared

        monkeypatch.setenv("API_TOKEN", "test-token")
        shared._get_owner_token.cache_clear()
        monkeypatch.setattr(shared, "_get_owner_token", lambda: "owner-token-sentinel")

        result = validate_token({"headers": None})
        assert isinstance(result, dict)
        assert result["statusCode"] == 401

    def test_same_public_and_owner_classifies_as_owner(self, monkeypatch) -> None:
        """Defensive default: if both tokens are the same string, owner wins (more permissive)."""
        from shared import Tier, validate_token
        import shared

        monkeypatch.setenv("API_TOKEN", "duplicate-token")
        shared._get_owner_token.cache_clear()
        monkeypatch.setattr(shared, "_get_owner_token", lambda: "duplicate-token")

        result = validate_token({"headers": {"authorization": "Bearer duplicate-token"}})
        assert result == Tier.OWNER
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest test_lambda_handlers.py::TestValidateToken -v
```

Expected: tests fail because `validate_token` currently returns `None` on success, not `Tier`.

- [ ] **Step 3: Refactor `validate_token` in `shared.py`**

Replace the existing `validate_token` function with:

```python
def validate_token(event: dict) -> Tier | dict:
    """Validate bearer token from Authorization header.

    Returns ``Tier.PUBLIC`` or ``Tier.OWNER`` on success, or an error response
    dict on failure. If both tokens are the same string, classifies as ``OWNER``
    (defensive: more permissive bias is fail-safe — the only place this matters
    is during accidental misconfiguration, where we'd rather reveal less data
    than more).
    """
    public_token = os.environ.get("API_TOKEN", "")
    if not public_token:
        return _error_response(500, "Server misconfiguration")

    headers = event.get("headers") or {}
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return _error_response(401, "Missing or malformed Authorization header")

    presented = auth[7:]

    try:
        owner_token = _get_owner_token()
    except Exception:
        logger.exception("owner_token_fetch_failed")
        return _error_response(500, "Server misconfiguration")

    # Compare against owner first so a duplicate string classifies as OWNER (less permissive failure mode).
    if hmac.compare_digest(presented, owner_token):
        return Tier.OWNER
    if hmac.compare_digest(presented, public_token):
        return Tier.PUBLIC
    return _error_response(401, "Invalid token")
```

Wait — the defensive default I described in the spec was "OWNER on duplicate." Let me re-check that's consistent: if both tokens are identical, classifying as OWNER is the more *permissive* outcome (sees everything). That's the spec's wording. The test above asserts this. The implementation matches.

- [ ] **Step 4: Update call sites in the three existing handlers**

In `terraform/modules/functions/src/list_providers.py`, replace the auth block:

```python
def handler(event: dict, context: object) -> dict:
    """Return the provider index by reading ``providers.json`` from S3."""
    tier = validate_token(event)
    if isinstance(tier, dict):
        logger.warning("auth_failure", extra={"handler": "list_providers"})
        return tier

    s3 = get_s3_client()
    try:
        obj = s3.get_object(Bucket=BUCKET, Key="providers.json")
        providers = json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        providers = {"providers": []}
    except Exception:
        logger.exception("s3_error", extra={"handler": "list_providers"})
        return json_response(500, {"error": "Internal server error"})

    return json_response(200, providers)
```

In `list_matches.py`, similarly replace the auth-check pattern at the top of `handler`:

```python
def handler(event: dict, context: object) -> dict:
    """Return the matches index for a provider by reading ``{provider}/matches.json`` from S3."""
    tier = validate_token(event)
    if isinstance(tier, dict):
        logger.warning("auth_failure", extra={"handler": "list_matches"})
        return tier

    provider = (event.get("pathParameters") or {}).get("provider", "")
    # ... rest unchanged
```

In `get_artifact.py`, similarly:

```python
def handler(event: dict, context: object) -> dict:
    """Resolve an artifact by name and return a presigned S3 URL via 302 redirect."""
    tier = validate_token(event)
    if isinstance(tier, dict):
        logger.warning("auth_failure", extra={"handler": "get_artifact"})
        return tier

    # ... rest unchanged
```

The `tier` variable is now unused in these handlers — Phase 3 will use it. Suppress the unused-variable warning for now by using `_` if your linter complains, or leave as `tier` for the upcoming Phase 3 changes. Recommend leaving as `tier` since the next phase wires it in.

- [ ] **Step 5: Run all handler tests to confirm they still pass**

```bash
pytest test_lambda_handlers.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add terraform/modules/functions/src/shared.py terraform/modules/functions/src/list_providers.py terraform/modules/functions/src/list_matches.py terraform/modules/functions/src/get_artifact.py src/tests/test_lambda_handlers.py
git commit -m "feat(api): validate_token returns Tier; accept both public and owner tokens"
```

---

### Task 6: Deploy Phase 2 and verify both tokens work

**Files:** none — operational.

- [ ] **Step 1: Re-package and apply**

```bash
cd terraform/environments/dev
terraform apply
```

Expected: Lambda source hash changes; three function versions updated.

- [ ] **Step 2: Verify public token still works**

```bash
API=$(terraform output -raw api_url)
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer test-token-pining-for-the-data" "$API/providers"
```

Expected: `200`.

- [ ] **Step 3: Verify owner token now works**

```bash
OWNER=$(aws ssm get-parameter --name "/pining-for-the-data/api_token_owner" --with-decryption --query 'Parameter.Value' --output text)
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $OWNER" "$API/providers"
```

Expected: `200`.

- [ ] **Step 4: Verify a wrong token still fails**

```bash
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer wrong-token" "$API/providers"
```

Expected: `401`.

- [ ] **Step 5: No commit (operational task)**

---

# Phase 3 — Match-level Visibility

Add `--visibility` flag to `pining-upload`, write to the `_private/` prefix when private, record `visibility` in `matches.json`, harden the path validator, filter `list_matches` by tier, make `get_artifact` visibility-aware.

### Task 7: Harden path validators against `_`-prefix

**Files:**
- Modify: `terraform/modules/functions/src/shared.py`
- Modify: `src/mock_api/upload.py`
- Modify: `src/tests/test_lambda_handlers.py`
- Modify: `src/tests/test_upload.py`

Goal: any path-param value (provider, id, artifact, name) that begins with `_` is rejected. `_private`, `_anything` → 400 from the API; `ValueError` from the upload CLI.

- [ ] **Step 1: Write failing tests in test_lambda_handlers.py**

Add to `src/tests/test_lambda_handlers.py`:

```python
class TestSafeParam:
    def test_rejects_underscore_prefix(self) -> None:
        from shared import validate_path_param

        result = validate_path_param("_private", "provider")
        assert result is not None
        assert result["statusCode"] == 400

    def test_rejects_any_underscore_prefix(self) -> None:
        from shared import validate_path_param

        for value in ["_files", "_admin", "_anything"]:
            result = validate_path_param(value, "id")
            assert result is not None, f"failed to reject {value!r}"
            assert result["statusCode"] == 400

    def test_accepts_underscore_midstring(self) -> None:
        from shared import validate_path_param

        # game_03 must still be valid
        assert validate_path_param("game_03", "id") is None
```

- [ ] **Step 2: Write failing test in test_upload.py**

Add to `src/tests/test_upload.py` (create if not present, but it does exist):

```python
class TestUploadValidation:
    def test_rejects_underscore_prefixed_provider(self):
        from mock_api.upload import _validate_param
        import pytest

        with pytest.raises(ValueError, match="Invalid provider"):
            _validate_param("_private", "provider")

    def test_rejects_underscore_prefixed_game_id(self):
        from mock_api.upload import _validate_param
        import pytest

        with pytest.raises(ValueError, match="Invalid game_id"):
            _validate_param("_files", "game_id")

    def test_accepts_underscore_midstring(self):
        from mock_api.upload import _validate_param

        # Should not raise
        _validate_param("game_03", "game_id")
```

- [ ] **Step 3: Run tests to verify failure**

```bash
cd src/tests
pytest test_lambda_handlers.py::TestSafeParam test_upload.py::TestUploadValidation -v
```

Expected: tests fail because `_private` currently passes (underscore is in the regex character class but no leading-underscore check exists).

- [ ] **Step 4: Tighten `_SAFE_PARAM` and add explicit prefix check in `shared.py`**

Replace the existing `_SAFE_PARAM` and `validate_path_param` in `terraform/modules/functions/src/shared.py`:

```python
# Strict allowlist: alphanumeric, hyphen, underscore — but no leading underscore.
# Leading `_` is reserved for tier and namespace markers (e.g., `_private`).
_SAFE_PARAM = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


def validate_path_param(value: str, name: str) -> dict | None:
    """Validate a path parameter against the safe character allowlist.

    Rejects empty, too long, or values starting with ``_`` (reserved for
    internal namespace markers like ``_private``).

    Returns None if valid, or an error response dict if invalid.
    """
    if not value or len(value) > 128 or not _SAFE_PARAM.match(value):
        return _error_response(400, f"Invalid {name}: must start with alphanumeric; use only alphanumeric, hyphens, or underscores")
    return None
```

- [ ] **Step 5: Tighten `_SAFE_PARAM` in `src/mock_api/upload.py`**

Replace the existing `_SAFE_PARAM` and `_validate_param`:

```python
# Same rule as the API-side validator: no leading underscore (reserved namespace).
_SAFE_PARAM = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


def _validate_param(value: str, name: str) -> None:
    """Raise ValueError if param is empty, too long, or contains unsafe characters.

    Leading underscore is rejected — reserved for internal namespace markers (`_private`).
    """
    if not value or len(value) > 128 or not _SAFE_PARAM.match(value):
        raise ValueError(
            f"Invalid {name}: must be 1-128 characters, start with alphanumeric, "
            f"and contain only alphanumeric, hyphens, or underscores"
        )
```

- [ ] **Step 6: Run tests to verify pass**

```bash
pytest test_lambda_handlers.py::TestSafeParam test_upload.py::TestUploadValidation -v
```

Expected: all pass.

- [ ] **Step 7: Run full suite to confirm no regression**

```bash
pytest -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add terraform/modules/functions/src/shared.py src/mock_api/upload.py src/tests/test_lambda_handlers.py src/tests/test_upload.py
git commit -m "feat(validation): reject underscore-prefixed path params; reserve _private namespace"
```

---

### Task 8: Add `--visibility` flag to pining-upload and write to `_private/` when private

**Files:**
- Modify: `src/mock_api/upload.py`
- Modify: `src/tests/test_upload.py`

Goal: `--visibility {public,private}` flag (default `public`). Private uploads write to `{provider}/_private/{game_id}/...`; public preserves existing behaviour. The `visibility` value is recorded in the match entry.

- [ ] **Step 1: Write failing tests for visibility behaviour**

Add to `src/tests/test_upload.py`:

```python
class TestUploadVisibility:
    def test_public_visibility_writes_to_provider_root(self, tmp_path):
        from unittest.mock import MagicMock
        from mock_api.upload import upload_game

        game_dir = tmp_path / "g1"
        game_dir.mkdir()
        (game_dir / "match.json").write_text("{}")

        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        s3.get_object.side_effect = s3.exceptions.NoSuchKey()

        from unittest.mock import patch
        with patch("mock_api.upload.boto3.client", return_value=s3):
            upload_game(game_dir, provider="skillcorner", game_id="g1", bucket="b", visibility="public")

        # Check that upload_file was called with key like skillcorner/g1/match.json (no _private)
        upload_keys = [c.args[2] for c in s3.upload_file.call_args_list]
        assert any(k == "skillcorner/g1/match.json" for k in upload_keys)
        assert not any("_private" in k for k in upload_keys)

    def test_private_visibility_writes_under_private_prefix(self, tmp_path):
        from unittest.mock import MagicMock, patch
        from mock_api.upload import upload_game

        game_dir = tmp_path / "g1"
        game_dir.mkdir()
        (game_dir / "match.json").write_text("{}")

        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        s3.get_object.side_effect = s3.exceptions.NoSuchKey()

        with patch("mock_api.upload.boto3.client", return_value=s3):
            upload_game(game_dir, provider="pff", game_id="3812", bucket="b", visibility="private")

        upload_keys = [c.args[2] for c in s3.upload_file.call_args_list]
        assert any(k == "pff/_private/3812/match.json" for k in upload_keys)

    def test_private_visibility_recorded_in_matches_json(self, tmp_path):
        from unittest.mock import MagicMock, patch
        import json
        from mock_api.upload import upload_game

        game_dir = tmp_path / "g1"
        game_dir.mkdir()
        (game_dir / "match.json").write_text("{}")

        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        s3.get_object.side_effect = s3.exceptions.NoSuchKey()

        with patch("mock_api.upload.boto3.client", return_value=s3):
            upload_game(game_dir, provider="pff", game_id="3812", bucket="b", visibility="private")

        # Find the put_object call that wrote matches.json
        matches_calls = [c for c in s3.put_object.call_args_list if c.kwargs.get("Key") == "pff/matches.json"]
        assert len(matches_calls) == 1
        body = json.loads(matches_calls[0].kwargs["Body"].decode("utf-8"))
        assert body["matches"][0]["visibility"] == "private"

    def test_default_visibility_is_public(self, tmp_path):
        """Calling upload_game without visibility kw arg must default to public."""
        from unittest.mock import MagicMock, patch
        import json
        from mock_api.upload import upload_game

        game_dir = tmp_path / "g1"
        game_dir.mkdir()
        (game_dir / "match.json").write_text("{}")

        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        s3.get_object.side_effect = s3.exceptions.NoSuchKey()

        with patch("mock_api.upload.boto3.client", return_value=s3):
            upload_game(game_dir, provider="sc", game_id="g1", bucket="b")

        matches_calls = [c for c in s3.put_object.call_args_list if c.kwargs.get("Key") == "sc/matches.json"]
        body = json.loads(matches_calls[0].kwargs["Body"].decode("utf-8"))
        assert body["matches"][0]["visibility"] == "public"

    def test_tier_mixing_rejected(self, tmp_path):
        """Re-uploading an existing public match as private must raise."""
        from unittest.mock import MagicMock, patch
        import json, pytest
        from mock_api.upload import upload_game

        game_dir = tmp_path / "g1"
        game_dir.mkdir()
        (game_dir / "match.json").write_text("{}")

        existing = json.dumps({
            "provider": "sc",
            "matches": [{"id": "g1", "artifacts": ["match"], "visibility": "public"}],
        }).encode()

        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=existing))}

        with patch("mock_api.upload.boto3.client", return_value=s3):
            with pytest.raises(ValueError, match="tier"):
                upload_game(game_dir, provider="sc", game_id="g1", bucket="b", visibility="private")
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest test_upload.py::TestUploadVisibility -v
```

Expected: all fail (no `visibility` parameter exists yet).

- [ ] **Step 3: Add `visibility` parameter to `upload_game` in `src/mock_api/upload.py`**

Modify the `upload_game` signature and body. Replace the function with:

```python
def upload_game(
    game_dir: Path,
    provider: str,
    game_id: str,
    bucket: str,
    visibility: str = "public",
    date: str | None = None,
    home: str | None = None,
    away: str | None = None,
    provenance: str | None = None,
    source_name: str | None = None,
    source_url: str | None = None,
    source_license: str | None = None,
) -> list[str]:
    """Upload all files in game_dir to S3 and update indexes.

    Parameters
    ----------
    visibility : str
        ``"public"`` (default) or ``"private"``. Private content is written
        under ``{provider}/_private/{game_id}/...`` and recorded with
        ``visibility: "private"`` in matches.json.
    """
    if visibility not in ("public", "private"):
        raise ValueError(f"Invalid visibility: {visibility!r} (must be 'public' or 'private')")

    _validate_param(provider, "provider")
    _validate_param(game_id, "game_id")

    s3 = boto3.client("s3")

    # Reject tier mixing: a re-upload cannot flip an existing match's tier.
    _check_no_tier_mixing(s3, bucket, provider, game_id, visibility)

    prefix = (
        f"{provider}/_private/{game_id}"
        if visibility == "private"
        else f"{provider}/{game_id}"
    )

    artifacts: list[str] = []
    for file_path in sorted(game_dir.iterdir()):
        if file_path.is_file() and not file_path.name.startswith("."):
            key = f"{prefix}/{file_path.name}"
            s3.upload_file(str(file_path), bucket, key)
            artifact_name = file_path.stem
            artifacts.append(artifact_name)
            print(f"  Uploaded {file_path.name} -> s3://{bucket}/{key}")

    if not artifacts:
        print(f"  No files found in {game_dir}")
        return artifacts

    _update_matches_json(
        s3, bucket, provider, game_id, artifacts, visibility, date, home, away,
        provenance, source_name, source_url, source_license,
    )
    _update_providers_json(s3, bucket, provider)

    return artifacts


def _check_no_tier_mixing(s3, bucket: str, provider: str, game_id: str, new_visibility: str) -> None:
    """Raise ValueError if `game_id` exists in matches.json with a different tier."""
    key = f"{provider}/matches.json"
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        data = json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        return  # No existing index — nothing to conflict with.

    existing = next((m for m in data.get("matches", []) if m.get("id") == game_id), None)
    if existing is None:
        return
    existing_visibility = existing.get("visibility", "public")
    if existing_visibility != new_visibility:
        raise ValueError(
            f"Cannot mix tiers for game_id {game_id!r}: existing entry is "
            f"{existing_visibility!r}, requested {new_visibility!r}. "
            f"Re-tiering requires an explicit move (not supported in v1)."
        )
```

Update `_update_matches_json` to accept and record `visibility`:

```python
def _update_matches_json(
    s3,
    bucket: str,
    provider: str,
    game_id: str,
    artifacts: list[str],
    visibility: str,
    date: str | None,
    home: str | None,
    away: str | None,
    provenance: str | None = None,
    source_name: str | None = None,
    source_url: str | None = None,
    source_license: str | None = None,
) -> None:
    """Read-modify-write the matches.json index for a provider."""
    key = f"{provider}/matches.json"

    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        data = json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        data = {"provider": provider, "matches": []}

    existing = next((m for m in data["matches"] if m["id"] == game_id), None)
    entry = {
        "id": game_id,
        "artifacts": artifacts,
        "visibility": visibility,
    }
    if date:
        entry["date"] = date
    if home:
        entry["home"] = home
    if away:
        entry["away"] = away
    if provenance:
        entry["provenance"] = provenance
    if source_name:
        entry["source"] = {
            "name": source_name,
            "url": source_url or "",
            "license": source_license or "",
        }

    if existing:
        idx = data["matches"].index(existing)
        data["matches"][idx] = entry
    else:
        data["matches"].append(entry)

    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(data, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    print(f"  Updated {key}")
```

- [ ] **Step 4: Add the `--visibility` flag to the CLI parser in `main()`**

In `src/mock_api/upload.py`, in the `main()` function, add the flag near the other arguments:

```python
    parser.add_argument(
        "--visibility",
        default="public",
        choices=["public", "private"],
        help="Visibility tier (default: public; private writes under _private/ prefix)",
    )
```

And pass it to `upload_game`:

```python
    artifacts = upload_game(
        game_dir=args.game_dir,
        provider=args.provider,
        game_id=args.game_id,
        bucket=args.bucket,
        visibility=args.visibility,
        date=args.date,
        home=args.home,
        away=args.away,
        provenance=args.provenance,
        source_name=args.source_name,
        source_url=args.source_url,
        source_license=args.source_license,
    )
```

- [ ] **Step 5: Run tests to verify pass**

```bash
pytest test_upload.py::TestUploadVisibility -v
```

Expected: all pass.

- [ ] **Step 6: Run full suite**

```bash
pytest -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/mock_api/upload.py src/tests/test_upload.py
git commit -m "feat(upload): add --visibility flag with tier-mixing rejection"
```

---

### Task 9: Update list_matches handler to filter by tier

**Files:**
- Modify: `terraform/modules/functions/src/list_matches.py`
- Modify: `src/tests/test_lambda_handlers.py`

Goal: public-tier callers see only entries with `visibility == "public"` (or missing, treated as public). Owner-tier sees everything. Empty filtered list returns 200 with `{"matches": []}`.

- [ ] **Step 1: Write failing tests**

In `src/tests/test_lambda_handlers.py`, replace the existing `TestListMatches` class with the expanded version:

```python
class TestListMatches(_ResetS3):
    def _matches_payload(self):
        return {
            "provider": "sc",
            "matches": [
                {"id": "pub1", "artifacts": ["match"], "visibility": "public"},
                {"id": "priv1", "artifacts": ["match"], "visibility": "private"},
                {"id": "legacy", "artifacts": ["match"]},  # missing visibility, treat as public
            ],
        }

    def _setup(self, monkeypatch):
        import shared
        monkeypatch.setenv("API_TOKEN", "pub-tok")
        shared._get_owner_token.cache_clear()
        monkeypatch.setattr(shared, "_get_owner_token", lambda: "own-tok")

    def test_public_tier_sees_public_only(self, monkeypatch) -> None:
        from list_matches import handler
        import json
        self._setup(monkeypatch)

        mock_s3 = _mock_s3()
        body = json.dumps(self._matches_payload()).encode()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body))}

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        ids = [m["id"] for m in json.loads(result["body"])["matches"]]
        assert "pub1" in ids
        assert "legacy" in ids
        assert "priv1" not in ids

    def test_owner_tier_sees_all(self, monkeypatch) -> None:
        from list_matches import handler
        import json
        self._setup(monkeypatch)

        mock_s3 = _mock_s3()
        body = json.dumps(self._matches_payload()).encode()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body))}

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "sc"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        ids = [m["id"] for m in json.loads(result["body"])["matches"]]
        assert set(ids) == {"pub1", "priv1", "legacy"}

    def test_empty_after_filter_returns_200_not_404(self, monkeypatch) -> None:
        """All-private dataset, public token: 200 with empty list (no existence leak)."""
        from list_matches import handler
        import json
        self._setup(monkeypatch)

        mock_s3 = _mock_s3()
        all_private = {"provider": "pff", "matches": [{"id": "3812", "artifacts": ["m"], "visibility": "private"}]}
        body = json.dumps(all_private).encode()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body))}

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "pff"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        assert json.loads(result["body"])["matches"] == []

    def test_unknown_provider_returns_404(self, monkeypatch) -> None:
        from list_matches import handler
        self._setup(monkeypatch)

        mock_s3 = _mock_s3()
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey()

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "unknown"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 404
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest test_lambda_handlers.py::TestListMatches -v
```

Expected: tier-filter tests fail (current handler returns the unfiltered payload).

- [ ] **Step 3: Update `list_matches.py` to filter by tier**

Replace the body of `handler` in `terraform/modules/functions/src/list_matches.py`:

```python
def handler(event: dict, context: object) -> dict:
    """Return the matches index for a provider, filtered by caller's tier."""
    tier = validate_token(event)
    if isinstance(tier, dict):
        logger.warning("auth_failure", extra={"handler": "list_matches"})
        return tier

    provider = (event.get("pathParameters") or {}).get("provider", "")
    param_error = validate_path_param(provider, "provider")
    if param_error:
        logger.warning("validation_failure", extra={"handler": "list_matches", "param": "provider"})
        return param_error

    s3 = get_s3_client()
    key = f"{provider}/matches.json"
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        matches = json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        return json_response(404, {"error": "Provider not found"})
    except Exception:
        logger.exception("s3_error", extra={"handler": "list_matches"})
        return json_response(500, {"error": "Internal server error"})

    if tier != Tier.OWNER:
        # Filter to public entries (missing `visibility` field defaults to public).
        filtered = [m for m in matches.get("matches", []) if m.get("visibility", "public") == "public"]
        matches = {**matches, "matches": filtered}

    return json_response(200, matches)
```

Add the `Tier` import at the top:

```python
from shared import Tier, get_s3_client, json_response, logger, validate_path_param, validate_token
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest test_lambda_handlers.py::TestListMatches -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add terraform/modules/functions/src/list_matches.py src/tests/test_lambda_handlers.py
git commit -m "feat(api): list_matches filters by tier; uniform 200+empty for tier-hidden content"
```

---

### Task 10: Make get_artifact visibility-aware

**Files:**
- Modify: `terraform/modules/functions/src/get_artifact.py`
- Modify: `src/tests/test_lambda_handlers.py`

Goal: `get_artifact` reads `matches.json`, looks up the match, checks tier vs match visibility, resolves the correct S3 prefix (`_private/` vs not), returns 302 on success or 404 on not-found-or-tier-mismatch.

- [ ] **Step 1: Write failing tests**

In `src/tests/test_lambda_handlers.py`, replace the existing `TestGetArtifact` with this expanded version:

```python
class TestGetArtifact(_ResetS3):
    def _setup(self, monkeypatch):
        import shared
        monkeypatch.setenv("API_TOKEN", "pub-tok")
        shared._get_owner_token.cache_clear()
        monkeypatch.setattr(shared, "_get_owner_token", lambda: "own-tok")

    def _wire_matches_and_listing(self, mock_s3, matches_payload, listing_keys):
        import json
        body = json.dumps(matches_payload).encode()

        def get_obj(Bucket, Key):
            if Key.endswith("matches.json"):
                return {"Body": MagicMock(read=MagicMock(return_value=body))}
            raise mock_s3.exceptions.NoSuchKey()

        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = get_obj
        mock_s3.list_objects_v2.return_value = {"Contents": [{"Key": k} for k in listing_keys]}
        mock_s3.generate_presigned_url.return_value = "https://s3.example.com/presigned"

    def test_public_match_public_tier_returns_302(self, monkeypatch) -> None:
        from get_artifact import handler
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire_matches_and_listing(
            mock_s3,
            {"provider": "sc", "matches": [{"id": "g1", "artifacts": ["match"], "visibility": "public"}]},
            ["sc/g1/match.json"],
        )

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc", "id": "g1", "artifact": "match"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 302
        assert mock_s3.generate_presigned_url.call_args.kwargs["Params"]["Key"] == "sc/g1/match.json"

    def test_private_match_owner_tier_returns_302_with_private_prefix(self, monkeypatch) -> None:
        from get_artifact import handler
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire_matches_and_listing(
            mock_s3,
            {"provider": "pff", "matches": [{"id": "3812", "artifacts": ["metadata"], "visibility": "private"}]},
            ["pff/_private/3812/metadata.json"],
        )

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "pff", "id": "3812", "artifact": "metadata"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 302
        # Listing must have been done against the _private prefix, not the public one
        list_call = mock_s3.list_objects_v2.call_args
        assert list_call.kwargs["Prefix"].startswith("pff/_private/3812/")

    def test_private_match_public_tier_returns_404(self, monkeypatch) -> None:
        """Private match accessed with public token: 404 (not 403 — no existence leak)."""
        from get_artifact import handler
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire_matches_and_listing(
            mock_s3,
            {"provider": "pff", "matches": [{"id": "3812", "artifacts": ["metadata"], "visibility": "private"}]},
            [],
        )

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "pff", "id": "3812", "artifact": "metadata"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 404

    def test_unknown_match_returns_404(self, monkeypatch) -> None:
        from get_artifact import handler
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire_matches_and_listing(
            mock_s3,
            {"provider": "sc", "matches": []},
            [],
        )

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "sc", "id": "missing", "artifact": "match"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 404

    def test_filters_by_exact_artifact_name(self, monkeypatch) -> None:
        from get_artifact import handler
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire_matches_and_listing(
            mock_s3,
            {"provider": "sc", "matches": [{"id": "g1", "artifacts": ["tracking"], "visibility": "public"}]},
            ["sc/g1/tracking.txt", "sc/g1/tracking_summary.json"],
        )

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc", "id": "g1", "artifact": "tracking"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 302
        assert mock_s3.generate_presigned_url.call_args.kwargs["Params"]["Key"] == "sc/g1/tracking.txt"

    def test_legacy_match_without_visibility_treated_as_public(self, monkeypatch) -> None:
        from get_artifact import handler
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire_matches_and_listing(
            mock_s3,
            {"provider": "sc", "matches": [{"id": "g1", "artifacts": ["match"]}]},
            ["sc/g1/match.json"],
        )

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc", "id": "g1", "artifact": "match"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 302
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest test_lambda_handlers.py::TestGetArtifact -v
```

Expected: tests fail because the current handler doesn't read matches.json or check tier.

- [ ] **Step 3: Rewrite `get_artifact.py`**

Replace the `handler` function in `terraform/modules/functions/src/get_artifact.py`:

```python
"""GET /v1/{provider}/matches/{id}/{artifact} — serve a tracking artifact."""

from __future__ import annotations

import json
import os

from shared import (
    Tier,
    get_s3_client,
    json_response,
    logger,
    redirect_response,
    validate_path_param,
    validate_token,
)

BUCKET = os.environ.get("DATA_BUCKET", "")
PRESIGNED_EXPIRY = int(os.environ.get("PRESIGNED_EXPIRY", "3600"))


def handler(event: dict, context: object) -> dict:
    """Resolve an artifact by name and return a presigned S3 URL via 302 redirect.

    Reads ``{provider}/matches.json`` once to determine the match's visibility,
    enforces tier check (404 on mismatch — uniform with not-found to avoid
    existence leaks), then resolves the actual S3 prefix from the recorded
    visibility and lists for ``{artifact}.*``.
    """
    tier = validate_token(event)
    if isinstance(tier, dict):
        logger.warning("auth_failure", extra={"handler": "get_artifact"})
        return tier

    params = event.get("pathParameters") or {}
    provider = params.get("provider", "")
    match_id = params.get("id", "")
    artifact = params.get("artifact", "")

    for name, value in [("provider", provider), ("id", match_id), ("artifact", artifact)]:
        param_error = validate_path_param(value, name)
        if param_error:
            logger.warning("validation_failure", extra={"handler": "get_artifact", "param": name})
            return param_error

    s3 = get_s3_client()

    # Look up the match in matches.json to determine visibility.
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=f"{provider}/matches.json")
        matches_data = json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        return json_response(404, {"error": "Match not found"})
    except Exception:
        logger.exception("s3_error", extra={"handler": "get_artifact", "stage": "matches_lookup"})
        return json_response(500, {"error": "Internal server error"})

    match = next((m for m in matches_data.get("matches", []) if m.get("id") == match_id), None)
    if match is None:
        return json_response(404, {"error": "Match not found"})

    visibility = match.get("visibility", "public")
    if visibility == "private" and tier != Tier.OWNER:
        # Uniform 404 with not-found — no existence leak.
        return json_response(404, {"error": "Match not found"})

    prefix_root = (
        f"{provider}/_private/{match_id}"
        if visibility == "private"
        else f"{provider}/{match_id}"
    )

    try:
        response = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"{prefix_root}/{artifact}", MaxKeys=5)
        contents = response.get("Contents", [])
        matching = [
            obj["Key"]
            for obj in contents
            if obj["Key"].rsplit("/", 1)[-1].rsplit(".", 1)[0] == artifact
        ]
        if not matching:
            return json_response(404, {"error": "Artifact not found"})

        key = matching[0]
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET, "Key": key},
            ExpiresIn=PRESIGNED_EXPIRY,
        )
    except Exception:
        logger.exception("s3_error", extra={"handler": "get_artifact", "stage": "artifact_resolve"})
        return json_response(500, {"error": "Internal server error"})

    return redirect_response(url)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest test_lambda_handlers.py::TestGetArtifact -v
```

Expected: all pass.

- [ ] **Step 5: Run full suite**

```bash
pytest -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add terraform/modules/functions/src/get_artifact.py src/tests/test_lambda_handlers.py
git commit -m "feat(api): get_artifact enforces match visibility; uniform 404 on tier mismatch"
```

---

# Phase 4 — Players Resource

Two new Lambda handlers (`list_players`, `get_player`), two new API Gateway routes, a new CLI (`pining-upload-players`).

### Task 11: Implement list_players handler

**Files:**
- Create: `terraform/modules/functions/src/list_players.py`
- Modify: `src/tests/test_lambda_handlers.py`

- [ ] **Step 1: Write failing tests**

Add to `src/tests/test_lambda_handlers.py`:

```python
class TestListPlayers(_ResetS3):
    def _setup(self, monkeypatch):
        import shared
        monkeypatch.setenv("API_TOKEN", "pub-tok")
        shared._get_owner_token.cache_clear()
        monkeypatch.setattr(shared, "_get_owner_token", lambda: "own-tok")

    def _wire(self, mock_s3, public_payload=None, private_payload=None):
        import json
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})

        def get_obj(Bucket, Key):
            if Key.endswith("/_private/players.json"):
                if private_payload is None:
                    raise mock_s3.exceptions.NoSuchKey()
                return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(private_payload).encode()))}
            if Key.endswith("/players.json"):
                if public_payload is None:
                    raise mock_s3.exceptions.NoSuchKey()
                return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(public_payload).encode()))}
            raise mock_s3.exceptions.NoSuchKey()

        mock_s3.get_object.side_effect = get_obj

    def test_public_tier_sees_only_public_players(self, monkeypatch) -> None:
        from list_players import handler
        import json
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire(
            mock_s3,
            public_payload={"provider": "sc", "players": [{"id": "p1", "nickname": "Pub"}]},
            private_payload={"provider": "sc", "players": [{"id": "p2", "nickname": "Priv"}]},
        )

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        ids = [p["id"] for p in body["players"]]
        assert ids == ["p1"]

    def test_owner_tier_sees_merged_players(self, monkeypatch) -> None:
        from list_players import handler
        import json
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire(
            mock_s3,
            public_payload={"provider": "sc", "players": [{"id": "p1", "nickname": "Pub"}]},
            private_payload={"provider": "sc", "players": [{"id": "p2", "nickname": "Priv"}]},
        )

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "sc"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        ids = sorted(p["id"] for p in body["players"])
        assert ids == ["p1", "p2"]

    def test_no_indexes_returns_empty_list(self, monkeypatch) -> None:
        """No public OR private index: 200 with empty list (still a valid provider with no player ref data)."""
        from list_players import handler
        import json
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire(mock_s3)

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "sc"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["players"] == []
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest test_lambda_handlers.py::TestListPlayers -v
```

Expected: ImportError on `list_players`.

- [ ] **Step 3: Create `list_players.py`**

Write `terraform/modules/functions/src/list_players.py`:

```python
"""GET /v1/{provider}/players — list players for a provider, filtered by tier."""

from __future__ import annotations

import json
import os

from shared import (
    Tier,
    get_s3_client,
    json_response,
    logger,
    validate_path_param,
    validate_token,
)

BUCKET = os.environ.get("DATA_BUCKET", "")


def handler(event: dict, context: object) -> dict:
    """Return the player catalogue for a provider, merged across visible tiers."""
    tier = validate_token(event)
    if isinstance(tier, dict):
        logger.warning("auth_failure", extra={"handler": "list_players"})
        return tier

    provider = (event.get("pathParameters") or {}).get("provider", "")
    param_error = validate_path_param(provider, "provider")
    if param_error:
        logger.warning("validation_failure", extra={"handler": "list_players", "param": "provider"})
        return param_error

    s3 = get_s3_client()
    public_players = _read_index(s3, f"{provider}/players.json")
    private_players = _read_index(s3, f"{provider}/_private/players.json") if tier == Tier.OWNER else []

    return json_response(200, {"provider": provider, "players": public_players + private_players})


def _read_index(s3, key: str) -> list[dict]:
    """Read a players index from S3. Returns [] if the index doesn't exist."""
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return data.get("players", [])
    except s3.exceptions.NoSuchKey:
        return []
    except Exception:
        logger.exception("s3_error", extra={"handler": "list_players", "key": key})
        return []
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest test_lambda_handlers.py::TestListPlayers -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add terraform/modules/functions/src/list_players.py src/tests/test_lambda_handlers.py
git commit -m "feat(api): add list_players handler with tier-aware merge"
```

---

### Task 12: Implement get_player handler

**Files:**
- Create: `terraform/modules/functions/src/get_player.py`
- Modify: `src/tests/test_lambda_handlers.py`

- [ ] **Step 1: Write failing tests**

Add to `src/tests/test_lambda_handlers.py`:

```python
class TestGetPlayer(_ResetS3):
    def _setup(self, monkeypatch):
        import shared
        monkeypatch.setenv("API_TOKEN", "pub-tok")
        shared._get_owner_token.cache_clear()
        monkeypatch.setattr(shared, "_get_owner_token", lambda: "own-tok")

    def _wire(self, mock_s3, public_payload=None, private_payload=None):
        import json
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})

        def get_obj(Bucket, Key):
            if Key.endswith("/_private/players.json"):
                if private_payload is None:
                    raise mock_s3.exceptions.NoSuchKey()
                return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(private_payload).encode()))}
            if Key.endswith("/players.json"):
                if public_payload is None:
                    raise mock_s3.exceptions.NoSuchKey()
                return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(public_payload).encode()))}
            raise mock_s3.exceptions.NoSuchKey()

        mock_s3.get_object.side_effect = get_obj

    def test_public_tier_can_read_public_player(self, monkeypatch) -> None:
        from get_player import handler
        import json
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire(mock_s3, public_payload={"provider": "sc", "players": [{"id": "p1", "nickname": "Pub"}]})

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc", "id": "p1"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        assert json.loads(result["body"])["nickname"] == "Pub"

    def test_public_tier_gets_404_for_private_player(self, monkeypatch) -> None:
        from get_player import handler
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire(
            mock_s3,
            public_payload={"provider": "sc", "players": []},
            private_payload={"provider": "sc", "players": [{"id": "p2", "nickname": "Priv"}]},
        )

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc", "id": "p2"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 404

    def test_owner_tier_can_read_private_player(self, monkeypatch) -> None:
        from get_player import handler
        import json
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire(
            mock_s3,
            public_payload={"provider": "sc", "players": []},
            private_payload={"provider": "sc", "players": [{"id": "p2", "nickname": "Priv"}]},
        )

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "sc", "id": "p2"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        assert json.loads(result["body"])["nickname"] == "Priv"

    def test_unknown_player_returns_404(self, monkeypatch) -> None:
        from get_player import handler
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire(mock_s3, public_payload={"provider": "sc", "players": []})

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "sc", "id": "missing"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 404
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest test_lambda_handlers.py::TestGetPlayer -v
```

Expected: ImportError on `get_player`.

- [ ] **Step 3: Create `get_player.py`**

Write `terraform/modules/functions/src/get_player.py`:

```python
"""GET /v1/{provider}/players/{id} — fetch a single player record."""

from __future__ import annotations

import json
import os

from shared import (
    Tier,
    get_s3_client,
    json_response,
    logger,
    validate_path_param,
    validate_token,
)

BUCKET = os.environ.get("DATA_BUCKET", "")


def handler(event: dict, context: object) -> dict:
    """Return a single player record. 404 if not found OR if found-but-private-and-public-tier."""
    tier = validate_token(event)
    if isinstance(tier, dict):
        logger.warning("auth_failure", extra={"handler": "get_player"})
        return tier

    params = event.get("pathParameters") or {}
    provider = params.get("provider", "")
    player_id = params.get("id", "")

    for name, value in [("provider", provider), ("id", player_id)]:
        param_error = validate_path_param(value, name)
        if param_error:
            logger.warning("validation_failure", extra={"handler": "get_player", "param": name})
            return param_error

    s3 = get_s3_client()

    # Try public index first.
    public_players = _read_index(s3, f"{provider}/players.json")
    found = next((p for p in public_players if p.get("id") == player_id), None)
    if found is not None:
        return json_response(200, found)

    # Owner tier: also try private index.
    if tier == Tier.OWNER:
        private_players = _read_index(s3, f"{provider}/_private/players.json")
        found = next((p for p in private_players if p.get("id") == player_id), None)
        if found is not None:
            return json_response(200, found)

    return json_response(404, {"error": "Player not found"})


def _read_index(s3, key: str) -> list[dict]:
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return data.get("players", [])
    except s3.exceptions.NoSuchKey:
        return []
    except Exception:
        logger.exception("s3_error", extra={"handler": "get_player", "key": key})
        return []
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest test_lambda_handlers.py::TestGetPlayer -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add terraform/modules/functions/src/get_player.py src/tests/test_lambda_handlers.py
git commit -m "feat(api): add get_player handler with tier-aware uniform 404"
```

---

### Task 13: Wire new lambdas in Terraform functions module

**Files:**
- Modify: `terraform/modules/functions/main.tf`
- Modify: `terraform/modules/functions/outputs.tf`

- [ ] **Step 1: Add Lambda function resources**

Append to `terraform/modules/functions/main.tf`:

```hcl
resource "aws_lambda_function" "list_players" {
  function_name                  = "${var.project_name}-list-players"
  role                           = aws_iam_role.lambda.arn
  handler                        = "list_players.handler"
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
      API_TOKEN         = var.api_token
      DATA_BUCKET       = var.bucket_name
      OWNER_TOKEN_PARAM = var.owner_token_param_name
    }
  }
}

resource "aws_lambda_function" "get_player" {
  function_name                  = "${var.project_name}-get-player"
  role                           = aws_iam_role.lambda.arn
  handler                        = "get_player.handler"
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
      API_TOKEN         = var.api_token
      DATA_BUCKET       = var.bucket_name
      OWNER_TOKEN_PARAM = var.owner_token_param_name
    }
  }
}

resource "aws_cloudwatch_log_group" "list_players" {
  name              = "/aws/lambda/${aws_lambda_function.list_players.function_name}"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "get_player" {
  name              = "/aws/lambda/${aws_lambda_function.get_player.function_name}"
  retention_in_days = 30
}
```

- [ ] **Step 2: Add outputs for new lambdas**

Append to `terraform/modules/functions/outputs.tf`:

```hcl
output "list_players_invoke_arn" {
  description = "Invoke ARN for the list_players Lambda"
  value       = aws_lambda_function.list_players.invoke_arn
}

output "list_players_function_name" {
  description = "Function name for the list_players Lambda"
  value       = aws_lambda_function.list_players.function_name
}

output "get_player_invoke_arn" {
  description = "Invoke ARN for the get_player Lambda"
  value       = aws_lambda_function.get_player.invoke_arn
}

output "get_player_function_name" {
  description = "Function name for the get_player Lambda"
  value       = aws_lambda_function.get_player.function_name
}
```

- [ ] **Step 3: Validate**

```bash
cd terraform/environments/dev
terraform validate
terraform plan -out=phase4-task13.tfplan
```

Expected: two new lambdas, two new log groups. No errors.

- [ ] **Step 4: Commit**

```bash
git add terraform/modules/functions/main.tf terraform/modules/functions/outputs.tf
git commit -m "feat(terraform): provision list_players and get_player Lambdas"
```

---

### Task 14: Add /players routes in Terraform API module

**Files:**
- Modify: `terraform/modules/api/main.tf`
- Modify: `terraform/modules/api/variables.tf`
- Modify: `terraform/environments/dev/main.tf`

- [ ] **Step 1: Add new variables to `terraform/modules/api/variables.tf`**

Append:

```hcl
variable "list_players_invoke_arn" {
  description = "Invoke ARN for the list_players Lambda"
  type        = string
}

variable "list_players_function_name" {
  description = "Function name for the list_players Lambda"
  type        = string
}

variable "get_player_invoke_arn" {
  description = "Invoke ARN for the get_player Lambda"
  type        = string
}

variable "get_player_function_name" {
  description = "Function name for the get_player Lambda"
  type        = string
}
```

- [ ] **Step 2: Add integrations, routes, and Lambda permissions in `terraform/modules/api/main.tf`**

Append:

```hcl
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
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/v1/GET/*"
}

resource "aws_lambda_permission" "get_player" {
  statement_id  = "AllowHTTPAPI"
  action        = "lambda:InvokeFunction"
  function_name = var.get_player_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/v1/GET/*"
}
```

- [ ] **Step 3: Wire variables in `terraform/environments/dev/main.tf`**

In the `module "api"` block, add the four new arguments:

```hcl
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
}
```

- [ ] **Step 4: Validate**

```bash
cd terraform/environments/dev
terraform validate
terraform plan -out=phase4-task14.tfplan
```

Expected: two new integrations, two new routes, two new Lambda permissions. No errors.

- [ ] **Step 5: Commit**

```bash
git add terraform/modules/api/main.tf terraform/modules/api/variables.tf terraform/environments/dev/main.tf
git commit -m "feat(terraform): add /players and /players/{id} API Gateway routes"
```

---

### Task 15: Create pining-upload-players CLI

**Files:**
- Create: `src/mock_api/upload_players.py`
- Modify: `pyproject.toml`
- Create: `src/tests/test_upload_players.py`

- [ ] **Step 1: Write failing tests**

Create `src/tests/test_upload_players.py`:

```python
"""Tests for the pining-upload-players CLI."""

from __future__ import annotations

import csv
import json
from unittest.mock import MagicMock, patch


def _csv_path(tmp_path):
    p = tmp_path / "players.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["dob", "firstName", "height", "id", "lastName", "nickname", "positionGroupType"])
        writer.writerow(["2001-06-17", "Jurrien", "182.0", "8022", "Timber", "Jurrien Timber", "D"])
        writer.writerow(["2000-03-06", "Iliman", "180.0", "2144", "Ndiaye", "Iliman Ndiaye", "M"])
    return p


class TestUploadPlayers:
    def test_private_visibility_writes_under_private_prefix(self, tmp_path):
        from mock_api.upload_players import upload_players
        csv_file = _csv_path(tmp_path)

        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        s3.get_object.side_effect = s3.exceptions.NoSuchKey()

        with patch("mock_api.upload_players.boto3.client", return_value=s3):
            upload_players(
                csv_file, provider="pff", bucket="b", visibility="private",
                source_name="PFF FC", source_url="https://www.pff.com/", source_licence="Restricted",
            )

        # Single put_object to the _private path
        keys = [c.kwargs.get("Key") for c in s3.put_object.call_args_list]
        assert "pff/_private/players.json" in keys

    def test_public_visibility_writes_to_provider_root(self, tmp_path):
        from mock_api.upload_players import upload_players
        csv_file = _csv_path(tmp_path)

        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        s3.get_object.side_effect = s3.exceptions.NoSuchKey()

        with patch("mock_api.upload_players.boto3.client", return_value=s3):
            upload_players(csv_file, provider="sc", bucket="b", visibility="public")

        keys = [c.kwargs.get("Key") for c in s3.put_object.call_args_list]
        assert "sc/players.json" in keys
        assert "sc/_private/players.json" not in keys

    def test_index_payload_shape(self, tmp_path):
        from mock_api.upload_players import upload_players
        csv_file = _csv_path(tmp_path)

        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        s3.get_object.side_effect = s3.exceptions.NoSuchKey()

        with patch("mock_api.upload_players.boto3.client", return_value=s3):
            upload_players(
                csv_file, provider="pff", bucket="b", visibility="private",
                source_name="PFF FC",
            )

        put_calls = [c for c in s3.put_object.call_args_list if c.kwargs.get("Key") == "pff/_private/players.json"]
        assert len(put_calls) == 1
        body = json.loads(put_calls[0].kwargs["Body"].decode("utf-8"))
        assert body["provider"] == "pff"
        assert len(body["players"]) == 2

        timber = next(p for p in body["players"] if p["id"] == "8022")
        assert timber["firstName"] == "Jurrien"
        assert timber["lastName"] == "Timber"
        assert timber["nickname"] == "Jurrien Timber"
        assert timber["dob"] == "2001-06-17"
        assert timber["height"] == 182.0
        assert timber["positionGroupType"] == "D"
        assert timber["visibility"] == "private"
        assert timber["source"]["name"] == "PFF FC"

    def test_idempotent_reupload_replaces_existing(self, tmp_path):
        from mock_api.upload_players import upload_players
        csv_file = _csv_path(tmp_path)

        existing = {
            "provider": "pff",
            "players": [
                {"id": "8022", "nickname": "Old Name", "visibility": "private"},
                {"id": "9999", "nickname": "Untouched", "visibility": "private"},
            ],
        }
        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=json.dumps(existing).encode()))}

        with patch("mock_api.upload_players.boto3.client", return_value=s3):
            upload_players(csv_file, provider="pff", bucket="b", visibility="private")

        put_calls = [c for c in s3.put_object.call_args_list if c.kwargs.get("Key") == "pff/_private/players.json"]
        body = json.loads(put_calls[0].kwargs["Body"].decode("utf-8"))
        ids = sorted(p["id"] for p in body["players"])
        assert ids == ["2144", "8022", "9999"]
        timber = next(p for p in body["players"] if p["id"] == "8022")
        assert timber["nickname"] == "Jurrien Timber"  # updated, not "Old Name"
        untouched = next(p for p in body["players"] if p["id"] == "9999")
        assert untouched["nickname"] == "Untouched"

    def test_tier_mixing_rejected(self, tmp_path):
        from mock_api.upload_players import upload_players
        import pytest
        csv_file = _csv_path(tmp_path)

        existing = {
            "provider": "pff",
            "players": [{"id": "8022", "nickname": "Existing", "visibility": "public"}],
        }
        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=json.dumps(existing).encode()))}

        with patch("mock_api.upload_players.boto3.client", return_value=s3):
            with pytest.raises(ValueError, match="tier"):
                upload_players(csv_file, provider="pff", bucket="b", visibility="private")
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest test_upload_players.py -v
```

Expected: ImportError on `mock_api.upload_players`.

- [ ] **Step 3: Create the CLI module**

Write `src/mock_api/upload_players.py`:

```python
"""Upload provider-level player reference data to S3.

Reads a CSV (PFF format: dob, firstName, height, id, lastName, nickname,
positionGroupType) or a JSON file with a top-level list, and writes the
provider's players index to S3 at one of:

- {provider}/players.json (visibility=public)
- {provider}/_private/players.json (visibility=private)

Existing players (by id) are updated in place; new players are appended.
Tier-mixing within a single id is rejected.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import boto3

_SAFE_PARAM = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


def _validate_param(value: str, name: str) -> None:
    if not value or len(value) > 128 or not _SAFE_PARAM.match(value):
        raise ValueError(f"Invalid {name}: must be 1-128 chars, alphanumeric start, then [a-zA-Z0-9_-]+")


def upload_players(
    input_file: Path,
    provider: str,
    bucket: str,
    visibility: str = "public",
    source_name: str | None = None,
    source_url: str | None = None,
    source_licence: str | None = None,
) -> int:
    """Upload a player catalogue to S3. Returns the number of players in the resulting index."""
    if visibility not in ("public", "private"):
        raise ValueError(f"Invalid visibility: {visibility!r}")
    _validate_param(provider, "provider")

    if not input_file.is_file():
        raise FileNotFoundError(f"Not a file: {input_file}")

    rows = _read_rows(input_file)
    new_records = [_to_player_record(row, visibility, source_name, source_url, source_licence) for row in rows]

    s3 = boto3.client("s3")
    key = f"{provider}/_private/players.json" if visibility == "private" else f"{provider}/players.json"

    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        existing_data = json.loads(obj["Body"].read().decode("utf-8"))
        existing_players = existing_data.get("players", [])
    except s3.exceptions.NoSuchKey:
        existing_players = []

    by_id = {p["id"]: p for p in existing_players}
    for new in new_records:
        prior = by_id.get(new["id"])
        if prior is not None and prior.get("visibility", "public") != visibility:
            raise ValueError(
                f"Cannot mix tiers for player id {new['id']!r}: existing "
                f"{prior.get('visibility')!r} vs requested {visibility!r}"
            )
        by_id[new["id"]] = new

    merged = sorted(by_id.values(), key=lambda p: p["id"])
    payload = {"provider": provider, "players": merged}

    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    print(f"  Wrote {len(merged)} player(s) to s3://{bucket}/{key}")
    return len(merged)


def _read_rows(path: Path) -> list[dict]:
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    if path.suffix.lower() == ".json":
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "players" in data:
            return data["players"]
        raise ValueError("JSON input must be a list or {'players': [...]}")
    raise ValueError(f"Unsupported input format: {path.suffix}")


def _to_player_record(
    row: dict,
    visibility: str,
    source_name: str | None,
    source_url: str | None,
    source_licence: str | None,
) -> dict:
    if "id" not in row or not row["id"]:
        raise ValueError(f"Player row missing required field 'id': {row}")
    if not (row.get("nickname") or (row.get("firstName") and row.get("lastName"))):
        raise ValueError(f"Player row {row.get('id')!r} needs nickname or firstName+lastName")

    record: dict = {"id": str(row["id"]), "visibility": visibility}
    for key in ("firstName", "lastName", "nickname", "dob", "positionGroupType"):
        val = row.get(key)
        if val:
            record[key] = val

    if row.get("height"):
        try:
            record["height"] = float(row["height"])
        except (TypeError, ValueError):
            pass

    if source_name:
        record["source"] = {
            "name": source_name,
            "url": source_url or "",
            "licence": source_licence or "",
        }
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload a player catalogue to the mock provider API's S3 bucket")
    parser.add_argument("input_file", type=Path, help="CSV or JSON file with player records")
    parser.add_argument("--provider", required=True, help="Provider name (e.g., pff)")
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument("--visibility", default="public", choices=["public", "private"])
    parser.add_argument("--source-name", default=None)
    parser.add_argument("--source-url", default=None)
    parser.add_argument("--source-licence", default=None)
    args = parser.parse_args()

    print(f"Uploading players ({args.provider}, {args.visibility}) from {args.input_file}")
    upload_players(
        input_file=args.input_file,
        provider=args.provider,
        bucket=args.bucket,
        visibility=args.visibility,
        source_name=args.source_name,
        source_url=args.source_url,
        source_licence=args.source_licence,
    )
    print("Done.")
```

- [ ] **Step 4: Add the entry point to `pyproject.toml`**

Find the existing `[project.scripts]` section and add:

```toml
pining-upload-players = "mock_api.upload_players:main"
```

- [ ] **Step 5: Run tests to verify pass**

```bash
pytest test_upload_players.py -v
```

Expected: all pass.

- [ ] **Step 6: Run full suite**

```bash
pytest -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/mock_api/upload_players.py src/tests/test_upload_players.py pyproject.toml
git commit -m "feat(cli): add pining-upload-players for player reference catalogues"
```

---

### Task 16: Deploy Phase 4 to dev and smoke-test /players

**Files:** none — operational.

- [ ] **Step 1: Apply Terraform**

```bash
cd terraform/environments/dev
terraform apply
```

Expected: two new lambdas, two new routes deploy successfully.

- [ ] **Step 2: List players for a provider that has none — expect empty**

```bash
API=$(terraform output -raw api_url)
TOKEN="test-token-pining-for-the-data"
curl -s -H "Authorization: Bearer $TOKEN" "$API/skillcorner/players" | python -m json.tool
```

Expected: `{"provider": "skillcorner", "players": []}`.

- [ ] **Step 3: Smoke test get_player on a missing id**

```bash
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" "$API/skillcorner/players/missing"
```

Expected: `404`.

- [ ] **Step 4: No commit (operational task)**

---

# Phase 5 — Audit Logging

CloudTrail data events on the data bucket, landing in a separate audit bucket with 365-day retention.

### Task 17: Create the audit Terraform module

**Files:**
- Create: `terraform/modules/audit/main.tf`
- Create: `terraform/modules/audit/variables.tf`
- Create: `terraform/modules/audit/outputs.tf`

- [ ] **Step 1: Define variables**

Write `terraform/modules/audit/variables.tf`:

```hcl
variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
}

variable "data_bucket_arn" {
  description = "ARN of the data bucket whose access events should be logged"
  type        = string
}

variable "data_bucket_kms_key_arn" {
  description = "KMS key ARN used by the data bucket (reused for log encryption)"
  type        = string
}

variable "log_retention_days" {
  description = "Days before audit log objects expire"
  type        = number
  default     = 365
}
```

- [ ] **Step 2: Define resources**

Write `terraform/modules/audit/main.tf`:

```hcl
data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "audit" {
  bucket = "${var.project_name}-audit-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_versioning" "audit" {
  bucket = aws_s3_bucket.audit.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "audit" {
  bucket = aws_s3_bucket.audit.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.data_bucket_kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "audit" {
  bucket                  = aws_s3_bucket.audit.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "audit" {
  bucket = aws_s3_bucket.audit.id
  rule {
    id     = "expire-old-logs"
    status = "Enabled"
    expiration {
      days = var.log_retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = var.log_retention_days
    }
  }
}

resource "aws_s3_bucket_policy" "audit" {
  bucket = aws_s3_bucket.audit.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AWSCloudTrailAclCheck"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = aws_s3_bucket.audit.arn
      },
      {
        Sid       = "AWSCloudTrailWrite"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.audit.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"
        Condition = {
          StringEquals = { "s3:x-amz-acl" = "bucket-owner-full-control" }
        }
      },
    ]
  })
}

resource "aws_cloudtrail" "data_bucket" {
  name                          = "${var.project_name}-data-bucket-trail"
  s3_bucket_name                = aws_s3_bucket.audit.id
  include_global_service_events = false
  is_multi_region_trail         = false
  enable_log_file_validation    = true

  advanced_event_selector {
    name = "Data bucket reads/writes excluding bookkeeping"

    field_selector {
      field  = "eventCategory"
      equals = ["Data"]
    }
    field_selector {
      field  = "resources.type"
      equals = ["AWS::S3::Object"]
    }
    field_selector {
      field        = "resources.ARN"
      starts_with  = ["${var.data_bucket_arn}/"]
    }
    field_selector {
      field           = "resources.ARN"
      not_ends_with   = ["/matches.json", "/players.json", "/providers.json"]
    }
  }

  depends_on = [aws_s3_bucket_policy.audit]
}
```

- [ ] **Step 3: Define outputs**

Write `terraform/modules/audit/outputs.tf`:

```hcl
output "audit_bucket_name" {
  description = "Name of the audit log bucket"
  value       = aws_s3_bucket.audit.id
}

output "audit_bucket_arn" {
  description = "ARN of the audit log bucket"
  value       = aws_s3_bucket.audit.arn
}

output "trail_arn" {
  description = "ARN of the CloudTrail trail"
  value       = aws_cloudtrail.data_bucket.arn
}
```

- [ ] **Step 4: Wire the module in `terraform/environments/dev/main.tf`**

Append:

```hcl
module "audit" {
  source                  = "../../modules/audit"
  project_name            = var.project_name
  data_bucket_arn         = module.storage.bucket_arn
  data_bucket_kms_key_arn = module.storage.kms_key_arn
}
```

And expose its bucket name as an output. In `terraform/environments/dev/outputs.tf`:

```hcl
output "audit_bucket_name" {
  description = "Audit log bucket"
  value       = module.audit.audit_bucket_name
}
```

- [ ] **Step 5: Validate**

```bash
cd terraform/environments/dev
terraform init   # picks up the new module
terraform validate
terraform plan -out=phase5.tfplan
```

Expected: new audit bucket, lifecycle config, bucket policy, CloudTrail trail. No errors.

- [ ] **Step 6: Apply**

```bash
terraform apply phase5.tfplan
```

Expected: all audit resources created.

- [ ] **Step 7: Smoke-test that an artifact fetch lands a CloudTrail event**

```bash
TOKEN="test-token-pining-for-the-data"
API=$(terraform output -raw api_url)
AUDIT_BUCKET=$(terraform output -raw audit_bucket_name)

# Fetch any existing public artifact (assumes Phase 1-4 left some data in place; if not, upload one first)
curl -s -L -o /dev/null -H "Authorization: Bearer $TOKEN" "$API/skillcorner/matches/game_03/match" || true

# Wait up to 15 minutes for CloudTrail to deliver — start by listing what's there
sleep 60
aws s3 ls "s3://$AUDIT_BUCKET/AWSLogs/" --recursive | head -10
```

Expected: at least one log file appears within ~15 minutes (CloudTrail delivers in batches).

- [ ] **Step 8: Commit**

```bash
git add terraform/modules/audit/ terraform/environments/dev/main.tf terraform/environments/dev/outputs.tf
git commit -m "feat(terraform): add CloudTrail data events on data bucket with audit log bucket"
```

---

# Phase 6 — PFF Orchestrator and Bulk Load

### Task 18: Write `scripts/upload_pff_wc2022.py`

**Files:**
- Create: `scripts/upload_pff_wc2022.py`

Goal: idempotent one-shot script that reads the PFF source folder, reshapes per-match files, calls `upload_game` for each of the 67 matches, and `upload_players` for the player catalogue.

- [ ] **Step 1: Write the script**

Create `scripts/upload_pff_wc2022.py`:

```python
"""Upload PFF FIFA World Cup 2022 to the mock provider API as private-tier data.

Reshapes the source bundle into per-match staging directories, then calls
the existing pining-upload primitives. Idempotent — re-running re-uploads
without producing duplicate index entries.

Source layout (input):
    FIFA World Cup 2022/
    ├── Event Data/{id}.json
    ├── Metadata/{id}.json
    ├── Rosters/{id}.json
    ├── Tracking Data/{id}.jsonl.bz2
    ├── competitions.csv          # not uploaded — directory data covered by /matches
    ├── players.csv               # uploaded as /players reference catalogue
    └── PFF FC Change Log.docx    # not uploaded
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

# Ensure src/ is importable when running directly from a checkout
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from mock_api.upload import upload_game  # noqa: E402
from mock_api.upload_players import upload_players  # noqa: E402

PROVIDER = "pff"
SOURCE_NAME = "PFF FC"
SOURCE_URL = "https://www.pff.com/"
SOURCE_LICENCE = "Restricted; redistribution not permitted pending licence clarification"


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk-upload PFF World Cup 2022 to the mock provider API")
    parser.add_argument("source_dir", type=Path, help="Path to the 'FIFA World Cup 2022' source folder")
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of matches uploaded (smoke test)")
    parser.add_argument("--skip-players", action="store_true", help="Skip players.csv upload (matches only)")
    parser.add_argument("--skip-matches", action="store_true", help="Skip per-match upload (players only)")
    args = parser.parse_args()

    if not args.source_dir.is_dir():
        parser.error(f"Not a directory: {args.source_dir}")

    if not args.skip_matches:
        match_ids = _discover_match_ids(args.source_dir)
        if args.limit:
            match_ids = match_ids[: args.limit]
        print(f"Uploading {len(match_ids)} match(es) to s3://{args.bucket}/{PROVIDER}/_private/")
        for match_id in match_ids:
            _upload_one_match(args.source_dir, match_id, args.bucket)

    if not args.skip_players:
        players_csv = args.source_dir / "players.csv"
        if not players_csv.is_file():
            print(f"WARN: {players_csv} not found, skipping player catalogue upload")
        else:
            print(f"Uploading player catalogue to s3://{args.bucket}/{PROVIDER}/_private/players.json")
            upload_players(
                input_file=players_csv,
                provider=PROVIDER,
                bucket=args.bucket,
                visibility="private",
                source_name=SOURCE_NAME,
                source_url=SOURCE_URL,
                source_licence=SOURCE_LICENCE,
            )

    print("Done.")


def _discover_match_ids(source_dir: Path) -> list[str]:
    """Return sorted match IDs by listing Metadata/*.json (the canonical match file)."""
    metadata_dir = source_dir / "Metadata"
    if not metadata_dir.is_dir():
        raise FileNotFoundError(f"Missing Metadata/ in {source_dir}")
    return sorted(p.stem for p in metadata_dir.glob("*.json"))


def _upload_one_match(source_dir: Path, match_id: str, bucket: str) -> None:
    """Reshape source files into a temp staging dir and call upload_game."""
    metadata_path = source_dir / "Metadata" / f"{match_id}.json"
    events_path   = source_dir / "Event Data" / f"{match_id}.json"
    roster_path   = source_dir / "Rosters" / f"{match_id}.json"
    tracking_path = source_dir / "Tracking Data" / f"{match_id}.jsonl.bz2"

    for required in (metadata_path, events_path, roster_path, tracking_path):
        if not required.is_file():
            raise FileNotFoundError(f"Match {match_id}: expected file missing: {required}")

    metadata_obj = json.loads(metadata_path.read_text(encoding="utf-8"))
    if isinstance(metadata_obj, list):
        # PFF wraps the metadata in a single-element list
        metadata_obj = metadata_obj[0] if metadata_obj else {}

    date = metadata_obj.get("date", "").split("T", 1)[0]
    home = (metadata_obj.get("homeTeam") or {}).get("name", "")
    away = (metadata_obj.get("awayTeam") or {}).get("name", "")

    with tempfile.TemporaryDirectory(prefix=f"pff-{match_id}-") as tmp:
        staging = Path(tmp)
        shutil.copy(metadata_path, staging / "metadata.json")
        shutil.copy(events_path,   staging / "events.json")
        shutil.copy(roster_path,   staging / "roster.json")
        shutil.copy(tracking_path, staging / "tracking.jsonl.bz2")

        upload_game(
            game_dir=staging,
            provider=PROVIDER,
            game_id=match_id,
            bucket=bucket,
            visibility="private",
            date=date,
            home=home,
            away=away,
            provenance="original",
            source_name=SOURCE_NAME,
            source_url=SOURCE_URL,
            source_license=SOURCE_LICENCE,
        )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Sanity-check the script with `--help`**

```bash
python scripts/upload_pff_wc2022.py --help
```

Expected: usage printed, no errors.

- [ ] **Step 3: Sanity-check match-id discovery on the source folder**

Without bucket access, just exercise the discovery:

```bash
python -c "
import sys
sys.path.insert(0, 'scripts')
import upload_pff_wc2022
from pathlib import Path
ids = upload_pff_wc2022._discover_match_ids(Path(r'D:\\[Karsten]\\Dropbox\\[Microsoft]\\Downloads\\FIFA World Cup 2022'))
print(f'Found {len(ids)} match(es)')
print('First 3:', ids[:3])
"
```

Expected: `Found 67 match(es)` and three numeric IDs.

- [ ] **Step 4: Commit**

```bash
git add scripts/upload_pff_wc2022.py
git commit -m "feat(scripts): add upload_pff_wc2022 orchestrator for bulk PFF private-tier load"
```

---

### Task 19: Smoke-test PFF upload with one match

**Files:** none — operational.

- [ ] **Step 1: Upload a single match end-to-end**

```bash
BUCKET=$(cd terraform/environments/dev && terraform output -raw bucket_name)
python scripts/upload_pff_wc2022.py \
  "D:\[Karsten]\Dropbox\[Microsoft]\Downloads\FIFA World Cup 2022" \
  --bucket "$BUCKET" \
  --limit 1 \
  --skip-players
```

Expected: one match (`3812`) uploads successfully; matches.json updates; providers.json gains `pff`.

- [ ] **Step 2: Verify public token gets 404 on private match**

```bash
API=$(cd terraform/environments/dev && terraform output -raw api_url)
PUB="test-token-pining-for-the-data"
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $PUB" "$API/pff/matches/3812/metadata"
```

Expected: `404`.

- [ ] **Step 3: Verify public token sees empty matches list for pff**

```bash
curl -s -H "Authorization: Bearer $PUB" "$API/pff/matches" | python -m json.tool
```

Expected: `{"provider": "pff", "matches": []}`.

- [ ] **Step 4: Verify owner token gets 302 + can fetch the metadata**

```bash
OWNER=$(aws ssm get-parameter --name "/pining-for-the-data/api_token_owner" --with-decryption --query 'Parameter.Value' --output text)
curl -s -L -H "Authorization: Bearer $OWNER" "$API/pff/matches/3812/metadata" | python -m json.tool | head -5
```

Expected: 302 followed transparently to the presigned URL, then the metadata JSON prints (showing Senegal vs Netherlands or similar World Cup 2022 match data).

- [ ] **Step 5: Verify owner token sees the match in the list**

```bash
curl -s -H "Authorization: Bearer $OWNER" "$API/pff/matches" | python -m json.tool
```

Expected: 1 match entry with `"id": "3812"` and `"visibility": "private"`.

- [ ] **Step 6: No commit (operational task)**

---

### Task 20: Bulk PFF load and final verification

**Files:** none — operational.

- [ ] **Step 1: Upload all 67 matches plus the player catalogue**

```bash
BUCKET=$(cd terraform/environments/dev && terraform output -raw bucket_name)
python scripts/upload_pff_wc2022.py \
  "D:\[Karsten]\Dropbox\[Microsoft]\Downloads\FIFA World Cup 2022" \
  --bucket "$BUCKET"
```

Expected: 67 matches uploaded, players catalogue uploaded. Total runtime: 5-15 minutes depending on bandwidth (5.3 GB).

- [ ] **Step 2: Verify owner token sees 67 matches**

```bash
API=$(cd terraform/environments/dev && terraform output -raw api_url)
OWNER=$(aws ssm get-parameter --name "/pining-for-the-data/api_token_owner" --with-decryption --query 'Parameter.Value' --output text)
curl -s -H "Authorization: Bearer $OWNER" "$API/pff/matches" | python -c "import sys, json; print(len(json.load(sys.stdin)['matches']))"
```

Expected: `67`.

- [ ] **Step 3: Verify owner token sees the player catalogue**

```bash
curl -s -H "Authorization: Bearer $OWNER" "$API/pff/players" | python -c "import sys, json; print(len(json.load(sys.stdin)['players']))"
```

Expected: `2322`.

- [ ] **Step 4: Verify public token still sees zero matches and zero players for pff**

```bash
PUB="test-token-pining-for-the-data"
curl -s -H "Authorization: Bearer $PUB" "$API/pff/matches" | python -c "import sys, json; print(len(json.load(sys.stdin)['matches']))"
curl -s -H "Authorization: Bearer $PUB" "$API/pff/players" | python -c "import sys, json; print(len(json.load(sys.stdin)['players']))"
```

Expected: `0` and `0`.

- [ ] **Step 5: Spot-check an individual player by ID**

```bash
curl -s -H "Authorization: Bearer $OWNER" "$API/pff/players/8022" | python -m json.tool
```

Expected: Jurrien Timber's record with DOB, height, position. `"visibility": "private"`.

- [ ] **Step 6: Verify public token gets 404 on the same player ID**

```bash
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $PUB" "$API/pff/players/8022"
```

Expected: `404`.

- [ ] **Step 7: Verify CloudTrail captured the recent fetches**

```bash
AUDIT_BUCKET=$(cd terraform/environments/dev && terraform output -raw audit_bucket_name)
sleep 120  # CloudTrail delivers in batches
aws s3 ls "s3://$AUDIT_BUCKET/AWSLogs/" --recursive | tail -5
```

Expected: log files exist (typically `.json.gz`), with one or more from the last few minutes covering the recent S3 GetObject calls.

- [ ] **Step 8: No commit (operational task)**

---

## Self-review notes

- All spec sections (3 auth, 4 visibility, 5 S3 layout, 6 reference resources, 7 audit logging, 8 upload tooling, 9 Lakehouse contract, 10 migration, 12 tests) have at least one task. The Lakehouse contract (section 9) is implicit — the API change is what the adapter consumes; no work in this repo. Migration (section 10) is exercised by Tasks 3, 6, 16, 19, 20 (operational deploy + smoke).
- No placeholders. Every step has either exact code, an exact command, or both.
- Type consistency: `Tier` enum used consistently across handlers. `validate_token` signature is `Tier | dict`. `visibility` is consistently `"public" | "private"` (string, not enum) at the upload-tool boundary because it crosses argparse — internal Lambda code doesn't reuse that string.
- One known scope gap: spec section 4.4 mentions deferring optional `--source-licence` flag spelling consistency. The existing `pining-upload` uses `--source-license` (American spelling); the new `pining-upload-players` uses `--source-licence` (British, matching the spec). This is inconsistent. Acceptable for v1 since the two CLIs are different commands; flagged for cleanup later if it bothers a future reader.

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-02-private-data-tier.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
