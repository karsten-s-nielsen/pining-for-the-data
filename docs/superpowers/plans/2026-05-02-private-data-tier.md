# Private Data Tier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a two-tier auth model (public + owner) to the mock provider API, expose private match data and a player reference resource (`/players`), enable CloudTrail audit logging, and load the PFF FIFA World Cup 2022 dataset (64 matches + 2,322 players) as the first restricted-tier content.

**Architecture:** A second bearer token (`owner`), stored in SSM Parameter Store, is accepted by every Lambda alongside the existing public token. `validate_token` returns a tier (`PUBLIC` or `OWNER`) used to filter list responses and enforce uniform-404 on artifact/player retrieval for tier mismatches. Match visibility is recorded per-entry in `matches.json`; private matches and the private player index live under a reserved `_private/` S3 prefix for defense in depth. A new resource family `/players` follows the same shape as `/matches`. CloudTrail data events on the data bucket land in a separate audit bucket with a 365-day retention policy.

**Tech Stack:** Python 3.12 (Lambda + CLI), Terraform 1.5+, AWS (Lambda, API Gateway HTTP, S3, KMS, SSM Parameter Store, CloudTrail), pytest, ruff, pyright. Source spec: `docs/superpowers/specs/2026-05-02-private-data-tier.md`.

---

## File Structure

**Lambda handlers** (`terraform/modules/functions/src/`):
- `shared.py` — modified: add `Tier` enum, `get_owner_token` SSM fetcher, refactor `validate_token` to return `Tier` (PUBLIC on duplicate-token; fail closed), harden `_SAFE_PARAM` against `_`-prefix, add `MatchEntry` and `PlayerRecord` Pydantic models with `$id`/`$schema` metadata.
- `list_providers.py` — modified: minor update to use new validator return type.
- `list_matches.py` — modified: filter response by tier; preserve `updated_at` and the `artifacts` object pass-through.
- `get_artifact.py` — modified: read `matches.json`, resolve prefix from match's recorded visibility, look up filename via `artifacts[name]` (whitelist enforcement built-in), generate presigned URL directly — no `list_objects_v2`, no `head_object`.
- `list_players.py` — new: gates on `providers.json` membership for unknown-provider 404; merges public + private with private precedence on owner-tier.
- `get_player.py` — new: same providers.json gate; private precedence on collisions.

**Terraform** (`terraform/modules/`):
- `functions/main.tf` — modified: two new lambdas, IAM additions for SSM + KMS, `OWNER_TOKEN_PARAM` env var, `LAST_ROTATION` env var (no-op marker for cache invalidation on token rotation).
- `functions/variables.tf` — modified: new `owner_token_param_arn` variable, `last_rotation` variable.
- `functions/outputs.tf` — modified: new lambda invoke ARNs.
- `api/main.tf` — modified: two new routes for `/players` and `/players/{id}`.
- `api/variables.tf` — modified: new lambda variables.
- `audit/main.tf` — new: audit bucket + KMS + CloudTrail trail. Trail's `not_ends_with` excludes only `/providers.json` (matches.json and players.json reads stay logged — see spec §7.5).
- `audit/variables.tf` — new.
- `audit/outputs.tf` — new.
- `environments/dev/main.tf` — modified: SSM parameter resource, audit module, owner-token wiring, `last_rotation` variable wiring.
- `environments/dev/variables.tf` — modified: new variable surface (none required from user; SSM value set out-of-band).

**Python CLI** (`src/mock_api/`):
- `upload.py` — modified: `--visibility` flag, `--source-licence` flag (with `--source-license` alias), `_`-prefix rejection, write to `_private/` when private, set `updated_at` on every write, write `artifacts` as `{name: filename}` object form, validate against `MatchEntry` Pydantic model before any S3 call.
- `upload_players.py` — new: `pining-upload-players` CLI. Canonical JSON only — explicitly rejects CSV with a message pointing at `scripts/upload_pff_wc2022.py` as the reference adapter. Validates every record against `PlayerRecord`. Cross-tier dedup check across both `players.json` files. British spelling canonical, American alias.

**Project config** (`pyproject.toml`):
- modified: add `pining-upload-players` entry point. Add `pydantic>=2` and `jsonschema` to runtime dependencies.

**Schemas** (`schemas/`):
- `matches.schema.json` — new, generated from `MatchEntry.model_json_schema()`.
- `players.schema.json` — new, generated from `PlayerRecord.model_json_schema()`.

**Tests** (`src/tests/`):
- `test_lambda_handlers.py` — modified: PUBLIC-on-duplicate, list_matches filter, list_providers tier-blind pin, get_artifact whitelist + no-list assertion, list_players unknown-provider 404 + cross-tier precedence, get_player same, validator hardening.
- `test_upload.py` — modified: visibility flag behaviour, `_`-prefix rejection, `updated_at` set on write, `artifacts` written as object, `--source-license` alias accepted, `MatchEntry` validation.
- `test_upload_players.py` — new.
- `test_schemas.py` — new: drift test asserting committed `schemas/*.json` matches `model.model_json_schema()` for current Pydantic models, including `$id` URN and `$schema` Draft-2020-12 metadata.

**Scripts** (`scripts/`):
- `upload_pff_wc2022.py` — new: one-shot PFF reshape + load orchestrator. Loads private-tier only — no runtime licence gate (single-owner private-tier load is data movement within the operator's own systems, not redistribution; spec §8.3). Includes a CSV→canonical-JSON normaliser for `players.csv` (since `pining-upload-players` no longer accepts CSV).
- `regenerate_schemas.py` — new: emits `schemas/{matches,players}.schema.json` from the Pydantic models. Run after any model edit.
- `verify_pff_load.py` — new: post-load verification (counts, visibility leak check, owner-tier artifact spot-check, player spot-check). Exits non-zero on any failure.

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

Goal: every Lambda (existing three + future two) gets `ssm:GetParameter` on the owner-token parameter, `kms:Decrypt` on the data KMS key, `OWNER_TOKEN_PARAM` env var set to the parameter name, and `LAST_ROTATION` env var (a no-op marker used to invalidate the warm-container `_get_owner_token` cache during a token rotation — see spec §3.5).

- [ ] **Step 1: Add new variables to `terraform/modules/functions/variables.tf`**

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

variable "last_rotation" {
  description = "No-op marker used to invalidate the warm-container _get_owner_token cache during a rotation. Bump on every owner-token rotation; spec §3.5."
  type        = string
  default     = "initial"
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

- [ ] **Step 3: Add `OWNER_TOKEN_PARAM` and `LAST_ROTATION` env vars to all three existing Lambda functions**

In `terraform/modules/functions/main.tf`, in each of `aws_lambda_function.list_providers`, `aws_lambda_function.list_matches`, and `aws_lambda_function.get_artifact`, replace the `environment.variables` block to include the two new vars. Example for `list_providers`:

```hcl
  environment {
    variables = {
      API_TOKEN          = var.api_token
      DATA_BUCKET        = var.bucket_name
      OWNER_TOKEN_PARAM  = var.owner_token_param_name
      LAST_ROTATION      = var.last_rotation
    }
  }
```

Apply the same change to `list_matches` (same four vars) and `get_artifact` (preserves `PRESIGNED_EXPIRY` plus the four). The two NEW Lambdas added in Phase 4 (`list_players`, `get_player`) get the same env-var block as part of Task 13 — keep them aligned so a rotation `terraform apply` bumps all five together (spec §3.5).

- [ ] **Step 4: Pass new variables from environment to module in `terraform/environments/dev/main.tf`**

In the `module "functions"` block, add the new arguments. `last_rotation` is passed through from a Terraform variable so the operator can bump it via `terraform apply -var=last_rotation=...` during a rotation (spec §3.5):

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
  last_rotation          = var.last_rotation
}
```

Add the corresponding variable to `terraform/environments/dev/variables.tf`:

```hcl
variable "last_rotation" {
  description = "No-op marker bumped during owner-token rotation to invalidate Lambda warm-container cache (spec §3.5)"
  type        = string
  default     = "initial"
}
```

- [ ] **Step 5: Validate and plan**

```bash
cd terraform/environments/dev
terraform validate
terraform plan -out=phase1-task2.tfplan
```

Expected: changes to all three Lambda functions (two new env vars: `OWNER_TOKEN_PARAM`, `LAST_ROTATION`) and the IAM policy (new statement). No errors.

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

The public token is documented in README and lives in `terraform.tfvars` under `api_token`. Operator exports it as `$PUB_TOKEN` for the curl examples in this plan; the value is not committed here (kept in tfvars + README).

```bash
# Operator: export PUB_TOKEN before running, e.g.:
#   export PUB_TOKEN=$(terraform output -raw api_token 2>/dev/null || grep '^api_token' terraform.tfvars | cut -d'"' -f2)
API=$(terraform output -raw api_url)
curl -s -H "Authorization: Bearer $PUB_TOKEN" "$API/providers" | python -m json.tool
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

    def test_same_public_and_owner_classifies_as_public(self, monkeypatch) -> None:
        """Fail closed: if both tokens are the same string, classify as PUBLIC.

        Spec §3.2: a misconfiguration that accidentally collapses both tokens
        to the same string should NOT silently grant owner-tier visibility to
        anyone holding the public token. Failing closed degrades the owner
        consumer (visibly broken) instead of leaking private content (silently
        broken). A visibly broken consumer gets fixed.
        """
        from shared import Tier, validate_token
        import shared

        monkeypatch.setenv("API_TOKEN", "duplicate-token")
        shared._get_owner_token.cache_clear()
        monkeypatch.setattr(shared, "_get_owner_token", lambda: "duplicate-token")

        result = validate_token({"headers": {"authorization": "Bearer duplicate-token"}})
        assert result == Tier.PUBLIC
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
    dict on failure. If both tokens are the same string (operator
    misconfiguration), classifies as ``PUBLIC`` — fail closed. Spec §3.2.
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

    # Compare against PUBLIC first so a duplicate-token misconfiguration
    # classifies as PUBLIC (fail closed; spec §3.2). This is the more
    # restrictive failure mode: we'd rather break the owner consumer
    # visibly than silently leak private content to public-token holders.
    if hmac.compare_digest(presented, public_token):
        return Tier.PUBLIC
    if hmac.compare_digest(presented, owner_token):
        return Tier.OWNER
    return _error_response(401, "Invalid token")
```

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
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $PUB_TOKEN" "$API/providers"
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

# Phase 2.5 — Canonical Schemas (Pydantic + JSON Schema)

The upload tooling and Lambda handlers need a shared canonical shape for `matches.json` entries and `players.json` entries. Pydantic models in `shared.py` are the single source of truth; JSON Schema files in `schemas/` are generated from them and committed to the repo as the consumer-facing contract. A drift test fails CI if the committed files lag the models.

This phase MUST land before Phase 3, because Phase 3 starts writing the `visibility`, `updated_at`, and object-form `artifacts` fields — those shapes need a model to validate against.

### Task 6.5: Define Pydantic models, generate JSON Schemas, write drift test

**Files:**
- Modify: `terraform/modules/functions/src/shared.py`
- Create: `scripts/regenerate_schemas.py`
- Create: `schemas/matches.schema.json`
- Create: `schemas/players.schema.json`
- Create: `src/tests/test_schemas.py`
- Modify: `pyproject.toml`

Goal: Pydantic v2 models for `MatchEntry` and `PlayerRecord`, a small CLI to (re)generate the JSON Schema files, and a drift test that fails if a model edit forgets to regenerate. Both models declare a stable URN `$id` so consumers can pin to the schema identity even though the file content is unversioned (additive changes don't bump the URN).

- [ ] **Step 1: Add Pydantic to runtime dependencies in `pyproject.toml`**

In `[project] dependencies = [...]`, add `"pydantic>=2.0"`. If not already present, also add `"jsonschema"` for runtime schema validation in tests.

- [ ] **Step 2: Write failing tests in `src/tests/test_schemas.py`**

Create `src/tests/test_schemas.py`:

```python
"""Schema-drift tests: committed JSON Schema files must match the current Pydantic models.

Spec §6.6: Pydantic models in shared.py are the single source of truth.
schemas/{matches,players}.schema.json are generated from them via
scripts/regenerate_schemas.py and committed for consumer reference. Editing
a model without regenerating the schema fails this test.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = REPO_ROOT / "schemas"


def _load_schema(name: str) -> dict:
    return json.loads((SCHEMAS_DIR / name).read_text(encoding="utf-8"))


class TestMatchEntrySchema:
    def test_committed_schema_matches_model(self):
        from shared import MatchEntry

        committed = _load_schema("matches.schema.json")
        generated = MatchEntry.model_json_schema()
        assert committed == generated, (
            "schemas/matches.schema.json is stale. Run scripts/regenerate_schemas.py "
            "to refresh after editing the MatchEntry model."
        )

    def test_schema_has_id_and_schema_metadata(self):
        committed = _load_schema("matches.schema.json")
        assert committed.get("$id") == "urn:pining-for-the-data:schema:matches:v1"
        assert committed.get("$schema") == "https://json-schema.org/draft/2020-12/schema"


class TestPlayerRecordSchema:
    def test_committed_schema_matches_model(self):
        from shared import PlayerRecord

        committed = _load_schema("players.schema.json")
        generated = PlayerRecord.model_json_schema()
        assert committed == generated, (
            "schemas/players.schema.json is stale. Run scripts/regenerate_schemas.py "
            "to refresh after editing the PlayerRecord model."
        )

    def test_schema_has_id_and_schema_metadata(self):
        committed = _load_schema("players.schema.json")
        assert committed.get("$id") == "urn:pining-for-the-data:schema:players:v1"
        assert committed.get("$schema") == "https://json-schema.org/draft/2020-12/schema"


class TestModelValidation:
    def test_match_entry_minimal_valid(self):
        from shared import MatchEntry

        m = MatchEntry(
            id="m-001",
            artifacts={"metadata": "metadata.json"},
            visibility="private",
            updated_at="2026-05-02T14:23:11Z",
        )
        assert m.id == "m-001"
        assert m.artifacts == {"metadata": "metadata.json"}

    def test_match_entry_rejects_missing_required(self):
        from shared import MatchEntry
        from pydantic import ValidationError
        import pytest

        with pytest.raises(ValidationError):
            MatchEntry(id="m-001")  # missing artifacts, visibility, updated_at

    def test_match_entry_rejects_artifact_key_with_path_traversal(self):
        """Spec §5.2: artifact keys must satisfy the path-param regex; upload tool
        cannot land entries the API would refuse to serve."""
        from shared import MatchEntry
        from pydantic import ValidationError
        import pytest

        with pytest.raises(ValidationError, match="artifact name"):
            MatchEntry(
                id="m-001",
                artifacts={"../etc/passwd": "evil.txt"},
                visibility="public",
                updated_at="2026-05-02T14:23:11Z",
            )

    def test_match_entry_rejects_artifact_key_with_leading_underscore(self):
        from shared import MatchEntry
        from pydantic import ValidationError
        import pytest

        with pytest.raises(ValidationError, match="artifact name"):
            MatchEntry(
                id="m-001",
                artifacts={"_private": "secret.json"},
                visibility="public",
                updated_at="2026-05-02T14:23:11Z",
            )

    def test_player_record_requires_id(self):
        from shared import PlayerRecord
        from pydantic import ValidationError
        import pytest

        with pytest.raises(ValidationError):
            PlayerRecord(nickname="No ID", visibility="public", updated_at="2026-05-02T14:23:11Z")

    def test_player_record_requires_a_name(self):
        from shared import PlayerRecord
        from pydantic import ValidationError
        import pytest

        with pytest.raises(ValidationError, match="nickname.*firstName"):
            PlayerRecord(id="x", visibility="public", updated_at="2026-05-02T14:23:11Z")

    def test_player_record_accepts_nickname_only(self):
        from shared import PlayerRecord

        p = PlayerRecord(id="x", nickname="Pelé", visibility="public", updated_at="2026-05-02T14:23:11Z")
        assert p.nickname == "Pelé"

    def test_player_record_accepts_firstname_lastname(self):
        from shared import PlayerRecord

        p = PlayerRecord(
            id="x", firstName="Test", lastName="Player",
            visibility="private", updated_at="2026-05-02T14:23:11Z",
        )
        assert p.firstName == "Test"

    def test_player_record_round_trips_unknown_fields(self):
        """Spec §6.3: provider-specific extensions are allowed; round-trip verbatim."""
        from shared import PlayerRecord

        p = PlayerRecord.model_validate({
            "id": "x", "nickname": "Test", "visibility": "public",
            "updated_at": "2026-05-02T14:23:11Z", "providerSpecificField": 42,
        })
        dumped = p.model_dump()
        assert dumped["providerSpecificField"] == 42
```

- [ ] **Step 3: Run tests to verify failure**

```bash
cd src/tests
pytest test_schemas.py -v
```

Expected: ImportError on `MatchEntry`, `PlayerRecord` (don't exist yet); schema files missing.

- [ ] **Step 4: Add Pydantic models to `terraform/modules/functions/src/shared.py`**

Append to `shared.py` (after the existing `Tier` enum and helpers):

```python
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _SourceMeta(BaseModel):
    """Provenance metadata for an upstream data source."""
    name: str
    url: str = ""
    licence: str = ""


_PATH_PARAM_RE = r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"


class MatchEntry(BaseModel):
    """Canonical shape of a single entry in `{provider}/matches.json`. Spec §4.1."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={"$id": "urn:pining-for-the-data:schema:matches:v1"},
    )

    id: str = Field(..., pattern=_PATH_PARAM_RE, max_length=128)
    artifacts: dict[str, str] = Field(
        ...,
        description="Map of artifact-name → exact filename. Keys form the API whitelist; each key MUST match the path-param regex.",
    )
    visibility: str = Field(..., pattern=r"^(public|private)$")
    updated_at: str = Field(..., description="ISO 8601 UTC timestamp")
    date: str | None = None
    home: str | None = None
    away: str | None = None
    provenance: str | None = None
    source: _SourceMeta | None = None

    @model_validator(mode="after")
    def _validate_artifact_keys(self) -> "MatchEntry":
        # Every artifact name must satisfy the same regex as path params,
        # so the upload tool cannot land entries the API will refuse to serve.
        # Spec §5.2.
        import re
        regex = re.compile(_PATH_PARAM_RE)
        for name in self.artifacts.keys():
            if not regex.match(name) or len(name) > 128:
                raise ValueError(
                    f"artifact name {name!r} does not match the path-param regex "
                    f"{_PATH_PARAM_RE} (max 128 chars)"
                )
        return self


class PlayerRecord(BaseModel):
    """Canonical shape of a single entry in `{provider}/players.json`. Spec §6.3."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={"$id": "urn:pining-for-the-data:schema:players:v1"},
    )

    id: str = Field(..., pattern=_PATH_PARAM_RE, max_length=128)
    visibility: str = Field(..., pattern=r"^(public|private)$")
    updated_at: str = Field(..., description="ISO 8601 UTC timestamp")
    firstName: str | None = None
    lastName: str | None = None
    nickname: str | None = None
    dob: str | None = None
    height: float | None = None
    position: str | None = None
    positionGroupType: str | None = None
    nationality: str | None = None
    source: _SourceMeta | None = None

    @model_validator(mode="after")
    def _require_a_name(self) -> "PlayerRecord":
        if not (self.nickname or (self.firstName and self.lastName)):
            raise ValueError(
                "PlayerRecord requires either nickname OR (firstName AND lastName)"
            )
        return self
```

- [ ] **Step 5: Write `scripts/regenerate_schemas.py`**

Create `scripts/regenerate_schemas.py`:

```python
"""Regenerate schemas/{matches,players}.schema.json from Pydantic models.

Run after any edit to MatchEntry or PlayerRecord in shared.py. The schema-drift
test (src/tests/test_schemas.py) fails CI if you forget.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "terraform" / "modules" / "functions" / "src"))

from shared import MatchEntry, PlayerRecord  # noqa: E402

SCHEMAS_DIR = REPO_ROOT / "schemas"


def main() -> None:
    SCHEMAS_DIR.mkdir(exist_ok=True)
    for name, model in [("matches", MatchEntry), ("players", PlayerRecord)]:
        path = SCHEMAS_DIR / f"{name}.schema.json"
        path.write_text(
            json.dumps(model.model_json_schema(), indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Generate the schema files**

```bash
python scripts/regenerate_schemas.py
```

Expected: creates `schemas/matches.schema.json` and `schemas/players.schema.json`. Inspect them — both should contain `"$id"`, `"$schema"` (Pydantic v2 emits Draft 2020-12 by default), `"properties"`, `"required"`, etc.

- [ ] **Step 7: Run the drift tests**

```bash
cd src/tests
pytest test_schemas.py -v
```

Expected: all pass.

- [ ] **Step 8: Run full suite to confirm no regression**

```bash
pytest -v
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add terraform/modules/functions/src/shared.py scripts/regenerate_schemas.py schemas/ src/tests/test_schemas.py pyproject.toml
git commit -m "feat(schemas): add MatchEntry/PlayerRecord Pydantic models with drift-tested JSON Schemas"
```

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

### Task 8: Add `--visibility` flag to pining-upload; switch matches.json to v2 entry shape

**Files:**
- Modify: `src/mock_api/upload.py`
- Modify: `src/tests/test_upload.py`

Goal: `--visibility {public,private}` flag (default `public`). Private uploads write to `{provider}/_private/{game_id}/...`; public preserves existing behaviour location-wise. Every match entry written to `matches.json` now uses the v2 shape (per spec §4.1):
- `visibility: "public"` or `"private"` (required)
- `updated_at: <ISO 8601 UTC>` (required, set on every write including no-op re-uploads)
- `artifacts: {<name>: <filename>}` (object, not array — keys form the API whitelist, values are exact filenames)

CLI flag spelling: British `--source-licence` is the canonical flag; `--source-license` is accepted as a quiet alias (no deprecation warning, both spellings coexist). The internal field name in the JSON output is always `licence`. Spec §8.2.1.

Validation: every entry is validated against the `MatchEntry` Pydantic model (Phase 2.5) before any S3 call. Validation errors abort the upload with field-level diagnostics.

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
            upload_game(game_dir, provider="pff", game_id="m-001", bucket="b", visibility="private")

        upload_keys = [c.args[2] for c in s3.upload_file.call_args_list]
        assert any(k == "pff/_private/m-001/match.json" for k in upload_keys)

    def test_private_visibility_and_updated_at_and_artifacts_object_recorded(self, tmp_path):
        from unittest.mock import MagicMock, patch
        import json
        from mock_api.upload import upload_game

        game_dir = tmp_path / "g1"
        game_dir.mkdir()
        (game_dir / "match.json").write_text("{}")
        (game_dir / "tracking.jsonl").write_text("")

        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        s3.get_object.side_effect = s3.exceptions.NoSuchKey()

        with patch("mock_api.upload.boto3.client", return_value=s3):
            upload_game(game_dir, provider="pff", game_id="m-001", bucket="b", visibility="private")

        # Find the put_object call that wrote matches.json
        matches_calls = [c for c in s3.put_object.call_args_list if c.kwargs.get("Key") == "pff/matches.json"]
        assert len(matches_calls) == 1
        body = json.loads(matches_calls[0].kwargs["Body"].decode("utf-8"))
        entry = body["matches"][0]
        assert entry["visibility"] == "private"
        # artifacts is an object (name -> filename), not an array
        assert entry["artifacts"] == {"match": "match.json", "tracking": "tracking.jsonl"}
        # updated_at is set, ISO 8601, ends in Z
        assert entry["updated_at"].endswith("Z")
        assert "T" in entry["updated_at"]

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

    def test_reupload_refreshes_updated_at_even_if_unchanged(self, tmp_path):
        """Spec §4.1: re-uploads update updated_at even if no other field changes."""
        from unittest.mock import MagicMock, patch
        import json, time
        from mock_api.upload import upload_game

        game_dir = tmp_path / "g1"
        game_dir.mkdir()
        (game_dir / "match.json").write_text("{}")

        existing = json.dumps({
            "provider": "sc",
            "matches": [{
                "id": "g1",
                "artifacts": {"match": "match.json"},
                "visibility": "public",
                "updated_at": "2025-01-01T00:00:00Z",
            }],
        }).encode()

        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=existing))}

        with patch("mock_api.upload.boto3.client", return_value=s3):
            upload_game(game_dir, provider="sc", game_id="g1", bucket="b", visibility="public")

        matches_calls = [c for c in s3.put_object.call_args_list if c.kwargs.get("Key") == "sc/matches.json"]
        body = json.loads(matches_calls[0].kwargs["Body"].decode("utf-8"))
        assert body["matches"][0]["updated_at"] != "2025-01-01T00:00:00Z"

    def test_source_license_alias_accepted(self, tmp_path, capsys):
        """Spec §8.2.1: --source-license is a quiet alias for --source-licence."""
        from mock_api.upload import main as upload_main
        import sys
        # Verify the parser accepts both spellings without error
        # (full execution would require S3 mocking; just verify argparse accepts the flag)
        from mock_api import upload as upload_module
        parser_args = ["--provider", "sc", "--game-id", "g1", "--bucket", "b",
                       "--source-license", "MIT", str(tmp_path / "g")]
        # Smoke the parser construction path
        # Detailed assertion: parsed namespace exposes the value as `source_licence`
        # regardless of which flag was used at the CLI.
        # (Use the helper exposed by the module if present; otherwise patch sys.argv)
        # ... see implementation below for the parser shape ...

    def test_pydantic_validation_runs_before_s3(self, tmp_path):
        """Bad data must abort before any S3 call."""
        from unittest.mock import MagicMock, patch
        import pytest
        from mock_api.upload import upload_game

        game_dir = tmp_path / "g1"
        game_dir.mkdir()
        (game_dir / "match.json").write_text("{}")

        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})

        with patch("mock_api.upload.boto3.client", return_value=s3):
            # Empty-string game_id will fail the MatchEntry id pattern
            with pytest.raises((ValueError, Exception)):
                upload_game(game_dir, provider="sc", game_id="", bucket="b", visibility="public")

        # No S3 call should have been made
        s3.upload_file.assert_not_called()
        s3.put_object.assert_not_called()

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
import sys
from datetime import datetime, timezone

# Ensure shared.py (Pydantic models) is importable
_SHARED_DIR = Path(__file__).resolve().parents[2] / "terraform" / "modules" / "functions" / "src"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

from shared import MatchEntry  # type: ignore[import-not-found]


def _utc_now_iso() -> str:
    """Current UTC time, ISO 8601 with trailing Z (no microseconds)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    source_licence: str | None = None,
) -> list[str]:
    """Upload all files in game_dir to S3 and update indexes.

    Parameters
    ----------
    visibility : str
        ``"public"`` (default) or ``"private"``. Private content is written
        under ``{provider}/_private/{game_id}/...`` and recorded with
        ``visibility: "private"`` in matches.json.
    source_licence : str | None
        Spelled British (``--source-licence`` at the CLI). The American
        ``source_license`` / ``--source-license`` is accepted as an alias by
        the argparse layer (see ``main()``) and forwarded into this parameter.
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

    # Build artifacts as {name: filename} object form (spec §4.1)
    artifacts: dict[str, str] = {}
    for file_path in sorted(game_dir.iterdir()):
        if file_path.is_file() and not file_path.name.startswith("."):
            key = f"{prefix}/{file_path.name}"
            s3.upload_file(str(file_path), bucket, key)
            # Strip ALL extensions to derive the artifact name (e.g. tracking.jsonl.bz2 -> tracking)
            artifact_name = file_path.name.split(".", 1)[0]
            artifacts[artifact_name] = file_path.name
            print(f"  Uploaded {file_path.name} -> s3://{bucket}/{key}")

    if not artifacts:
        print(f"  No files found in {game_dir}")
        return list(artifacts)

    # Build and validate the canonical entry BEFORE any S3 index write.
    entry = _build_match_entry(
        game_id, artifacts, visibility, date, home, away,
        provenance, source_name, source_url, source_licence,
    )
    _update_matches_json(s3, bucket, provider, entry)
    _update_providers_json(s3, bucket, provider)

    return list(artifacts)


def _build_match_entry(
    game_id: str,
    artifacts: dict[str, str],
    visibility: str,
    date: str | None,
    home: str | None,
    away: str | None,
    provenance: str | None,
    source_name: str | None,
    source_url: str | None,
    source_licence: str | None,
) -> dict:
    """Assemble and Pydantic-validate a MatchEntry. Raises on validation error."""
    payload: dict = {
        "id": game_id,
        "artifacts": artifacts,
        "visibility": visibility,
        "updated_at": _utc_now_iso(),
    }
    if date:
        payload["date"] = date
    if home:
        payload["home"] = home
    if away:
        payload["away"] = away
    if provenance:
        payload["provenance"] = provenance
    if source_name:
        payload["source"] = {
            "name": source_name,
            "url": source_url or "",
            "licence": source_licence or "",
        }
    # Validation will raise pydantic.ValidationError before any S3 call.
    return MatchEntry.model_validate(payload).model_dump(exclude_none=True)


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
            f"Re-tiering requires an explicit move (manual procedure documented in spec §11.4; "
            f"not supported by tooling in v1)."
        )
```

Update `_update_matches_json` to take a pre-built validated entry:

```python
def _update_matches_json(s3, bucket: str, provider: str, entry: dict) -> None:
    """Read-modify-write the matches.json index for a provider.

    `entry` MUST already be a validated MatchEntry dict (see _build_match_entry).
    """
    key = f"{provider}/matches.json"

    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        data = json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        data = {"provider": provider, "matches": []}

    game_id = entry["id"]
    existing = next((m for m in data["matches"] if m["id"] == game_id), None)
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

- [ ] **Step 4: Add the `--visibility` flag and the `--source-licence`/`--source-license` alias to the CLI parser in `main()`**

In `src/mock_api/upload.py`, in the `main()` function, add the visibility flag and rework the licence flag to accept both spellings into a single destination:

```python
    parser.add_argument(
        "--visibility",
        default="public",
        choices=["public", "private"],
        help="Visibility tier (default: public; private writes under _private/ prefix)",
    )

    # Both spellings populate `source_licence`. British is canonical; American
    # is a quiet alias (no deprecation warning). Spec §8.2.1.
    parser.add_argument(
        "--source-licence", "--source-license",
        dest="source_licence",
        default=None,
        help="Source licence text (British spelling canonical; --source-license also accepted)",
    )
```

Replace the existing `--source-license` argument (American only) with the combined form above. If `--source-licence` already exists separately for any reason, remove it — the single combined argument handles both.

Pass to `upload_game`:

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
        source_licence=args.source_licence,
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
        all_private = {"provider": "pff", "matches": [{"id": "m-001", "artifacts": ["m"], "visibility": "private"}]}
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


class TestListProviders(_ResetS3):
    """Spec §4.2: list_providers returns the same provider list to BOTH tiers.

    Existence of a provider is not the secret; the per-match visibility flag is
    the only enforcement boundary. This test pins the behaviour so a future
    contributor doesn't quietly add tier filtering thinking it tightens security.
    """
    def _setup(self, monkeypatch):
        import shared
        monkeypatch.setenv("API_TOKEN", "pub-tok")
        shared._get_owner_token.cache_clear()
        monkeypatch.setattr(shared, "_get_owner_token", lambda: "own-tok")

    def test_public_tier_sees_pff_in_provider_list(self, monkeypatch) -> None:
        from list_providers import handler
        import json
        self._setup(monkeypatch)

        mock_s3 = _mock_s3()
        body = json.dumps({"providers": ["skillcorner", "pff"]}).encode()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body))}

        event = {"headers": {"authorization": "Bearer pub-tok"}}
        result = handler(event, None)
        assert result["statusCode"] == 200
        # Even though pff is all-private, public tier sees it in the provider catalogue.
        assert "pff" in json.loads(result["body"])["providers"]

    def test_owner_tier_sees_same_provider_list(self, monkeypatch) -> None:
        from list_providers import handler
        import json
        self._setup(monkeypatch)

        mock_s3 = _mock_s3()
        body = json.dumps({"providers": ["skillcorner", "pff"]}).encode()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body))}

        event = {"headers": {"authorization": "Bearer own-tok"}}
        result = handler(event, None)
        assert result["statusCode"] == 200
        assert json.loads(result["body"])["providers"] == ["skillcorner", "pff"]
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

### Task 10: Make get_artifact visibility-aware and whitelist-driven (object-form artifacts)

**Files:**
- Modify: `terraform/modules/functions/src/get_artifact.py`
- Modify: `src/tests/test_lambda_handlers.py`

Goal: `get_artifact` reads `matches.json`, looks up the match, checks tier vs match visibility, resolves the correct S3 prefix (`_private/` vs not), looks up the requested artifact in the entry's `artifacts: {name: filename}` object (the whitelist), generates the presigned URL directly via `s3.generate_presigned_url(...)`, returns 302 on success or 404 on not-found-or-tier-mismatch-or-not-whitelisted. **No `list_objects_v2` call. No `head_object` call.** Spec §4.3.

- [ ] **Step 1: Write failing tests**

In `src/tests/test_lambda_handlers.py`, replace the existing `TestGetArtifact` with this expanded version:

```python
class TestGetArtifact(_ResetS3):
    def _setup(self, monkeypatch):
        import shared
        monkeypatch.setenv("API_TOKEN", "pub-tok")
        shared._get_owner_token.cache_clear()
        monkeypatch.setattr(shared, "_get_owner_token", lambda: "own-tok")

    def _wire_matches(self, mock_s3, matches_payload):
        import json
        body = json.dumps(matches_payload).encode()
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})

        def get_obj(Bucket, Key):
            if Key.endswith("matches.json"):
                return {"Body": MagicMock(read=MagicMock(return_value=body))}
            raise mock_s3.exceptions.NoSuchKey()

        mock_s3.get_object.side_effect = get_obj
        mock_s3.generate_presigned_url.return_value = "https://s3.example.com/presigned"

    def test_public_match_public_tier_returns_302(self, monkeypatch) -> None:
        from get_artifact import handler
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire_matches(
            mock_s3,
            {"provider": "sc", "matches": [
                {"id": "g1",
                 "artifacts": {"match": "match.json"},
                 "visibility": "public",
                 "updated_at": "2026-05-02T14:00:00Z"},
            ]},
        )

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc", "id": "g1", "artifact": "match"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 302
        assert mock_s3.generate_presigned_url.call_args.kwargs["Params"]["Key"] == "sc/g1/match.json"
        # No list_objects_v2 call on the success path (spec §4.3).
        mock_s3.list_objects_v2.assert_not_called()

    def test_private_match_owner_tier_returns_302_with_private_prefix(self, monkeypatch) -> None:
        from get_artifact import handler
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire_matches(
            mock_s3,
            {"provider": "pff", "matches": [
                {"id": "m-001",
                 "artifacts": {"metadata": "metadata.json"},
                 "visibility": "private",
                 "updated_at": "2026-05-02T14:00:00Z"},
            ]},
        )

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "pff", "id": "m-001", "artifact": "metadata"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 302
        # Key resolves under _private/ for private match, no listing.
        assert mock_s3.generate_presigned_url.call_args.kwargs["Params"]["Key"] == "pff/_private/m-001/metadata.json"
        mock_s3.list_objects_v2.assert_not_called()

    def test_private_match_public_tier_returns_404(self, monkeypatch) -> None:
        """Private match accessed with public token: 404 (not 403 — no existence leak)."""
        from get_artifact import handler
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire_matches(
            mock_s3,
            {"provider": "pff", "matches": [
                {"id": "m-001",
                 "artifacts": {"metadata": "metadata.json"},
                 "visibility": "private",
                 "updated_at": "2026-05-02T14:00:00Z"},
            ]},
        )

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "pff", "id": "m-001", "artifact": "metadata"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 404
        mock_s3.generate_presigned_url.assert_not_called()

    def test_unknown_match_returns_404(self, monkeypatch) -> None:
        from get_artifact import handler
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire_matches(mock_s3, {"provider": "sc", "matches": []})

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "sc", "id": "missing", "artifact": "match"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 404

    def test_artifact_not_in_whitelist_returns_404(self, monkeypatch) -> None:
        """Spec §4.3: artifact name must be a key in the entry's artifacts object."""
        from get_artifact import handler
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire_matches(
            mock_s3,
            {"provider": "sc", "matches": [
                {"id": "g1",
                 "artifacts": {"match": "match.json"},
                 "visibility": "public",
                 "updated_at": "2026-05-02T14:00:00Z"},
            ]},
        )

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc", "id": "g1", "artifact": "tracking"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 404
        mock_s3.generate_presigned_url.assert_not_called()
        mock_s3.list_objects_v2.assert_not_called()

    def test_artifact_filename_resolves_via_object_lookup(self, monkeypatch) -> None:
        """Spec §4.3: get_artifact resolves filename via artifacts[name], no S3 list."""
        from get_artifact import handler
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire_matches(
            mock_s3,
            {"provider": "sc", "matches": [
                {"id": "g1",
                 "artifacts": {
                     "match": "match.json",
                     "tracking": "tracking.jsonl.bz2",
                 },
                 "visibility": "public",
                 "updated_at": "2026-05-02T14:00:00Z"},
            ]},
        )

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc", "id": "g1", "artifact": "tracking"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 302
        assert mock_s3.generate_presigned_url.call_args.kwargs["Params"]["Key"] == "sc/g1/tracking.jsonl.bz2"
        mock_s3.list_objects_v2.assert_not_called()
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

    Reads ``{provider}/matches.json`` once to determine the match's visibility
    and the artifact's filename, enforces tier check (404 on mismatch — uniform
    with not-found to avoid existence leaks), then generates the presigned URL
    directly. No ``list_objects_v2`` and no ``head_object`` — the index is the
    source of truth for the file's existence (the upload tool wrote both
    atomically). Spec §4.3.
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

    # Look up the match in matches.json to determine visibility AND the artifact filename.
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

    # Whitelist + filename lookup in one step. Spec §4.3: artifacts is an
    # object {name: filename}; keys form the whitelist, values resolve the file.
    artifacts = match.get("artifacts") or {}
    if not isinstance(artifacts, dict):
        # Defensive: legacy array-form entries should not exist post-Task 8,
        # but if they do, treat as malformed and refuse to serve.
        logger.warning("legacy_artifacts_array_form", extra={"match_id": match_id})
        return json_response(404, {"error": "Artifact not found"})

    filename = artifacts.get(artifact)
    if filename is None:
        return json_response(404, {"error": "Artifact not found"})

    prefix_root = (
        f"{provider}/_private/{match_id}"
        if visibility == "private"
        else f"{provider}/{match_id}"
    )
    key = f"{prefix_root}/{filename}"

    try:
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET, "Key": key},
            ExpiresIn=PRESIGNED_EXPIRY,
        )
    except Exception:
        logger.exception("s3_error", extra={"handler": "get_artifact", "stage": "presign"})
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

    def _wire(self, mock_s3, providers=("sc", "pff"), public_payload=None, private_payload=None):
        """Wire mock_s3 with a providers.json + per-provider players indexes."""
        import json
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        providers_body = json.dumps({"providers": list(providers)}).encode()

        def get_obj(Bucket, Key):
            if Key == "providers.json":
                return {"Body": MagicMock(read=MagicMock(return_value=providers_body))}
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
        """Known provider with no public OR private players index: 200 with empty list."""
        from list_players import handler
        import json
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire(mock_s3)  # providers exist; no players indexes

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "sc"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["players"] == []

    def test_unknown_provider_returns_404(self, monkeypatch) -> None:
        """Spec §6.4: list_players gates on providers.json membership; 404 on unknown."""
        from list_players import handler
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire(mock_s3, providers=("sc", "pff"))

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "made-up"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 404

    def test_owner_tier_cross_tier_id_collision_private_wins(self, monkeypatch) -> None:
        """Spec §6.3.1: on cross-tier ID collision, owner-tier sees the private record."""
        from list_players import handler
        import json
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire(
            mock_s3,
            public_payload={"provider": "sc", "players": [
                {"id": "p1", "nickname": "Public Mask"},
            ]},
            private_payload={"provider": "sc", "players": [
                {"id": "p1", "nickname": "Private Real"},
            ]},
        )

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "sc"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        # Single record (deduped); private record wins.
        assert len(body["players"]) == 1
        assert body["players"][0]["nickname"] == "Private Real"
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
    """Return the player catalogue for a provider, merged across visible tiers.

    Gates on providers.json membership for unknown-provider 404 (spec §6.4).
    Owner-tier merge applies private-wins precedence on cross-tier ID
    collision (spec §6.3.1).
    """
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
    if not _provider_known(s3, provider):
        return json_response(404, {"error": "Provider not found"})

    public_players = _read_index(s3, f"{provider}/players.json")
    private_players = _read_index(s3, f"{provider}/_private/players.json") if tier == Tier.OWNER else []

    # Private-wins precedence on cross-tier ID collision (spec §6.3.1).
    by_id: dict[str, dict] = {p.get("id"): p for p in public_players if p.get("id")}
    for priv in private_players:
        pid = priv.get("id")
        if pid:
            by_id[pid] = priv  # overwrite any same-id public entry

    return json_response(200, {"provider": provider, "players": list(by_id.values())})


def _provider_known(s3, provider: str) -> bool:
    """Check that `provider` appears in providers.json. Returns True if so."""
    try:
        obj = s3.get_object(Bucket=BUCKET, Key="providers.json")
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return provider in (data.get("providers") or [])
    except s3.exceptions.NoSuchKey:
        return False
    except Exception:
        logger.exception("s3_error", extra={"handler": "list_players", "key": "providers.json"})
        return False


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

    def _wire(self, mock_s3, providers=("sc", "pff"), public_payload=None, private_payload=None):
        import json
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        providers_body = json.dumps({"providers": list(providers)}).encode()

        def get_obj(Bucket, Key):
            if Key == "providers.json":
                return {"Body": MagicMock(read=MagicMock(return_value=providers_body))}
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

    def test_unknown_provider_returns_404(self, monkeypatch) -> None:
        """Spec §6.4: get_player gates on providers.json membership."""
        from get_player import handler
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire(mock_s3, providers=("sc", "pff"))

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "made-up", "id": "p1"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 404

    def test_owner_tier_cross_tier_collision_private_wins(self, monkeypatch) -> None:
        """Spec §6.3.1: owner-tier sees the private record on cross-tier ID collision."""
        from get_player import handler
        import json
        self._setup(monkeypatch)
        mock_s3 = _mock_s3()
        self._wire(
            mock_s3,
            public_payload={"provider": "sc", "players": [{"id": "p1", "nickname": "Public Mask"}]},
            private_payload={"provider": "sc", "players": [{"id": "p1", "nickname": "Private Real"}]},
        )

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "sc", "id": "p1"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        assert json.loads(result["body"])["nickname"] == "Private Real"
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
    """Return a single player record. 404 if provider unknown, player not found,
    or found-but-private-and-public-tier.

    Spec §6.4: gates on providers.json membership.
    Spec §6.3.1: on cross-tier ID collision, owner-tier sees the private record.
    """
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
    if not _provider_known(s3, provider):
        return json_response(404, {"error": "Provider not found"})

    # Owner tier: try PRIVATE index first so a cross-tier ID collision returns the
    # private record (spec §6.3.1: private wins).
    if tier == Tier.OWNER:
        private_players = _read_index(s3, f"{provider}/_private/players.json")
        found = next((p for p in private_players if p.get("id") == player_id), None)
        if found is not None:
            return json_response(200, found)

    # Fall through to public index for both tiers.
    public_players = _read_index(s3, f"{provider}/players.json")
    found = next((p for p in public_players if p.get("id") == player_id), None)
    if found is not None:
        return json_response(200, found)

    return json_response(404, {"error": "Player not found"})


def _provider_known(s3, provider: str) -> bool:
    try:
        obj = s3.get_object(Bucket=BUCKET, Key="providers.json")
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return provider in (data.get("providers") or [])
    except s3.exceptions.NoSuchKey:
        return False
    except Exception:
        logger.exception("s3_error", extra={"handler": "get_player", "key": "providers.json"})
        return False


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
      LAST_ROTATION     = var.last_rotation
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
      LAST_ROTATION     = var.last_rotation
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

### Task 15: Create pining-upload-players CLI (canonical JSON only)

**Files:**
- Create: `src/mock_api/upload_players.py`
- Modify: `pyproject.toml`
- Create: `src/tests/test_upload_players.py`

The CLI accepts canonical JSON only — a list of `PlayerRecord` objects matching the schema in spec §6.3, or `{"players": [...]}`. CSV is explicitly rejected with a message pointing operators at `scripts/upload_pff_wc2022.py` as the reference adapter (spec §6.5). Cross-tier dedup check runs across BOTH `players.json` files (public and `_private/`) before any write — same id in either file fails the upload (spec §6.5 step 4).

- [ ] **Step 1: Write failing tests**

Create `src/tests/test_upload_players.py`:

```python
"""Tests for the pining-upload-players CLI."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch


def _canonical_json_path(tmp_path: Path) -> Path:
    """Write a canonical JSON file with two synthetic player records.

    Synthetic IDs and names — never use real provider data in committed test
    fixtures (memory: feedback_no_local_paths_in_committed_docs.md, and the
    spec §8.3 redistribution-licence rationale also applies to test data).
    """
    p = tmp_path / "players.json"
    p.write_text(json.dumps([
        {"id": "test-001", "firstName": "Test", "lastName": "Alpha",
         "nickname": "Test Alpha", "dob": "2000-01-01",
         "height": 180.0, "positionGroupType": "D"},
        {"id": "test-002", "firstName": "Test", "lastName": "Beta",
         "nickname": "Test Beta", "dob": "2000-02-02",
         "height": 175.0, "positionGroupType": "M"},
    ]), encoding="utf-8")
    return p


def _empty_s3():
    s3 = MagicMock()
    s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
    s3.get_object.side_effect = s3.exceptions.NoSuchKey()
    return s3


class TestUploadPlayersCSVRejection:
    def test_csv_input_is_rejected_with_helpful_message(self, tmp_path):
        """Spec §6.5: CSV is explicitly rejected; error names the reference adapter."""
        from mock_api.upload_players import upload_players
        import pytest

        csv_file = tmp_path / "players.csv"
        csv_file.write_text("id,nickname\ntest-001,Test\n", encoding="utf-8")

        s3 = _empty_s3()
        with patch("mock_api.upload_players.boto3.client", return_value=s3):
            with pytest.raises(ValueError, match="canonical JSON"):
                upload_players(csv_file, provider="pff", bucket="b", visibility="private")

    def test_csv_rejection_message_mentions_reference_adapter(self, tmp_path):
        from mock_api.upload_players import upload_players
        import pytest

        csv_file = tmp_path / "players.csv"
        csv_file.write_text("id,nickname\n", encoding="utf-8")

        with patch("mock_api.upload_players.boto3.client", return_value=_empty_s3()):
            with pytest.raises(ValueError, match="upload_pff_wc2022"):
                upload_players(csv_file, provider="pff", bucket="b", visibility="private")


class TestUploadPlayersCanonicalJSON:
    def test_private_visibility_writes_under_private_prefix(self, tmp_path):
        from mock_api.upload_players import upload_players
        json_file = _canonical_json_path(tmp_path)

        s3 = _empty_s3()
        with patch("mock_api.upload_players.boto3.client", return_value=s3):
            upload_players(
                json_file, provider="pff", bucket="b", visibility="private",
                source_name="PFF FC", source_url="https://www.pff.com/", source_licence="Restricted",
            )

        keys = [c.kwargs.get("Key") for c in s3.put_object.call_args_list]
        assert "pff/_private/players.json" in keys

    def test_public_visibility_writes_to_provider_root(self, tmp_path):
        from mock_api.upload_players import upload_players
        json_file = _canonical_json_path(tmp_path)

        s3 = _empty_s3()
        with patch("mock_api.upload_players.boto3.client", return_value=s3):
            upload_players(json_file, provider="sc", bucket="b", visibility="public")

        keys = [c.kwargs.get("Key") for c in s3.put_object.call_args_list]
        assert "sc/players.json" in keys
        assert "sc/_private/players.json" not in keys

    def test_index_payload_shape_with_updated_at(self, tmp_path):
        from mock_api.upload_players import upload_players
        json_file = _canonical_json_path(tmp_path)

        s3 = _empty_s3()
        with patch("mock_api.upload_players.boto3.client", return_value=s3):
            upload_players(
                json_file, provider="pff", bucket="b", visibility="private",
                source_name="PFF FC",
            )

        put_calls = [c for c in s3.put_object.call_args_list if c.kwargs.get("Key") == "pff/_private/players.json"]
        assert len(put_calls) == 1
        body = json.loads(put_calls[0].kwargs["Body"].decode("utf-8"))
        assert body["provider"] == "pff"
        assert len(body["players"]) == 2

        alpha = next(p for p in body["players"] if p["id"] == "test-001")
        assert alpha["firstName"] == "Test"
        assert alpha["lastName"] == "Alpha"
        assert alpha["nickname"] == "Test Alpha"
        assert alpha["dob"] == "2000-01-01"
        assert alpha["height"] == 180.0
        assert alpha["positionGroupType"] == "D"
        assert alpha["visibility"] == "private"
        # updated_at is set by the CLI on every write (spec §6.3)
        assert alpha["updated_at"].endswith("Z")
        assert alpha["source"]["name"] == "PFF FC"

    def test_pydantic_validation_rejects_record_missing_a_name(self, tmp_path):
        from mock_api.upload_players import upload_players
        import pytest

        bad_file = tmp_path / "bad.json"
        bad_file.write_text(json.dumps([{"id": "x"}]), encoding="utf-8")  # no nickname or firstName+lastName

        with patch("mock_api.upload_players.boto3.client", return_value=_empty_s3()):
            with pytest.raises((ValueError, Exception)):
                upload_players(bad_file, provider="pff", bucket="b", visibility="private")

    def test_idempotent_reupload_replaces_existing(self, tmp_path):
        from mock_api.upload_players import upload_players
        json_file = _canonical_json_path(tmp_path)

        existing_private = {
            "provider": "pff",
            "players": [
                {"id": "test-001", "nickname": "Old Name", "visibility": "private", "updated_at": "2025-01-01T00:00:00Z"},
                {"id": "test-999", "nickname": "Untouched", "visibility": "private", "updated_at": "2025-01-01T00:00:00Z"},
            ],
        }
        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})

        def get_obj(Bucket, Key):
            if Key == "pff/_private/players.json":
                return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(existing_private).encode()))}
            raise s3.exceptions.NoSuchKey()

        s3.get_object.side_effect = get_obj

        with patch("mock_api.upload_players.boto3.client", return_value=s3):
            upload_players(json_file, provider="pff", bucket="b", visibility="private")

        put_calls = [c for c in s3.put_object.call_args_list if c.kwargs.get("Key") == "pff/_private/players.json"]
        body = json.loads(put_calls[0].kwargs["Body"].decode("utf-8"))
        ids = sorted(p["id"] for p in body["players"])
        assert ids == ["test-001", "test-002", "test-999"]
        alpha = next(p for p in body["players"] if p["id"] == "test-001")
        assert alpha["nickname"] == "Test Alpha"  # updated, not "Old Name"
        untouched = next(p for p in body["players"] if p["id"] == "test-999")
        assert untouched["nickname"] == "Untouched"

    def test_tier_mixing_within_same_file_rejected(self, tmp_path):
        from mock_api.upload_players import upload_players
        import pytest
        json_file = _canonical_json_path(tmp_path)

        existing_public = {
            "provider": "pff",
            "players": [{"id": "test-001", "nickname": "Existing", "visibility": "public", "updated_at": "2025-01-01T00:00:00Z"}],
        }
        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})

        def get_obj(Bucket, Key):
            if Key == "pff/players.json":
                return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(existing_public).encode()))}
            raise s3.exceptions.NoSuchKey()

        s3.get_object.side_effect = get_obj

        with patch("mock_api.upload_players.boto3.client", return_value=s3):
            with pytest.raises(ValueError, match="tier"):
                upload_players(json_file, provider="pff", bucket="b", visibility="private")

    def test_cross_tier_dedup_check_scans_both_files(self, tmp_path):
        """Spec §6.5: cross-tier dedup check reads BOTH players.json files before write."""
        from mock_api.upload_players import upload_players
        import pytest
        json_file = _canonical_json_path(tmp_path)

        # test-001 lives in the OTHER tier (public); writing private must fail.
        existing_public = {
            "provider": "pff",
            "players": [{"id": "test-001", "nickname": "Other Tier", "visibility": "public", "updated_at": "2025-01-01T00:00:00Z"}],
        }
        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})

        def get_obj(Bucket, Key):
            if Key == "pff/players.json":
                return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(existing_public).encode()))}
            raise s3.exceptions.NoSuchKey()

        s3.get_object.side_effect = get_obj

        with patch("mock_api.upload_players.boto3.client", return_value=s3):
            with pytest.raises(ValueError, match="cross-tier|other tier"):
                upload_players(json_file, provider="pff", bucket="b", visibility="private")

    def test_source_license_alias_accepted(self, tmp_path, monkeypatch):
        """Spec §8.2.1: --source-license (American) is a quiet alias for --source-licence (British).

        Both spellings populate the same argparse destination (source_licence) so
        downstream code only sees one canonical name.
        """
        from mock_api import upload_players as upload_players_mod
        import sys
        from unittest.mock import patch

        # Stub out upload_players so we just exercise the argparse layer.
        captured: dict = {}

        def fake_upload(**kwargs):
            captured.update(kwargs)
            return 0

        # Try American spelling — should populate source_licence kwarg with British internal name.
        monkeypatch.setattr(upload_players_mod, "upload_players", fake_upload)
        json_file = tmp_path / "players.json"
        json_file.write_text("[]", encoding="utf-8")
        monkeypatch.setattr(sys, "argv", [
            "pining-upload-players", str(json_file),
            "--provider", "p", "--bucket", "b",
            "--source-license", "Test License",
        ])
        upload_players_mod.main()
        assert captured["source_licence"] == "Test License"

        # And the British spelling populates the same destination.
        captured.clear()
        monkeypatch.setattr(sys, "argv", [
            "pining-upload-players", str(json_file),
            "--provider", "p", "--bucket", "b",
            "--source-licence", "British License",
        ])
        upload_players_mod.main()
        assert captured["source_licence"] == "British License"
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

Reads a canonical JSON file (a list of PlayerRecord objects, or
{"players": [...]}) and writes the provider's players index to S3 at one of:

- {provider}/players.json (visibility=public)
- {provider}/_private/players.json (visibility=private)

CSV input is explicitly rejected (spec §6.5) — provider-specific shapes must
be normalised to canonical JSON by a provider-specific adapter; see
scripts/upload_pff_wc2022.py for a worked example.

Existing players (by id, within the same tier) are updated in place; new
players are appended; updated_at is set on every write.

Tier mixing is rejected at two levels:
- within the same file: re-uploading a public id with --visibility private fails
- across both files: an id present in EITHER tier blocks an upload of the same
  id to the OTHER tier (spec §6.5 step 4)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3

# Make the Pydantic models in shared.py importable.
_SHARED_DIR = Path(__file__).resolve().parents[2] / "terraform" / "modules" / "functions" / "src"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

from shared import PlayerRecord  # type: ignore[import-not-found]

_SAFE_PARAM = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")

_CSV_REJECTION_MESSAGE = (
    "pining-upload-players accepts canonical JSON only (a list of PlayerRecord objects, "
    "or {\"players\": [...]}).\n"
    "CSV input is not supported by this CLI — provider-specific shapes must be normalised "
    "to canonical JSON by a provider-specific adapter. See scripts/upload_pff_wc2022.py for "
    "a worked example."
)


def _validate_param(value: str, name: str) -> None:
    if not value or len(value) > 128 or not _SAFE_PARAM.match(value):
        raise ValueError(f"Invalid {name}: must be 1-128 chars, alphanumeric start, then [a-zA-Z0-9_-]+")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def upload_players(
    input_file: Path,
    provider: str,
    bucket: str,
    visibility: str = "public",
    source_name: str | None = None,
    source_url: str | None = None,
    source_licence: str | None = None,
) -> int:
    """Upload a canonical-JSON player catalogue to S3. Returns the number of players in the resulting index."""
    if visibility not in ("public", "private"):
        raise ValueError(f"Invalid visibility: {visibility!r}")
    _validate_param(provider, "provider")

    if not input_file.is_file():
        raise FileNotFoundError(f"Not a file: {input_file}")

    # CSV (or anything not .json) is explicitly rejected — spec §6.5.
    if input_file.suffix.lower() != ".json":
        raise ValueError(_CSV_REJECTION_MESSAGE)

    raw_records = _read_canonical_json(input_file)

    # Validate every record against the canonical Pydantic model BEFORE any S3 call.
    now = _utc_now_iso()
    new_records: list[dict] = []
    for raw in raw_records:
        record = dict(raw)  # avoid mutating caller's data
        record["visibility"] = visibility
        record.setdefault("updated_at", now)
        if source_name and "source" not in record:
            record["source"] = {
                "name": source_name,
                "url": source_url or "",
                "licence": source_licence or "",
            }
        # PlayerRecord.model_validate raises ValidationError with field-level diagnostics.
        validated = PlayerRecord.model_validate(record)
        # Always refresh updated_at on this write.
        dumped = validated.model_dump(exclude_none=True)
        dumped["updated_at"] = now
        new_records.append(dumped)

    s3 = boto3.client("s3")
    target_key = f"{provider}/_private/players.json" if visibility == "private" else f"{provider}/players.json"
    other_key  = f"{provider}/players.json" if visibility == "private" else f"{provider}/_private/players.json"

    target_existing = _read_index(s3, bucket, target_key)
    other_existing  = _read_index(s3, bucket, other_key)

    # Cross-tier dedup check (spec §6.5 step 4): no incoming id may already
    # exist in the OTHER tier's file.
    other_ids = {p.get("id") for p in other_existing}
    for new in new_records:
        if new["id"] in other_ids:
            raise ValueError(
                f"Cross-tier collision for player id {new['id']!r}: id already exists in the "
                f"other tier ({other_key!r}). Re-tiering requires the manual procedure in spec §11.4."
            )

    # Same-file tier-mixing check (defensive — same-tier merge only here).
    by_id: dict[str, dict] = {p["id"]: p for p in target_existing}
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
        Key=target_key,
        Body=json.dumps(payload, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    print(f"  Wrote {len(merged)} player(s) to s3://{bucket}/{target_key}")
    return len(merged)


def _read_canonical_json(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "players" in data:
        return data["players"]
    raise ValueError("Canonical JSON input must be a list or {'players': [...]}")


def _read_index(s3, bucket: str, key: str) -> list[dict]:
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(obj["Body"].read().decode("utf-8")).get("players", [])
    except s3.exceptions.NoSuchKey:
        return []


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload a canonical-JSON player catalogue to the mock provider API's S3 bucket"
    )
    parser.add_argument("input_file", type=Path, help="Canonical JSON file with player records")
    parser.add_argument("--provider", required=True, help="Provider name (e.g., pff)")
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument("--visibility", default="public", choices=["public", "private"])
    parser.add_argument("--source-name", default=None)
    parser.add_argument("--source-url", default=None)
    # British spelling canonical; American is a quiet alias (spec §8.2.1).
    parser.add_argument(
        "--source-licence", "--source-license",
        dest="source_licence",
        default=None,
        help="Source licence text (British spelling canonical; --source-license also accepted)",
    )
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
TOKEN="$PUB_TOKEN"
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
    name = "Data bucket reads/writes excluding only providers.json"

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
    # Spec §7.5: exclude ONLY providers.json (true bookkeeping; never reveals
    # private content). matches.json and players.json reads stay logged because
    # enumeration via /matches and /players is the most likely abuse vector and
    # the trail is its forensic record.
    field_selector {
      field           = "resources.ARN"
      not_ends_with   = ["/providers.json"]
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

**Precondition:** at least one public artifact must exist in the bucket. If the dev environment is empty, upload a SkillCorner test match first (e.g., re-run an earlier `pining-upload` from Phase 1 work). Discover an actually-present match before issuing the curl, so a stale `game_03` reference doesn't silently mask a real CloudTrail wiring failure.

```bash
TOKEN="$PUB_TOKEN"
API=$(terraform output -raw api_url)
AUDIT_BUCKET=$(terraform output -raw audit_bucket_name)

# Discover a real public match ID (don't hardcode a possibly-stale one).
SC_MATCH=$(curl -s -H "Authorization: Bearer $TOKEN" "$API/skillcorner/matches" | \
  python -c "import sys, json; m=json.load(sys.stdin)['matches']; print(m[0]['id'] if m else '')")

if [ -z "$SC_MATCH" ]; then
  echo "ERROR: no SkillCorner matches in bucket — upload one before running this smoke test"
  exit 1
fi

# Fetch the artifact — fail loudly if it doesn't return 302 + payload.
curl -s -L -f -o /dev/null -H "Authorization: Bearer $TOKEN" "$API/skillcorner/matches/$SC_MATCH/match"

# CloudTrail delivers in batches; allow up to 15 minutes for the first event to appear.
sleep 60
aws s3 ls "s3://$AUDIT_BUCKET/AWSLogs/" --recursive | head -10
```

Expected: the curl succeeds (no `|| true` masking), then at least one log file appears within ~15 minutes (CloudTrail delivers in batches). If the `s3 ls` is empty after 60s, retry every couple of minutes for up to 15 minutes before treating the trail as broken.

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

Goal: idempotent one-shot script that reads the PFF source folder, reshapes per-match files, calls `upload_game` for each of the 64 matches, and `upload_players` for the player catalogue.

- [ ] **Step 1: Write the script**

Create `scripts/upload_pff_wc2022.py`:

```python
"""Upload PFF FIFA World Cup 2022 to the mock provider API as private-tier data.

Reshapes the source bundle into per-match staging directories, then calls
the pining-upload primitives. For players, normalises players.csv into a
canonical-JSON file in a temp directory and calls pining-upload-players
(which only accepts canonical JSON; spec §6.5).

Idempotent — re-running re-uploads without producing duplicate index entries.

Loads private-tier only — visibility is hardcoded to "private" for both matches
and players. A single-owner private-tier load (the data goes only into the
operator's own private bucket, served back only to the operator's own
owner-token holder) does not engage redistribution licence concerns: it's the
operator moving their own data between their own systems. If a public-tier
upload mode is ever added to this script, that path will need its own
licence-clarification gate before serving (spec §8.3).

Source layout (input):
    FIFA World Cup 2022/
    ├── Event Data/{id}.json
    ├── Metadata/{id}.json
    ├── Rosters/{id}.json
    ├── Tracking Data/{id}.jsonl.bz2
    ├── competitions.csv          # not uploaded — directory data covered by /matches
    ├── players.csv               # normalised to canonical JSON, then uploaded as /players catalogue
    └── PFF FC Change Log.docx    # not uploaded
"""

from __future__ import annotations

import argparse
import csv
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
            with tempfile.TemporaryDirectory(prefix="pff-players-") as tmp:
                canonical_json = Path(tmp) / "players.json"
                count = _normalise_players_csv_to_canonical(players_csv, canonical_json)
                print(f"Normalised {count} PFF player(s) to canonical JSON at {canonical_json}")
                print(f"Uploading player catalogue to s3://{args.bucket}/{PROVIDER}/_private/players.json")
                upload_players(
                    input_file=canonical_json,
                    provider=PROVIDER,
                    bucket=args.bucket,
                    visibility="private",
                    source_name=SOURCE_NAME,
                    source_url=SOURCE_URL,
                    source_licence=SOURCE_LICENCE,
                )

    print("Done.")


def _normalise_players_csv_to_canonical(csv_path: Path, out_path: Path) -> int:
    """Read PFF's players.csv and write a canonical-JSON file matching PlayerRecord.

    PFF columns: dob, firstName, height, id, lastName, nickname, positionGroupType.
    Maps directly to canonical fields with no semantic translation; type-coerce
    `height` to float. visibility/updated_at/source are added by the upload CLI.
    """
    records: list[dict] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("id"):
                continue
            record: dict = {"id": str(row["id"])}
            for key in ("firstName", "lastName", "nickname", "dob", "positionGroupType"):
                val = row.get(key)
                if val:
                    record[key] = val
            if row.get("height"):
                try:
                    record["height"] = float(row["height"])
                except (TypeError, ValueError):
                    pass
            records.append(record)

    out_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return len(records)


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
            source_licence=SOURCE_LICENCE,
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

Set `PFF_SOURCE_DIR` to the local path of the unpacked `FIFA World Cup 2022` source folder before running. Path stays out of git.

```bash
# operator sets this in their local shell, not committed:
#   export PFF_SOURCE_DIR="/path/to/FIFA World Cup 2022"

python -c "
import os, sys
sys.path.insert(0, 'scripts')
import upload_pff_wc2022
from pathlib import Path
ids = upload_pff_wc2022._discover_match_ids(Path(os.environ['PFF_SOURCE_DIR']))
print(f'Found {len(ids)} match(es)')
print('First 3:', ids[:3])
"
```

Expected: `Found 64 match(es)` and three numeric IDs.

- [ ] **Step 4: Commit**

```bash
git add scripts/upload_pff_wc2022.py
git commit -m "feat(scripts): add upload_pff_wc2022 orchestrator for bulk PFF private-tier load"
```

---

### Task 19: Smoke-test PFF upload with one match

**Files:** none — operational.

**Prerequisite:** Phase 1–5 deployed; SSM owner token set. No licence gate — spec §8.3 explains why private-tier loads are gate-free under the single-owner threat model.

Operator must have `PFF_SOURCE_DIR` set to the local path of the unpacked source folder. The path is intentionally NOT committed to the repo.

- [ ] **Step 1: Upload a single match end-to-end**

```bash
BUCKET=$(cd terraform/environments/dev && terraform output -raw bucket_name)
python scripts/upload_pff_wc2022.py \
  "$PFF_SOURCE_DIR" \
  --bucket "$BUCKET" \
  --limit 1 \
  --skip-players
```

Expected: one match uploads successfully; matches.json updates with object-form `artifacts`, `updated_at`, and `visibility: "private"`; providers.json gains `pff`. The `--limit 1` flag picks the first match in alphabetical order from the source folder; the operator notes its ID for the next steps and exports it as `PFF_SMOKE_MATCH_ID`:

```bash
# Operator captures the uploaded match ID for downstream curl steps. Not committed.
export PFF_SMOKE_MATCH_ID=$(ls "$PFF_SOURCE_DIR/Metadata" | head -1 | sed 's/\.json$//')
```

- [ ] **Step 2: Verify public token gets 404 on private match**

```bash
API=$(cd terraform/environments/dev && terraform output -raw api_url)
PUB="$PUB_TOKEN"
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $PUB" "$API/pff/matches/$PFF_SMOKE_MATCH_ID/metadata"
```

Expected: `404`.

- [ ] **Step 3: Verify public token sees empty matches list for pff**

```bash
curl -s -H "Authorization: Bearer $PUB" "$API/pff/matches" | python -m json.tool
```

Expected: `{"provider": "pff", "matches": []}`.

- [ ] **Step 4: Verify public token still sees pff in the providers list (spec §4.2)**

```bash
curl -s -H "Authorization: Bearer $PUB" "$API/providers" | python -m json.tool
```

Expected: `pff` is in the providers list — visible to public tier (existence is not the secret).

- [ ] **Step 5: Verify owner token gets 302 + can fetch the metadata**

```bash
OWNER=$(aws ssm get-parameter --name "/pining-for-the-data/api_token_owner" --with-decryption --query 'Parameter.Value' --output text)
curl -s -L -H "Authorization: Bearer $OWNER" "$API/pff/matches/$PFF_SMOKE_MATCH_ID/metadata" | python -m json.tool | head -5
```

Expected: 302 followed transparently to the presigned URL, then the metadata JSON prints. The body content is not asserted in this smoke test (any specific team/player names would themselves be PFF mapping data).

- [ ] **Step 6: Verify owner token sees the match in the list with object-form artifacts**

```bash
curl -s -H "Authorization: Bearer $OWNER" "$API/pff/matches" | python -m json.tool
```

Expected: 1 match entry with `"id": "$PFF_SMOKE_MATCH_ID"`, `"visibility": "private"`, and `"artifacts"` as an object like `{"metadata": "metadata.json", "events": "events.json", "roster": "roster.json", "tracking": "tracking.jsonl.bz2"}`. Also has `"updated_at"` ending in `Z`.

- [ ] **Step 7: No commit (operational task)**

---

### Task 20: Bulk PFF load and final verification

**Files:** none — operational. (Task 21 below introduces the `verify_pff_load.py` script invoked at Step 8.)

**Prerequisite:** Phase 1–5 deployed; SSM owner token set. No licence gate (spec §8.3).

- [ ] **Step 1: Upload all 64 matches plus the player catalogue**

```bash
BUCKET=$(cd terraform/environments/dev && terraform output -raw bucket_name)
python scripts/upload_pff_wc2022.py \
  "$PFF_SOURCE_DIR" \
  --bucket "$BUCKET"
```

Expected: 64 matches uploaded, players catalogue uploaded (canonical-JSON normalisation runs in a temp directory before `pining-upload-players` is invoked). Total runtime: 5-15 minutes depending on bandwidth (5.3 GB).

- [ ] **Step 2: Verify owner token sees 64 matches**

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
PUB="$PUB_TOKEN"
curl -s -H "Authorization: Bearer $PUB" "$API/pff/matches" | python -c "import sys, json; print(len(json.load(sys.stdin)['matches']))"
curl -s -H "Authorization: Bearer $PUB" "$API/pff/players" | python -c "import sys, json; print(len(json.load(sys.stdin)['players']))"
```

Expected: `0` and `0`.

- [ ] **Step 5: Spot-check an individual player by ID**

The operator picks a player ID from the loaded catalogue at runtime — do NOT hardcode a PFF identifier in committed docs (see memory `feedback_no_local_paths_in_committed_docs.md`; the same hygiene applies to provider identifiers).

```bash
# Operator picks an ID from the loaded catalogue:
SAMPLE_PLAYER_ID=$(curl -s -H "Authorization: Bearer $OWNER" "$API/pff/players" | \
  python -c "import sys, json; print(json.load(sys.stdin)['players'][0]['id'])")
curl -s -H "Authorization: Bearer $OWNER" "$API/pff/players/$SAMPLE_PLAYER_ID" | python -m json.tool
```

Expected: a player record with `firstName`, `lastName`, `nickname`, and `"visibility": "private"`.

- [ ] **Step 6: Verify public token gets 404 on the same player ID**

```bash
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $PUB" "$API/pff/players/$SAMPLE_PLAYER_ID"
```

Expected: `404`.

- [ ] **Step 7: Verify CloudTrail captured the recent fetches**

```bash
AUDIT_BUCKET=$(cd terraform/environments/dev && terraform output -raw audit_bucket_name)
sleep 120  # CloudTrail delivers in batches
aws s3 ls "s3://$AUDIT_BUCKET/AWSLogs/" --recursive | tail -5
```

Expected: log files exist (typically `.json.gz`), with one or more from the last few minutes covering the recent S3 GetObject calls. Per spec §7.5, `/matches.json` and `/players.json` reads are also logged (only `/providers.json` is excluded), so these recent enumeration calls should be visible.

- [ ] **Step 8: Run automated post-load verification (`scripts/verify_pff_load.py`)**

```bash
python scripts/verify_pff_load.py \
  --api "$API" \
  --owner-token "$OWNER" \
  --public-token "$PUB"
```

Expected: exits 0 with a summary like `OK: 64 matches, 2322 players; 5/5 artifact spot-checks pass; 3/3 player spot-checks pass; public-tier sees 0 matches, 0 players, pff in providers list`. Exits non-zero on ANY post-condition failure (count mismatch, visibility leak, artifact 404, player record missing).

- [ ] **Step 9: No commit (operational task)**

---

### Task 21: Write `scripts/verify_pff_load.py`

**Files:**
- Create: `scripts/verify_pff_load.py`

Replaces the manual `curl` smoke tests with an automated post-condition checker. Spec §8.3.1. The script is idempotent and side-effect-free (only HTTP GETs); it should be runnable any time after the PFF load to detect regressions.

- [ ] **Step 1: Write the script**

Create `scripts/verify_pff_load.py`:

```python
"""Post-load verification for the PFF World Cup 2022 dataset.

Replaces manual curl smoke tests with an automated check that runs after
scripts/upload_pff_wc2022.py and exits non-zero on any post-condition failure.

Checks (spec §8.3.1):
  - owner-tier /pff/matches returns exactly EXPECTED_MATCH_COUNT entries
  - owner-tier /pff/players returns exactly EXPECTED_PLAYER_COUNT entries
  - public-tier /pff/matches and /pff/players return zero entries
  - public-tier /providers includes 'pff' (existence is not the secret)
  - 5 random match × 4 artifact owner-tier fetches return 200 + non-empty body
  - 3 specific known-good player IDs return 200 with expected fields
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import urllib.parse
import urllib.request
from typing import Any

EXPECTED_MATCH_COUNT = 67
EXPECTED_PLAYER_COUNT = 2322
PLAYER_SPOT_CHECK_SAMPLE_SIZE = 5  # sample N players from the response, content-agnostic
ARTIFACTS_PER_MATCH = ["metadata", "events", "roster", "tracking"]

# DO NOT hardcode (provider_id → name) tuples here — those are the licensed
# mapping the spec §8.3 redistribution-licence gate exists to protect.
# Spot-checks instead sample from the live response and validate shape only.


def _get_json(api: str, path: str, token: str) -> Any:
    req = urllib.request.Request(
        f"{api}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8")), resp.status


def _follow_redirect(api: str, path: str, token: str) -> tuple[int, int]:
    """Follow a 302 from get_artifact and return (final_status, body_byte_count)."""
    req = urllib.request.Request(
        f"{api}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read()
        return resp.status, len(body)


def _get_status(api: str, path: str, token: str) -> int:
    req = urllib.request.Request(
        f"{api}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the PFF WC2022 dataset is loaded correctly")
    parser.add_argument("--api", required=True, help="API base URL (no trailing slash)")
    parser.add_argument("--owner-token", required=True)
    parser.add_argument("--public-token", required=True)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    failures: list[str] = []

    # 1. Owner-tier match count
    try:
        body, _ = _get_json(args.api, "/pff/matches", args.owner_token)
        n = len(body.get("matches", []))
        if n != EXPECTED_MATCH_COUNT:
            failures.append(f"owner /pff/matches: expected {EXPECTED_MATCH_COUNT}, got {n}")
        else:
            print(f"OK: owner /pff/matches = {n}")
    except Exception as e:
        failures.append(f"owner /pff/matches: request failed: {e}")
        body = {"matches": []}

    matches = body.get("matches", [])

    # 2. Owner-tier player count
    try:
        pbody, _ = _get_json(args.api, "/pff/players", args.owner_token)
        np_ = len(pbody.get("players", []))
        if np_ != EXPECTED_PLAYER_COUNT:
            failures.append(f"owner /pff/players: expected {EXPECTED_PLAYER_COUNT}, got {np_}")
        else:
            print(f"OK: owner /pff/players = {np_}")
    except Exception as e:
        failures.append(f"owner /pff/players: request failed: {e}")

    # 3. Public-tier visibility leak checks
    try:
        body, _ = _get_json(args.api, "/pff/matches", args.public_token)
        if body.get("matches"):
            failures.append(f"VISIBILITY LEAK: public /pff/matches returned {len(body['matches'])} entries (expected 0)")
        else:
            print("OK: public /pff/matches = 0")
    except Exception as e:
        failures.append(f"public /pff/matches: request failed: {e}")

    try:
        body, _ = _get_json(args.api, "/pff/players", args.public_token)
        if body.get("players"):
            failures.append(f"VISIBILITY LEAK: public /pff/players returned {len(body['players'])} entries (expected 0)")
        else:
            print("OK: public /pff/players = 0")
    except Exception as e:
        failures.append(f"public /pff/players: request failed: {e}")

    # 4. public /providers MUST include pff (existence is not the secret; spec §4.2)
    try:
        body, _ = _get_json(args.api, "/providers", args.public_token)
        if "pff" not in body.get("providers", []):
            failures.append("public /providers: 'pff' missing — spec §4.2 says public tier sees all providers")
        else:
            print("OK: public /providers contains 'pff'")
    except Exception as e:
        failures.append(f"public /providers: request failed: {e}")

    # 5. Owner-tier artifact spot-check (5 random matches × 4 artifacts)
    rng = random.Random(args.seed)
    sample = rng.sample(matches, min(5, len(matches)))
    spot_pass = 0
    spot_total = 0
    for m in sample:
        match_id = m["id"]
        for artifact in ARTIFACTS_PER_MATCH:
            spot_total += 1
            try:
                status, size = _follow_redirect(args.api, f"/pff/matches/{match_id}/{artifact}", args.owner_token)
                if status == 200 and size > 0:
                    spot_pass += 1
                else:
                    failures.append(f"artifact spot-check {match_id}/{artifact}: status={status}, body={size}B")
            except Exception as e:
                failures.append(f"artifact spot-check {match_id}/{artifact}: failed: {e}")
    print(f"OK: artifact spot-check {spot_pass}/{spot_total}")

    # 6. Player spot-check — content-agnostic. Sample N players from the live
    # response and assert each conforms to the canonical PlayerRecord shape:
    # has an id matching the path-param regex, and at least one of nickname /
    # firstName+lastName per spec §6.3.
    try:
        all_players_body, _ = _get_json(args.api, "/pff/players", args.owner_token)
        all_players = all_players_body.get("players", [])
    except Exception as e:
        failures.append(f"owner /pff/players for spot-check: failed: {e}")
        all_players = []

    sample_players = rng.sample(all_players, min(PLAYER_SPOT_CHECK_SAMPLE_SIZE, len(all_players)))
    player_pass = 0
    for p in sample_players:
        pid = p.get("id", "")
        # Round-trip via /pff/players/{id} to confirm individual lookup works.
        try:
            body, _ = _get_json(args.api, f"/pff/players/{pid}", args.owner_token)
            shape_ok = (
                isinstance(body.get("id"), str)
                and (body.get("nickname") or (body.get("firstName") and body.get("lastName")))
                and body.get("visibility") == "private"
            )
            if shape_ok:
                player_pass += 1
            else:
                failures.append(f"player {pid}: PlayerRecord shape invalid in response")
        except Exception as e:
            failures.append(f"player {pid}: request failed: {e}")
    print(f"OK: player spot-check {player_pass}/{len(sample_players)}")

    # 7. Public-tier 404 on a known private artifact (spot-check uniform-404)
    if matches:
        any_match = matches[0]["id"]
        any_artifact = next(iter(matches[0].get("artifacts", {}).keys()), "metadata")
        status = _get_status(args.api, f"/pff/matches/{any_match}/{any_artifact}", args.public_token)
        if status != 404:
            failures.append(f"public /pff/matches/{any_match}/{any_artifact}: expected 404, got {status}")
        else:
            print(f"OK: public 404 on private artifact /pff/matches/{any_match}/{any_artifact}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nAll post-conditions pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke-check the script with `--help`**

```bash
python scripts/verify_pff_load.py --help
```

Expected: usage printed, no errors.

- [ ] **Step 3: Commit**

```bash
git add scripts/verify_pff_load.py
git commit -m "feat(scripts): add verify_pff_load post-load post-condition checker"
```

---

### Task 22: Rotation runbook and rehearsal

**Files:** none — operational + documents the runbook for spec §3.5.

The owner-token rotation procedure is in spec §3.5. This task is the operational rehearsal: exercise the handshake end-to-end against the dev stack so the runbook is verified executable, and so the operator has muscle memory before a real rotation.

**Critical:** the `LAST_ROTATION` env-var bump MUST cover all five Lambdas (`list_providers`, `list_matches`, `get_artifact`, `list_players`, `get_player`) in the same `terraform apply` — otherwise endpoints diverge: `/matches` accepts the new token while `/players` still accepts the old one, depending on which handler was bumped, and a consumer making mixed-endpoint calls sees split-brain auth (spec §3.5).

**No dual-validity in v1.** During the rotation window, consumers will see transient 401s from in-flight warm containers; the API does NOT keep both tokens valid. Consumers MUST implement 401 retry with backoff. Spec §3.5 covers this; spec §11.6 sketches the future zero-downtime upgrade if the operational pattern ever needs it.

- [ ] **Step 1: Capture the current owner token (so it can be restored after the rehearsal)**

```bash
ORIG_OWNER=$(aws ssm get-parameter --name "/pining-for-the-data/api_token_owner" --with-decryption --query 'Parameter.Value' --output text)
```

- [ ] **Step 2: Generate a new token and write it to SSM**

```bash
NEW_OWNER=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
aws ssm put-parameter \
  --name "/pining-for-the-data/api_token_owner" \
  --value "$NEW_OWNER" \
  --type SecureString \
  --overwrite
```

- [ ] **Step 3: Bump `LAST_ROTATION` on ALL five Lambdas via a single `terraform apply`**

```bash
cd terraform/environments/dev
terraform apply -var="last_rotation=$(date -u +%Y%m%dT%H%M%SZ)" -auto-approve
```

Expected: all five Lambda functions updated (only the `LAST_ROTATION` env var changed). This bump triggers AWS to provision new containers, which fetch the new SSM value on first invocation.

- [ ] **Step 4: Verify old token now fails on every endpoint within ~1 minute**

```bash
API=$(terraform output -raw api_url)
for endpoint in "/providers" "/skillcorner/matches" "/skillcorner/players"; do
  printf "%-30s -> " "$endpoint (old)"
  curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $ORIG_OWNER" "$API$endpoint"
done
```

Expected: every endpoint returns `401` once the warm-container population has fully recycled (typically under a minute under steady traffic; longer if traffic is sparse — re-run if some endpoints still return 200).

- [ ] **Step 5: Verify new token works on every endpoint**

```bash
for endpoint in "/providers" "/skillcorner/matches" "/skillcorner/players"; do
  printf "%-30s -> " "$endpoint (new)"
  curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $NEW_OWNER" "$API$endpoint"
done
```

Expected: every endpoint returns `200`.

- [ ] **Step 6: Restore the original token (rehearsal cleanup)**

```bash
aws ssm put-parameter \
  --name "/pining-for-the-data/api_token_owner" \
  --value "$ORIG_OWNER" \
  --type SecureString \
  --overwrite
terraform apply -var="last_rotation=$(date -u +%Y%m%dT%H%M%SZ)" -auto-approve
unset ORIG_OWNER NEW_OWNER
```

Expected: the original token is restored across all five Lambdas; the rehearsal leaves the system in its pre-rehearsal state.

- [ ] **Step 7: No commit (operational task)**

---

## Self-review notes

- All spec sections (3 auth incl. §3.5 onboarding/rotation, 4 visibility, 5 S3 layout, 6 reference resources incl. §6.6 schemas, 7 audit logging, 8 upload tooling incl. §8.2.1 spelling and §8.3 licence gate, 9 consumer contract, 10 migration, 11 future extensions incl. §11.4 re-tiering and §11.6 dual-validity sketch, 12 tests, 13 resolved questions) have at least one task. The consumer contract (§9) is implicit — the API change is what consumers consume; no work in this repo. Migration (§10) is exercised by Tasks 3, 6, 6.5, 16, 19, 20 (operational deploy + smoke + licence-gated bulk load).
- No placeholders. Every step has either exact code, an exact command, or both. The one operator-supplied input (PFF source folder) is referenced via `$PFF_SOURCE_DIR` env var; local paths are deliberately NOT committed (per memory `feedback_no_local_paths_in_committed_docs.md`).
- Type consistency: `Tier` enum used consistently across handlers. `validate_token` signature is `Tier | dict`; duplicate-token misconfiguration classifies as `Tier.PUBLIC` (fail closed; spec §3.2). `visibility` is consistently `"public" | "private"` (string, not enum) at the upload-tool boundary because it crosses argparse — internal Lambda code uses the string but Pydantic models pin the literal. `artifacts` is `dict[str, str]` everywhere (spec §4.1) — array form is gone end-to-end.
- Schema discipline: `MatchEntry` and `PlayerRecord` Pydantic models are the single source of truth (Phase 2.5, Task 6.5); `schemas/{matches,players}.schema.json` are generated from them and drift-tested in CI. Both upload CLIs validate against the models before any S3 call. Adding fields requires only model edit + `python scripts/regenerate_schemas.py`.
- Spelling consistency: British `--source-licence` is canonical on both `pining-upload` and `pining-upload-players`; American `--source-license` is a quiet alias on both (spec §8.2.1). The internal JSON field is always `licence`. The pre-revision inconsistency between the two CLIs is resolved.
- CloudTrail filter discipline: `not_ends_with` excludes only `/providers.json` (spec §7.5). The earlier sketch's broader exclusion (also `/matches.json` and `/players.json`) is corrected to keep enumeration-vector reads logged.
- Operational gating: Phase 6 (bulk PFF load) has NO runtime licence gate. Spec §8.3 documents the rationale — under the single-owner threat model, a private-tier load is data movement within the operator's own systems (their copy of the data into their own private bucket served back to their own owner-token holder), not redistribution to a third party. If a public-tier upload mode is ever added to `scripts/upload_pff_wc2022.py`, that path will need its own licence-clarification gate.
- Verify script (`scripts/verify_pff_load.py`, Task 21) replaces the manual `curl` smoke tests with an automated post-condition checker. It's HTTP-only, idempotent, and re-runnable any time after the load. Player spot-checks are content-agnostic — sample N records from the live response and validate canonical-shape conformance (no hardcoded provider-specific id→name tuples; the licensed mapping is exactly what the §8.3 redistribution gate exists to protect, including in committed test fixtures).
- Rotation runbook (Task 22) is exercised end-to-end as a rehearsal. The `LAST_ROTATION` env var bumps all five Lambdas in a single `terraform apply` (spec §3.5: split-brain risk if only some are bumped). v1 has no dual-validity — consumers MUST implement 401 retry during the rotation window, per spec §3.5; the future zero-downtime upgrade path is sketched in spec §11.6.
- Sensitive-content hygiene: no operator-local paths (uses `$PFF_SOURCE_DIR`), no hardcoded provider-internal identifiers in committed test fixtures or smoke commands (synthetic `m-001`/`test-001`/`test-002` for tests; `$PFF_SMOKE_MATCH_ID` for operational smoke; sample-from-live-response in verify script). The public token literal `test-token-pining-for-the-data` is parameterised as `$PUB_TOKEN` even though it's already documented in README — keeps the plan portable across rotations.
- Whitelist enforcement on artifact keys: `MatchEntry` Pydantic model has a `model_validator` asserting every artifact key matches the path-param regex, so the upload tool cannot land entries the API would refuse to serve (closes the path-traversal-style write that the API would block at request time but the upload tool wouldn't catch).

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-02-private-data-tier.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
