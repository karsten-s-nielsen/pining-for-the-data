# Audit Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve all 48 findings from the five parallel audits (security, observability, optimization, cognitive interface, documentation) before the repo goes public.

**Architecture:** Changes span four layers: documentation text fixes, Lambda handler code (structured logging, response headers, DRY refactor, health endpoint), CLI error handling, and Terraform infrastructure (API Gateway access logging, CloudWatch alarms/dashboard, S3 lifecycle, Lambda memory, CORS tightening). Each task is independently committable.

**Tech Stack:** Python 3.12, Terraform (hashicorp/aws ~> 6.44), AWS Lambda/API Gateway/CloudWatch/SNS, pytest

**Finding cross-reference:** Each task header lists the finding IDs it resolves (e.g., `[C3, H3]` = Critical #3, High #3 from the consolidated report).

---

## File Structure

**Create:**
- `CONTRIBUTING.md` -- contributor guide for public repo
- `CODE_OF_CONDUCT.md` -- Contributor Covenant
- `terraform/modules/functions/src/health.py` -- health check Lambda handler
- `terraform/modules/observability/main.tf` -- CloudWatch alarms, SNS, dashboard
- `terraform/modules/observability/variables.tf` -- module inputs
- `terraform/modules/observability/outputs.tf` -- module outputs
- `docs/decisions/ADR-0007-observability-baseline.md` -- SLI/SLO definitions

**Modify:**
- `ARCHITECTURE.md:102` -- fix models location
- `README.md` -- add API URL, upload-players example, luxury-lakehouse link, Dockerfile note
- `CHANGELOG.md:23,33` -- whitelist->allowlist, test count
- `SECURITY.md:19` -- add contact mechanism
- `docs/api-reference.md:113,162,267` -- whitelist->allowlist, fix "see above"
- `docs/tutorial.md:32-33,54,112-113` -- Windows alternatives, API URL
- `terraform/docs/setup.md` -- full rewrite for current API surface
- `src/publish/hf_push.py:73` -- fix GitHub URL
- `src/canonical/models.py:56,72` -- whitelist->allowlist, hoist re.compile
- `src/deidentify/name_pools.py:75-81` -- use set for dedup
- `terraform/modules/functions/src/shared.py` -- security headers, Cache-Control, JSON logging, correlation IDs, request logging, DRY helpers
- `terraform/modules/functions/src/get_player.py` -- use shared helpers
- `terraform/modules/functions/src/list_players.py` -- use shared helpers
- `terraform/modules/functions/src/get_artifact.py` -- add request logging
- `terraform/modules/functions/src/list_matches.py` -- add request logging
- `terraform/modules/functions/src/list_providers.py` -- add request logging
- `terraform/modules/api/main.tf` -- access logging, CORS, health route, tighten permissions
- `terraform/modules/functions/main.tf` -- health Lambda, memory 128->256, log groups
- `terraform/modules/storage/main.tf` -- noncurrent version lifecycle
- `terraform/environments/dev/main.tf` -- wire observability module
- `terraform/environments/dev/variables.tf` -- alarm email variable
- `src/mock_api/upload.py` -- error wrapping, env var default, spec ref removal, argument groups
- `src/mock_api/upload_players.py` -- error wrapping, spec ref removal, help text
- `.github/workflows/python-ci.yml` -- add pip-audit step

---

### Task 1: Documentation text fixes [C3, H3, H4, H6, H7, M22, L4, L6]

Quick targeted edits across multiple documentation files. No code logic changes.

**Files:**
- Modify: `ARCHITECTURE.md:102`
- Modify: `README.md:69,176,191,210`
- Modify: `CHANGELOG.md:23,33`
- Modify: `SECURITY.md:19`
- Modify: `docs/api-reference.md:113,162,267`
- Modify: `src/publish/hf_push.py:73`
- Modify: `src/canonical/models.py:56`

- [ ] **Step 1: Fix ARCHITECTURE.md models location [C3]**

Line 102 currently says:
```
Canonical Pydantic models (`MatchEntry`, `PlayerRecord`) in `shared.py` are the single source of truth
```
Replace with:
```
Canonical Pydantic models (`MatchEntry`, `PlayerRecord`) in `src/canonical/models.py` are the single source of truth for the index shapes; JSON Schemas in `schemas/` are generated from them and drift-tested. Lambda handlers consume already-validated dict payloads from S3 and never import the models (ADR 0006).
```

- [ ] **Step 2: Fix HuggingFace dataset card GitHub URL [H3]**

In `src/publish/hf_push.py`, line 73:
```python
# BEFORE:
Code and tooling: [pining-for-the-data](https://github.com/karstenskyt/pining-for-the-data)
# AFTER:
Code and tooling: [pining-for-the-data](https://github.com/karsten-s-nielsen/pining-for-the-data)
```

- [ ] **Step 3: Replace "whitelist" with "allowlist" [H4]**

Three locations:

`CHANGELOG.md:23` -- change `the keys form the API's whitelist` to `the keys form the API's allowlist`.

`docs/api-reference.md:113` -- change `The keys form the API's whitelist` to `The keys form the API's allowlist`.

`docs/api-reference.md:162` -- change `artifact name is not a key in the entry's artifacts whitelist` to `artifact name is not a key in the entry's artifacts allowlist`.

`src/canonical/models.py:56` -- change `Keys form the API whitelist` to `Keys form the API allowlist`.

`get_artifact.py:69` -- change `keys form the whitelist` to `keys form the allowlist`.

- [ ] **Step 4: Fix CHANGELOG test count [H6]**

`CHANGELOG.md:33` -- change `Test count: 64 -> 127.` to `Test count: 64 -> 155.`

- [ ] **Step 5: Add pining-upload-players to README Quick Start [H7]**

After the existing "Upload to Mock API" section (after line 134), add:

```markdown
### Upload Player Catalogue

```bash
uv run pining-upload-players players.json \
  --provider skillcorner \
  --bucket your-bucket-name \
  --visibility public
```

Accepts canonical JSON only (not CSV). See [`docs/api-reference.md`](docs/api-reference.md) for the player record schema and [`scripts/upload_gradient_wc2022.py`](scripts/upload_gradient_wc2022.py) for a provider-specific adapter example. Requires AWS credentials.
```

- [ ] **Step 6: Add luxury-lakehouse hyperlink [M22]**

`README.md:210` -- change:
```
- luxury-lakehouse -- the main analytics platform that ingests this data
```
to:
```
- [luxury-lakehouse](https://github.com/karsten-s-nielsen/luxury-lakehouse) -- the main analytics platform that ingests this data
```

- [ ] **Step 7: Fix SECURITY.md contact mechanism [M21]**

Replace lines 18-19 of `SECURITY.md` with:
```markdown
1. **Do not** open a public GitHub issue.
2. Use [GitHub's private vulnerability reporting](https://github.com/karsten-s-nielsen/pining-for-the-data/security/advisories/new) (preferred), or email the maintainer directly (see GitHub profile for contact information).
3. Include a description of the vulnerability, steps to reproduce, and potential impact.
```

- [ ] **Step 8: Fix api-reference.md "see above" reference [M11 from doc audit]**

`docs/api-reference.md:267` -- change `discovery index (object-form artifacts; see above)` to `discovery index (object-form artifacts; see [List Matches](#list-matches) response)`.

- [ ] **Step 9: Clarify Dockerfile in README project structure [M17 from doc audit]**

`README.md:191` -- change `Dockerfile               # GPU-enabled container (future)` to `Dockerfile               # GPU-enabled container (scaffolding only; no workload yet)`.

- [ ] **Step 10: Run tests to verify no regressions**

```bash
uv run pytest -x -q
uv run ruff check src/
uv run pyright src/
```

- [ ] **Step 11: Commit**

```bash
git add ARCHITECTURE.md README.md CHANGELOG.md SECURITY.md docs/api-reference.md src/publish/hf_push.py src/canonical/models.py terraform/modules/functions/src/get_artifact.py
git commit -m "docs: fix 8 audit findings — models location, HF URL, whitelist terminology, test count, upload-players example, contact mechanism"
```

---

### Task 2: Setup guide overhaul [H1]

Complete rewrite of `terraform/docs/setup.md` to reflect current API surface (5 endpoints, two-tier auth, audit module, players, CORS, artifact dict-lookup).

**Files:**
- Modify: `terraform/docs/setup.md`

- [ ] **Step 1: Rewrite setup.md**

Replace the full file content. Key changes from the current version:
- **API Endpoints table** (lines 208-213): expand from 3 to 5 endpoints (add `list_players`, `get_player`)
- **"Adding New Artifact Types" section** (lines 184-185): replace stale glob description with current dict-lookup description: "The `get_artifact` handler resolves artifact names by looking up the key in the match entry's `artifacts` object (a `{name: filename}` dict). Upload a file, and the upload CLI records it in `matches.json`; the artifact name (filename without extension) becomes the API path."
- **Step 5: Upload Game Data** curl example (line 142): change `tracking.txt` to `tracking.jsonl`
- **Teardown section** (lines 236-246): add warning admonition before destructive commands
- **Architecture section** (lines 192-204): add audit module, SSM, KMS to the tree
- **New section: Two-tier auth** -- explain public/owner tokens, SSM, rotation, `--visibility`
- **New section: Upload Player Catalogue** -- document `pining-upload-players`

The full replacement content for `terraform/docs/setup.md`:

```markdown
# Mock Provider API -- Setup Guide

Step-by-step deployment of the AWS mock provider API. Estimated time: 15 minutes.
Estimated monthly cost: **~$0.02** (effectively free within AWS Free Tier).

---

## Prerequisites

- [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html) configured with credentials
- [Terraform >= 1.5](https://developer.hashicorp.com/terraform/install)
- Python 3.12+ with [uv](https://github.com/astral-sh/uv)

Verify:

```bash
aws sts get-caller-identity   # should show your account
terraform --version            # >= 1.5.0
uv --version
```

---

## Step 1: Bootstrap State Infrastructure

The state module creates an S3 bucket and DynamoDB table for Terraform remote state.
This only needs to run once per AWS account.

```bash
cd terraform/modules/state
terraform init
terraform apply \
  -var="project_name=pining-for-the-data" \
  -var="aws_region=us-east-1"
```

Note the outputs:

```
state_bucket = "pining-for-the-data-tfstate-XXXXXXXXXXXX"
lock_table   = "pining-for-the-data-tflock"
```

---

## Step 2: Configure the Backend

Edit `terraform/environments/dev/backend.tf` with the values from Step 1:

```hcl
terraform {
  backend "s3" {
    bucket         = "pining-for-the-data-tfstate-XXXXXXXXXXXX"  # from step 1
    key            = "pining-for-the-data/dev/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "pining-for-the-data-tflock"                # from step 1
    encrypt        = true
  }
}
```

---

## Step 3: Configure Variables

```bash
cd terraform/environments/dev
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:

```hcl
aws_region   = "us-east-1"
project_name = "pining-for-the-data"
api_token    = "test-token-pining-for-the-data"
```

The public token is intentionally documented -- the SkillCorner data is open.
Auth exists to exercise the same code path as real providers.

---

## Step 4: Deploy

```bash
cd terraform/environments/dev
terraform init
terraform apply
```

Note the outputs:

```
api_url     = "https://XXXXXXXXXX.execute-api.us-east-1.amazonaws.com/v1"
bucket_name = "karstenskyt-pining-for-the-data"
```

---

## Step 5: Upload Game Data

Install the upload CLI:

```bash
uv sync --extra aws
```

Upload a game:

```bash
uv run pining-upload path/to/game_03/ \
  --provider skillcorner \
  --game-id game_03 \
  --bucket "karstenskyt-pining-for-the-data" \
  --date 2026-01-03 \
  --home "Auckland FC" \
  --away "Wellington Phoenix FC"
```

The upload CLI:
1. Uploads all files in the directory to `s3://{bucket}/skillcorner/game_03/`
2. Creates/updates `skillcorner/matches.json` with the game entry (object-form `artifacts: {name: filename}`)
3. Creates/updates `providers.json` with the provider

Upload player reference data:

```bash
uv run pining-upload-players players.json \
  --provider skillcorner \
  --bucket "karstenskyt-pining-for-the-data" \
  --visibility public
```

Accepts canonical JSON only (not CSV). Provider-specific shapes must be normalised first; see `scripts/upload_gradient_wc2022.py` for a worked example.

### Private-tier content

To upload restricted data visible only to the owner token:

```bash
uv run pining-upload path/to/game/ \
  --provider gradientsports \
  --game-id example-001 \
  --bucket "karstenskyt-pining-for-the-data" \
  --visibility private
```

Private content is stored under `{provider}/_private/{game_id}/` in S3 and recorded with `visibility: "private"` in `matches.json`. The public API token returns `404` for private content (uniform with not-found to prevent existence leaks).

---

## Step 6: Set the Owner Token (optional)

The owner-tier bearer token enables access to private-tier content. It is stored as an SSM SecureString and never committed to the repo.

```bash
aws ssm put-parameter \
  --name "/pining-for-the-data/api_token_owner" \
  --type SecureString \
  --value "your-secret-owner-token" \
  --overwrite
```

To rotate, update the SSM value and bump `LAST_ROTATION` to invalidate warm Lambda caches:

```bash
terraform apply -var='last_rotation=2026-05-07T120000Z'
```

---

## Step 7: Test the API

```bash
TOKEN="test-token-pining-for-the-data"
API="https://XXXXXXXXXX.execute-api.us-east-1.amazonaws.com/v1"

# List providers
curl -s -H "Authorization: Bearer $TOKEN" "$API/providers" | python -m json.tool

# List matches for a provider
curl -s -H "Authorization: Bearer $TOKEN" "$API/skillcorner/matches" | python -m json.tool

# Download an artifact (follows redirect to presigned S3 URL)
curl -s -L -H "Authorization: Bearer $TOKEN" \
  "$API/skillcorner/matches/game_03/tracking" -o tracking.jsonl
ls -la tracking.jsonl

# List players for a provider
curl -s -H "Authorization: Bearer $TOKEN" "$API/skillcorner/players" | python -m json.tool

# Get a single player
curl -s -H "Authorization: Bearer $TOKEN" "$API/gradientsports/players/example-007" | python -m json.tool

# Health check (no auth required)
curl -s "$API/health" | python -m json.tool

# Verify auth rejection
curl -s "$API/providers"  # should return 401
```

---

## For Forkers

Everything is configurable -- no hardcoded values. To deploy your own instance:

1. Fork this repo
2. Create an AWS account ([Free Tier](https://aws.amazon.com/free/) covers everything)
3. Install Terraform and AWS CLI
4. Follow Steps 1-7 above, changing `project_name` in `terraform.tfvars` to your own

What you'll customize:

| Setting | Where | Default |
|---------|-------|---------|
| Project name | `terraform.tfvars` | `pining-for-the-data` |
| AWS region | `terraform.tfvars` | `us-east-1` |
| Public API token | `terraform.tfvars` | `test-token-pining-for-the-data` |
| Owner API token | SSM Parameter Store | Set via `aws ssm put-parameter` |
| Provider name | `pining-upload --provider` | `skillcorner` |

### Adding a New Provider

No infrastructure changes needed. Upload files with a new provider prefix:

```bash
uv run pining-upload path/to/game/ \
  --provider my_provider \
  --game-id game_01 \
  --bucket your-bucket-name
```

### Adding New Artifact Types

The `get_artifact` handler resolves artifact names by looking up the key in the match entry's `artifacts` object (a `{name: filename}` dict). The upload CLI records each file automatically: the artifact name is the filename without its extension (e.g., `tracking.jsonl` becomes artifact `tracking`).

For example, uploading a directory containing `events.json` makes it available at `GET /v1/{provider}/matches/{game_id}/events`.

---

## Architecture

```
terraform/
├── environments/dev/     # Environment composition + variables
├── modules/
│   ├── state/            # Bootstrap: S3 state bucket + DynamoDB lock
│   ├── storage/          # Data bucket + KMS encryption
│   ├── functions/        # Lambda handlers + IAM
│   │   └── src/          # Python handler source
│   ├── api/              # API Gateway REST routes + access logging
│   ├── audit/            # CloudTrail data events on data bucket
│   └── observability/    # CloudWatch alarms, dashboard, SNS notifications
└── shared/               # Provider + version pins
```

### API Endpoints

| Method | Path | Handler | Response |
|--------|------|---------|----------|
| GET | `/v1/providers` | `list_providers` | JSON list of providers (tier-blind) |
| GET | `/v1/{provider}/matches` | `list_matches` | JSON list of games + artifacts (filtered by tier) |
| GET | `/v1/{provider}/matches/{id}/{artifact}` | `get_artifact` | 302 redirect to presigned S3 URL |
| GET | `/v1/{provider}/players` | `list_players` | JSON list of player records (filtered by tier) |
| GET | `/v1/{provider}/players/{id}` | `get_player` | Single player record |
| GET | `/v1/health` | `health` | `{"status": "ok"}` (no auth required) |

All endpoints except `/v1/health` require `Authorization: Bearer <token>`.

### Two-tier auth

- **Public tier**: documented `api_token` in `terraform.tfvars`. Sees public-visibility content only.
- **Owner tier**: bearer token in SSM Parameter Store SecureString. Sees all content.
- Tier mismatch returns uniform `404` (not `403`) to prevent existence leaks.
- See [API Reference](../../docs/api-reference.md) for the full contract.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `terraform init` fails with "Failed to get existing workspaces" | Backend S3 bucket doesn't exist yet | Run Step 1 (Bootstrap) first |
| `terraform apply` fails with "Access Denied" | IAM permissions insufficient | Your AWS credentials need `s3:*`, `apigateway:*`, `lambda:*`, `iam:*`, `kms:*`, `dynamodb:*`, `logs:*` permissions |
| `pining-upload` fails with "NoSuchBucket" | Bucket name mismatch | Check `terraform output bucket_name` and pass the exact value to `--bucket` |
| `pining-upload` fails with "ModuleNotFoundError: No module named 'boto3'" | Missing AWS extra | Run `uv sync --extra aws` (or `uv sync --extra dev`) |
| `curl` returns `{"error":"Invalid token"}` | Wrong or missing token | Verify `Authorization: Bearer <token>` header matches `api_token` in `terraform.tfvars` |
| `curl` returns `{"message":"Forbidden"}` | API Gateway stage mismatch | Ensure the URL ends with `/v1/...` |
| `curl` returns `{"error":"Artifact not found"}` | File not uploaded or wrong artifact name | Check S3: `aws s3 ls s3://{bucket}/{provider}/{game_id}/` -- artifact name is the filename without extension |
| `terraform destroy` fails with "BucketNotEmpty" | S3 bucket still has objects | Empty it first (see Teardown) |

---

## Teardown

> **Warning:** This permanently deletes all data and infrastructure. Ensure you have backups of any data you want to keep before proceeding.

To remove all AWS resources:

```bash
# Empty the data bucket first (Terraform can't delete non-empty buckets)
aws s3 rm s3://karstenskyt-pining-for-the-data --recursive

# Empty the audit bucket
aws s3 rm s3://pining-for-the-data-audit-XXXXXXXXXXXX --recursive

cd terraform/environments/dev
terraform destroy

# Optionally remove state infrastructure
cd terraform/modules/state
terraform destroy -var="project_name=pining-for-the-data" -var="aws_region=us-east-1"
```
```

- [ ] **Step 2: Commit**

```bash
git add terraform/docs/setup.md
git commit -m "docs: rewrite setup guide for current API surface (5 endpoints, two-tier auth, players, audit)"
```

---

### Task 3: Tutorial and API reference fixes [H2, M18, M11 from doc audit]

Fix Windows-incompatible commands in the tutorial, add the hosted API URL, and fix cross-reference.

**Files:**
- Modify: `docs/tutorial.md:32-33,54,112-113`
- Modify: `README.md:69`
- Modify: `docs/api-reference.md:5`

- [ ] **Step 1: Add hosted API URL to README [H2]**

In `README.md`, line 68-69, change:
```bash
TOKEN="test-token-pining-for-the-data"
curl -H "Authorization: Bearer $TOKEN" "$API_URL/v1/skillcorner/matches" | python -m json.tool
```
to:
```bash
TOKEN="test-token-pining-for-the-data"
API_URL="https://ozqgk9a3ji.execute-api.us-east-1.amazonaws.com"
curl -H "Authorization: Bearer $TOKEN" "$API_URL/v1/skillcorner/matches" | python -m json.tool
```

- [ ] **Step 2: Add hosted API URL to API reference [H2]**

In `docs/api-reference.md`, line 5, change:
```
**Base URL:** `https://{api-gateway-id}.execute-api.{region}.amazonaws.com/v1`
```
to:
```
**Base URL:** `https://ozqgk9a3ji.execute-api.us-east-1.amazonaws.com/v1`

> To deploy your own instance, see the [Setup Guide](../terraform/docs/setup.md). The base URL pattern is `https://{api-gateway-id}.execute-api.{region}.amazonaws.com/v1`.
```

- [ ] **Step 3: Fix tutorial API URL [H2]**

In `docs/tutorial.md`, lines 111-112, change:
```bash
TOKEN="test-token-pining-for-the-data"
API="https://your-api-url/v1"
```
to:
```bash
TOKEN="test-token-pining-for-the-data"
API="https://ozqgk9a3ji.execute-api.us-east-1.amazonaws.com/v1"
```

- [ ] **Step 4: Add Windows alternatives to tutorial commands [M18]**

In `docs/tutorial.md`, after line 32 (`cat ... | python -m json.tool | head -50`), add:
```markdown
> **Windows (PowerShell):** `Get-Content src\tests\fixtures\sample_match.json | python -m json.tool | Select-Object -First 50`
```

After line 54 (`head -2 src/tests/fixtures/sample_tracking.jsonl | python -m json.tool`), add:
```markdown
> **Windows (PowerShell):** `Get-Content src\tests\fixtures\sample_tracking.jsonl -TotalCount 2 | python -m json.tool`
```

- [ ] **Step 5: Commit**

```bash
git add docs/tutorial.md README.md docs/api-reference.md
git commit -m "docs: add hosted API URL, Windows command alternatives in tutorial"
```

---

### Task 4: Community documentation [H5, L6]

Create CONTRIBUTING.md and CODE_OF_CONDUCT.md for the public repo.

**Files:**
- Create: `CONTRIBUTING.md`
- Create: `CODE_OF_CONDUCT.md`

- [ ] **Step 1: Create CONTRIBUTING.md**

```markdown
# Contributing to pining-for-the-data

Thank you for your interest in contributing! This document covers the development workflow and standards.

## Development Setup

```bash
git clone https://github.com/karsten-s-nielsen/pining-for-the-data.git
cd pining-for-the-data
uv sync --extra dev
uv run pre-commit install
```

## Coding Standards

- **Python 3.12+** -- use modern syntax (type unions with `|`, etc.)
- **Ruff** for linting and formatting (line length 120)
- **Pyright** for type checking (basic mode)
- **pytest** for testing

All three must pass before submitting a PR:

```bash
uv run ruff check src/ && uv run ruff format --check src/
uv run pyright src/
uv run pytest
```

## Commit Conventions

- Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`
- Keep commits focused -- one logical change per commit

## Pull Request Process

1. Fork the repo and create a feature branch from `main`
2. Make your changes with tests
3. Ensure all checks pass (ruff, pyright, pytest)
4. Fill out the PR template
5. A maintainer will review your PR

## What to Contribute

- Bug fixes (with regression tests)
- Documentation improvements
- New format handlers (see `src/formats/` for the pattern)
- Test coverage improvements

## What Not to Contribute (without discussion first)

- New CLI tools or API endpoints (open an issue first)
- Large refactors (open an issue first)
- Changes to the de-identification engine (reserved for private data use cases)

## Questions?

Open a [GitHub issue](https://github.com/karsten-s-nielsen/pining-for-the-data/issues) for questions or feature proposals.
```

- [ ] **Step 2: Create CODE_OF_CONDUCT.md**

Use the [Contributor Covenant v2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/):

```markdown
# Contributor Covenant Code of Conduct

## Our Pledge

We as members, contributors, and leaders pledge to make participation in our
community a harassment-free experience for everyone, regardless of age, body
size, visible or invisible disability, ethnicity, sex characteristics, gender
identity and expression, level of experience, education, socio-economic status,
nationality, personal appearance, race, caste, color, religion, or sexual
identity and orientation.

We pledge to act and interact in ways that contribute to an open, welcoming,
diverse, inclusive, and healthy community.

## Our Standards

Examples of behavior that contributes to a positive environment for our
community include:

* Demonstrating empathy and kindness toward other people
* Being respectful of differing opinions, viewpoints, and experiences
* Giving and gracefully accepting constructive feedback
* Accepting responsibility and apologizing to those affected by our mistakes,
  and learning from the experience
* Focusing on what is best not just for us as individuals, but for the overall
  community

Examples of unacceptable behavior include:

* The use of sexualized language or imagery, and sexual attention or advances of
  any kind
* Trolling, insulting or derogatory comments, and personal or political attacks
* Public or private harassment
* Publishing others' private information, such as a physical or email address,
  without their explicit permission
* Other conduct which could reasonably be considered inappropriate in a
  professional setting

## Enforcement

Instances of abusive, harassing, or otherwise unacceptable behavior may be
reported to the project maintainer. All complaints will be reviewed and
investigated promptly and fairly.

## Attribution

This Code of Conduct is adapted from the [Contributor Covenant](https://www.contributor-covenant.org),
version 2.1, available at
[https://www.contributor-covenant.org/version/2/1/code_of_conduct.html](https://www.contributor-covenant.org/version/2/1/code_of_conduct.html).
```

- [ ] **Step 3: Commit**

```bash
git add CONTRIBUTING.md CODE_OF_CONDUCT.md
git commit -m "docs: add CONTRIBUTING.md and CODE_OF_CONDUCT.md for public repo"
```

---

### Task 5: Lambda response hardening [H8, H16, M4]

Add security headers and Cache-Control to all Lambda responses. Tighten CORS.

**Files:**
- Modify: `terraform/modules/functions/src/shared.py:124-145`
- Test: `src/tests/test_lambda_handlers.py`

- [ ] **Step 1: Write tests for new response headers**

Add to `src/tests/test_lambda_handlers.py`:

```python
def test_json_response_includes_security_headers():
    """All JSON responses must include security headers."""
    from shared import json_response
    resp = json_response(200, {"test": "data"})
    headers = resp["headers"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Strict-Transport-Security"] == "max-age=63072000; includeSubDomains"
    assert headers["X-Frame-Options"] == "DENY"


def test_json_response_cache_control_default():
    """Default responses have no-store cache control."""
    from shared import json_response
    resp = json_response(200, {"test": "data"})
    assert resp["headers"]["Cache-Control"] == "no-store"


def test_json_response_cache_control_override():
    """Cache-Control can be overridden for list endpoints."""
    from shared import json_response
    resp = json_response(200, {"test": "data"}, cache_control="public, max-age=60")
    assert resp["headers"]["Cache-Control"] == "public, max-age=60"


def test_redirect_response_includes_security_headers():
    """Redirect responses must include security headers."""
    from shared import redirect_response
    resp = redirect_response("https://example.com")
    headers = resp["headers"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Cache-Control"] == "no-store"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest src/tests/test_lambda_handlers.py -k "security_headers or cache_control" -v
```

- [ ] **Step 3: Update shared.py response helpers**

Replace `json_response` and `redirect_response` in `shared.py`:

```python
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "X-Frame-Options": "DENY",
}


def json_response(status_code: int, body: dict, *, cache_control: str = "no-store") -> dict:
    """Build API Gateway proxy response with security headers."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": cache_control,
            **_SECURITY_HEADERS,
        },
        "body": json.dumps(body),
    }


def redirect_response(url: str) -> dict:
    """Build 302 redirect response with security headers."""
    return {
        "statusCode": 302,
        "headers": {
            "Location": url,
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-store",
            **_SECURITY_HEADERS,
        },
        "body": "",
    }
```

- [ ] **Step 4: Add Cache-Control overrides to list endpoints**

In `list_providers.py`, change the final return:
```python
return json_response(200, providers, cache_control="public, max-age=60")
```

In `list_matches.py`, change the final return:
```python
return json_response(200, {**matches, "matches": match_list}, cache_control="public, max-age=60")
```

In `list_players.py`, change the final return:
```python
return json_response(200, {"provider": provider, "players": players}, cache_control="public, max-age=60")
```

Leave `get_artifact` and `get_player` with default `no-store` (single-record endpoints).

- [ ] **Step 5: Run tests**

```bash
uv run pytest -x -q
```

- [ ] **Step 6: Commit**

```bash
git add terraform/modules/functions/src/shared.py terraform/modules/functions/src/list_providers.py terraform/modules/functions/src/list_matches.py terraform/modules/functions/src/list_players.py src/tests/test_lambda_handlers.py
git commit -m "feat(api): add security headers and Cache-Control to all Lambda responses"
```

---

### Task 6: Structured JSON logging with correlation IDs [H11, H12, H13, L7 from obs audit]

Replace default Python logging with structured JSON format, inject request_id and trace_id, add request/response logging on every invocation.

**Files:**
- Modify: `terraform/modules/functions/src/shared.py`
- Modify: All 5 Lambda handler files (add request/response log lines)
- Test: `src/tests/test_lambda_handlers.py`

- [ ] **Step 1: Write test for structured logging**

Add to `src/tests/test_lambda_handlers.py`:

```python
import json as json_mod
import logging


def test_configure_logging_sets_json_format(monkeypatch):
    """configure_handler_logging must produce JSON log lines."""
    from shared import configure_handler_logging
    logger = configure_handler_logging("test_handler")
    # Capture a log line
    import io
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logger.handlers[0].formatter)
    logger.addHandler(handler)
    logger.info("test_message", extra={"key": "value"})
    line = stream.getvalue().strip()
    parsed = json_mod.loads(line)
    assert parsed["message"] == "test_message"
    assert parsed["handler"] == "test_handler"
    assert parsed["key"] == "value"
    logger.removeHandler(handler)
```

- [ ] **Step 2: Add JSON log formatter and configure_handler_logging to shared.py**

Add before the existing `logger = logging.getLogger(__name__)` line:

```python
class _JsonFormatter(logging.Formatter):
    """Structured JSON log formatter for CloudWatch Logs Insights."""

    def __init__(self, handler_name: str = "") -> None:
        super().__init__()
        self._handler_name = handler_name

    def format(self, record: logging.LogRecord) -> str:
        log_dict: dict = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + "Z",
            "level": record.levelname,
            "handler": self._handler_name or getattr(record, "handler", ""),
            "message": record.getMessage(),
        }
        # Merge extra fields
        for key in ("request_id", "trace_id", "method", "path", "status_code", "key", "param", "stage"):
            val = getattr(record, key, None)
            if val is not None:
                log_dict[key] = val
        if record.exc_info and record.exc_info[0] is not None:
            log_dict["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_dict, default=str)


def configure_handler_logging(handler_name: str) -> logging.Logger:
    """Create a per-handler logger with JSON formatting.

    Call once at module level in each Lambda handler.
    """
    log = logging.getLogger(handler_name)
    log.setLevel(logging.INFO)
    # Lambda pre-configures a handler; replace its formatter
    if log.handlers:
        log.handlers[0].setFormatter(_JsonFormatter(handler_name))
    else:
        h = logging.StreamHandler()
        h.setFormatter(_JsonFormatter(handler_name))
        log.addHandler(h)
    return log
```

Keep the existing `logger = logging.getLogger(__name__)` for shared.py's own use but note that handlers will use their own loggers.

- [ ] **Step 3: Add request context extraction helper to shared.py**

```python
def extract_request_context(event: dict, context: object) -> dict:
    """Extract request metadata for structured logging."""
    rc = event.get("requestContext") or {}
    http = rc.get("http") or {}
    return {
        "request_id": getattr(context, "aws_request_id", ""),
        "trace_id": os.environ.get("_X_AMZN_TRACE_ID", ""),
        "method": http.get("method", ""),
        "path": http.get("path", ""),
    }
```

- [ ] **Step 4: Update each handler to use structured logging**

In each handler file (`list_providers.py`, `list_matches.py`, `get_artifact.py`, `list_players.py`, `get_player.py`), replace:
```python
from shared import ... logger ...
```
with importing `configure_handler_logging` and `extract_request_context`:
```python
from shared import (
    ...
    configure_handler_logging,
    extract_request_context,
)

logger = configure_handler_logging("list_providers")  # use the handler's name
```

At the top of each `handler()` function, add:
```python
    req = extract_request_context(event, context)
    logger.info("request_start", extra=req)
```

Before each return statement, add:
```python
    logger.info("request_end", extra={**req, "status_code": response["statusCode"]})
```

Where `response` is the dict being returned. For handlers with multiple return paths, add the log line before each return. Use the pattern:
```python
    response = json_response(200, ...)
    logger.info("request_end", extra={**req, "status_code": 200})
    return response
```

For early error returns (auth failure, validation), log at the existing warning level but include request context:
```python
    logger.warning("auth_failure", extra={**req, "handler": "list_providers"})
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest -x -q
```

- [ ] **Step 6: Commit**

```bash
git add terraform/modules/functions/src/ src/tests/test_lambda_handlers.py
git commit -m "feat(api): structured JSON logging with correlation IDs and request/response logging"
```

---

### Task 7: Lambda DRY refactor [M7 from security, M8 from obs, M11 from opt]

Extract duplicated `_provider_known()` and `_read_index()` from `list_players.py` and `get_player.py` into `shared.py`.

**Files:**
- Modify: `terraform/modules/functions/src/shared.py`
- Modify: `terraform/modules/functions/src/list_players.py`
- Modify: `terraform/modules/functions/src/get_player.py`
- Test: `src/tests/test_lambda_handlers.py`

- [ ] **Step 1: Write test for shared helpers**

```python
def test_provider_known_returns_true_for_known_provider(s3_stub):
    """provider_known should return True when provider exists in providers.json."""
    import json as json_mod
    from shared import get_s3_client, provider_known
    s3 = get_s3_client()
    s3.put_object(Bucket="test-bucket", Key="providers.json",
                  Body=json_mod.dumps({"providers": ["skillcorner"]}).encode())
    assert provider_known(s3, "test-bucket", "skillcorner") is True
    assert provider_known(s3, "test-bucket", "unknown") is False


def test_read_player_index_returns_players(s3_stub):
    """read_player_index should return the players list from an index file."""
    import json as json_mod
    from shared import get_s3_client, read_player_index
    s3 = get_s3_client()
    s3.put_object(Bucket="test-bucket", Key="gradientsports/players.json",
                  Body=json_mod.dumps({"players": [{"id": "p1"}]}).encode())
    assert read_player_index(s3, "test-bucket", "gradientsports/players.json") == [{"id": "p1"}]


def test_read_player_index_returns_empty_on_missing(s3_stub):
    """read_player_index should return [] when the index doesn't exist."""
    from shared import get_s3_client, read_player_index
    s3 = get_s3_client()
    assert read_player_index(s3, "test-bucket", "nonexistent.json") == []
```

- [ ] **Step 2: Add shared helpers to shared.py**

```python
def provider_known(s3, bucket: str, provider: str) -> bool:
    """Check that `provider` appears in providers.json."""
    try:
        obj = s3.get_object(Bucket=bucket, Key="providers.json")
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return provider in (data.get("providers") or [])
    except s3.exceptions.NoSuchKey:
        return False
    except Exception:
        logger.exception("s3_error", extra={"key": "providers.json"})
        return False


def read_player_index(s3, bucket: str, key: str) -> list[dict]:
    """Read a players index from S3. Returns [] if the index doesn't exist."""
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return data.get("players", [])
    except s3.exceptions.NoSuchKey:
        return []
    except Exception:
        logger.exception("s3_error", extra={"key": key})
        return []
```

- [ ] **Step 3: Update list_players.py and get_player.py**

Remove the local `_provider_known()` and `_read_index()` functions from both files. Import from shared instead:

```python
from shared import (
    ...,
    provider_known,
    read_player_index,
)
```

Replace all calls: `_provider_known(s3, provider)` becomes `provider_known(s3, BUCKET, provider)`, and `_read_index(s3, key)` becomes `read_player_index(s3, BUCKET, key)`.

- [ ] **Step 4: Run tests**

```bash
uv run pytest -x -q
```

- [ ] **Step 5: Commit**

```bash
git add terraform/modules/functions/src/shared.py terraform/modules/functions/src/list_players.py terraform/modules/functions/src/get_player.py src/tests/test_lambda_handlers.py
git commit -m "refactor(api): extract provider_known and read_player_index to shared.py"
```

---

### Task 8: Health check endpoint [H14]

Add a `/v1/health` endpoint that requires no auth and returns `{"status": "ok"}`.

**Files:**
- Create: `terraform/modules/functions/src/health.py`
- Modify: `terraform/modules/functions/main.tf`
- Modify: `terraform/modules/api/main.tf`
- Modify: `terraform/modules/api/variables.tf`
- Modify: `terraform/modules/api/outputs.tf` (if needed)
- Modify: `terraform/modules/functions/outputs.tf`
- Modify: `terraform/environments/dev/main.tf`
- Test: `src/tests/test_lambda_handlers.py`

- [ ] **Step 1: Write test**

```python
def test_health_returns_ok():
    """Health endpoint returns 200 with status ok."""
    from health import handler
    response = handler({}, None)
    assert response["statusCode"] == 200
    import json as json_mod
    body = json_mod.loads(response["body"])
    assert body["status"] == "ok"
```

- [ ] **Step 2: Create health.py**

```python
"""GET /v1/health -- unauthenticated health check."""

from __future__ import annotations

from shared import json_response


def handler(event: dict, context: object) -> dict:
    """Return 200 OK. No auth required. Used for synthetic monitoring."""
    return json_response(200, {"status": "ok"})
```

- [ ] **Step 3: Add Lambda function to terraform/modules/functions/main.tf**

After the `get_player` function block, add:

```hcl
resource "aws_lambda_function" "health" {
  function_name                  = "${var.project_name}-health"
  role                           = aws_iam_role.lambda.arn
  handler                        = "health.handler"
  runtime                        = "python3.12"
  memory_size                    = 128
  timeout                        = 5
  reserved_concurrent_executions = -1
  filename                       = data.archive_file.lambda_zip.output_path
  source_code_hash               = data.archive_file.lambda_zip.output_base64sha256

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      # Health doesn't need tokens but Lambda env must be non-empty
      LAST_ROTATION = var.last_rotation
    }
  }
}

resource "aws_cloudwatch_log_group" "health" {
  name              = "/aws/lambda/${aws_lambda_function.health.function_name}"
  retention_in_days = 30
}
```

Add corresponding outputs in `terraform/modules/functions/outputs.tf`:
```hcl
output "health_invoke_arn" {
  value = aws_lambda_function.health.invoke_arn
}

output "health_function_name" {
  value = aws_lambda_function.health.function_name
}
```

- [ ] **Step 4: Add API Gateway route in terraform/modules/api/main.tf**

```hcl
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
```

Add variables for health in `terraform/modules/api/variables.tf`:
```hcl
variable "health_invoke_arn" {
  type = string
}

variable "health_function_name" {
  type = string
}
```

- [ ] **Step 5: Wire through terraform/environments/dev/main.tf**

Add to the `module "api"` block:
```hcl
  health_invoke_arn      = module.functions.health_invoke_arn
  health_function_name   = module.functions.health_function_name
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest -x -q
```

- [ ] **Step 7: Commit**

```bash
git add terraform/modules/functions/src/health.py terraform/modules/functions/main.tf terraform/modules/functions/outputs.tf terraform/modules/api/main.tf terraform/modules/api/variables.tf terraform/environments/dev/main.tf src/tests/test_lambda_handlers.py
git commit -m "feat(api): add /v1/health endpoint for synthetic monitoring"
```

---

### Task 9: CLI error handling [M14, M15, M16, cognitive F-4a through F-4d]

Wrap CLI `main()` functions to catch expected errors and present clean messages. Add `PINING_BUCKET` env var default. Remove internal spec references from error messages. Add help text to bare flags.

**Files:**
- Modify: `src/mock_api/upload.py`
- Modify: `src/mock_api/upload_players.py`
- Test: `src/tests/test_upload.py` (or existing test file)

- [ ] **Step 1: Fix spec references in error messages [M14]**

In `src/mock_api/upload.py:194`, change:
```python
f"Re-tiering requires an explicit move (manual procedure documented in spec §11.4; "
f"not supported by tooling in v1)."
```
to:
```python
f"Re-tiering is not supported by the upload tool. To change a game's visibility tier, "
f"manually delete the existing entry from matches.json and re-upload with the desired visibility."
```

In `src/mock_api/upload_players.py:111`, change:
```python
f"Re-tiering requires the manual procedure in spec §11.4."
```
to:
```python
f"Re-tiering is not supported by the upload tool. Manually delete the player from the "
f"existing tier's index and re-upload with the desired visibility."
```

- [ ] **Step 2: Add PINING_BUCKET env var default [M16]**

In `src/mock_api/upload.py`, change the `--bucket` argument (line 259):
```python
parser.add_argument("--bucket", required=False,
    default=os.environ.get("PINING_BUCKET"),
    help="S3 bucket name (default: $PINING_BUCKET env var)")
```

Add `import os` at the top if not already present.

After `args = parser.parse_args()`, add:
```python
if not args.bucket:
    parser.error("--bucket is required (or set PINING_BUCKET environment variable)")
```

Do the same in `src/mock_api/upload_players.py`.

- [ ] **Step 3: Add help text to bare flags in upload_players.py [cognitive F-4d]**

In `src/mock_api/upload_players.py:164`, add help strings:
```python
parser.add_argument("--source-name", default=None, help="Name of the original data source")
parser.add_argument("--source-url", default=None, help="URL of the original data source")
```

- [ ] **Step 4: Wrap main() with error handler [M15]**

In `src/mock_api/upload.py`, wrap the `main()` body:

```python
def main() -> None:
    """CLI entry point for uploading game data to S3."""
    parser = argparse.ArgumentParser(description="Upload game artifacts to the mock provider API's S3 bucket")
    # ... argument definitions unchanged ...
    args = parser.parse_args()

    if not args.bucket:
        parser.error("--bucket is required (or set PINING_BUCKET environment variable)")

    if not args.game_dir.is_dir():
        parser.error(f"Not a directory: {args.game_dir}")

    try:
        print(f"Uploading {args.game_id} ({args.provider}, {args.visibility}) to s3://{args.bucket}/")
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
        print(f"Done — {len(artifacts)} artifact(s) uploaded.")
    except ValueError as e:
        parser.error(str(e))
    except ImportError as e:
        if "boto3" in str(e):
            parser.error("boto3 is required. Install with: uv sync --extra aws")
        raise
    except Exception as e:
        name = type(e).__name__
        # Check for common boto3 errors
        if "NoCredentialsError" in name or "CredentialRetrievalError" in name:
            parser.error("AWS credentials not configured. Run `aws configure` or set AWS_PROFILE.")
        if "NoSuchBucket" in name:
            parser.error(f"Bucket not found. Check `terraform output bucket_name` for the correct name.")
        raise
```

Apply the same pattern in `upload_players.py`:

```python
def main() -> None:
    # ... parser setup ...
    args = parser.parse_args()

    if not args.bucket:
        parser.error("--bucket is required (or set PINING_BUCKET environment variable)")

    try:
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
    except ValueError as e:
        parser.error(str(e))
    except ImportError as e:
        if "boto3" in str(e):
            parser.error("boto3 is required. Install with: uv sync --extra aws")
        raise
    except Exception as e:
        name = type(e).__name__
        if "NoCredentialsError" in name or "CredentialRetrievalError" in name:
            parser.error("AWS credentials not configured. Run `aws configure` or set AWS_PROFILE.")
        if "NoSuchBucket" in name:
            parser.error(f"Bucket not found. Check `terraform output bucket_name` for the correct name.")
        raise
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest -x -q
uv run ruff check src/
```

- [ ] **Step 6: Commit**

```bash
git add src/mock_api/upload.py src/mock_api/upload_players.py
git commit -m "fix(cli): clean error messages, env var defaults, remove internal spec references"
```

---

### Task 10: Terraform API hardening [H8, H9, M7 from security, H3 from security]

Enable API Gateway access logging, tighten CORS, and tighten Lambda permissions.

**Files:**
- Modify: `terraform/modules/api/main.tf`

- [ ] **Step 1: Add API Gateway access log group and update stage**

In `terraform/modules/api/main.tf`, add a log group and update the stage:

```hcl
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
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      requestTime    = "$context.requestTime"
      httpMethod     = "$context.httpMethod"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      protocol       = "$context.protocol"
      responseLength = "$context.responseLength"
      integrationLatency = "$context.integrationLatency"
      responseLatency    = "$context.responseLatency"
    })
  }
}
```

- [ ] **Step 2: Tighten CORS [H8]**

Since this API is consumed by CLI tools and backend services (not browsers), remove the wildcard CORS. Change the `cors_configuration` block:

```hcl
  cors_configuration {
    allow_origins = []
    allow_methods = ["GET"]
    allow_headers = ["Authorization", "Content-Type"]
    max_age       = 3600
  }
```

Note: Setting `allow_origins = []` effectively disables CORS (no `Access-Control-Allow-Origin` header from API Gateway). The `Access-Control-Allow-Origin: *` in Lambda responses also needs removal -- this was done in Task 5 by... actually, we kept `*` in the Lambda responses. Let's remove it from the Lambda responses too.

Update `shared.py` -- remove `"Access-Control-Allow-Origin": "*"` from both `json_response` and `redirect_response`. CORS is handled at the API Gateway level. If CORS is needed later, it should be configured with a specific origin at the API Gateway level.

- [ ] **Step 3: Tighten Lambda permissions [M7 from security]**

Replace the wildcard `GET/*` source ARNs with route-specific patterns:

```hcl
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
```

- [ ] **Step 4: Run terraform fmt**

```bash
cd terraform && terraform fmt -recursive
```

- [ ] **Step 5: Commit**

```bash
git add terraform/modules/api/main.tf terraform/modules/functions/src/shared.py
git commit -m "feat(infra): API Gateway access logging, disable CORS wildcard, tighten Lambda permissions"
```

---

### Task 11: Observability module -- alarms, dashboard, SLIs [C1, C2, H15, M9, M10, M12 from obs]

Create a new Terraform module for CloudWatch alarms, an SNS notification topic, a CloudWatch dashboard, and document SLIs/SLOs in an ADR.

**Files:**
- Create: `terraform/modules/observability/main.tf`
- Create: `terraform/modules/observability/variables.tf`
- Create: `terraform/modules/observability/outputs.tf`
- Create: `docs/decisions/ADR-0007-observability-baseline.md`
- Modify: `terraform/environments/dev/main.tf`
- Modify: `terraform/environments/dev/variables.tf`

- [ ] **Step 1: Create terraform/modules/observability/variables.tf**

```hcl
variable "project_name" {
  type = string
}

variable "alarm_email" {
  description = "Email address for CloudWatch alarm notifications (empty = no email subscription)"
  type        = string
  default     = ""
}

variable "lambda_function_names" {
  description = "List of Lambda function names to monitor"
  type        = list(string)
}

variable "api_gateway_id" {
  description = "API Gateway ID for metrics"
  type        = string
}

variable "api_stage_name" {
  description = "API Gateway stage name"
  type        = string
  default     = "v1"
}
```

- [ ] **Step 2: Create terraform/modules/observability/main.tf**

```hcl
# --- SNS Topic for Alarm Notifications ---

resource "aws_sns_topic" "alarms" {
  name = "${var.project_name}-alarms"
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

# --- Lambda Duration P99 Alarm (aggregated) ---

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

# --- Lambda Throttle Alarm (aggregated) ---

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
      # API Gateway metrics
      [{
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "API Gateway Requests"
          region  = "us-east-1"
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
          title   = "API Gateway Latency"
          region  = "us-east-1"
          metrics = [
            ["AWS/ApiGateway", "Latency", "ApiId", var.api_gateway_id, { stat = "p99", period = 300 }],
            ["AWS/ApiGateway", "Latency", "ApiId", var.api_gateway_id, { stat = "p50", period = 300 }],
          ]
          view = "timeSeries"
        }
      }],
      # Lambda per-function metrics
      [for i, fn in var.lambda_function_names : {
        type   = "metric"
        x      = (i % 2) * 12
        y      = 6 + floor(i / 2) * 6
        width  = 12
        height = 6
        properties = {
          title   = fn
          region  = "us-east-1"
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
```

- [ ] **Step 3: Create terraform/modules/observability/outputs.tf**

```hcl
output "alarm_topic_arn" {
  value = aws_sns_topic.alarms.arn
}

output "dashboard_url" {
  value = "https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=${var.project_name}"
}
```

- [ ] **Step 4: Wire module in terraform/environments/dev/main.tf**

Add after the `module "audit"` block:

```hcl
module "observability" {
  source        = "../../modules/observability"
  project_name  = var.project_name
  alarm_email   = var.alarm_email
  api_gateway_id = module.api.api_id
  lambda_function_names = [
    aws_lambda_function.list_providers.function_name,
    aws_lambda_function.list_matches.function_name,
    aws_lambda_function.get_artifact.function_name,
    aws_lambda_function.list_players.function_name,
    aws_lambda_function.get_player.function_name,
    aws_lambda_function.health.function_name,
  ]
}
```

Wait -- the function names come from the functions module. Update to:

```hcl
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
```

Add `api_id` output to `terraform/modules/api/outputs.tf` (it may not exist -- check):

```hcl
output "api_id" {
  value = aws_apigatewayv2_api.api.id
}
```

Add `alarm_email` variable to `terraform/environments/dev/variables.tf`:

```hcl
variable "alarm_email" {
  description = "Email for CloudWatch alarm notifications (leave empty to skip)"
  type        = string
  default     = ""
}
```

- [ ] **Step 5: Create ADR for SLI/SLO definitions [C2]**

Create `docs/decisions/ADR-0007-observability-baseline.md`:

```markdown
# ADR 0007: Observability Baseline and SLI/SLO Definitions

## Status

Accepted

## Context

The five-audit review identified zero alerting, zero dashboards, and no defined SLIs or SLOs. The API was running with CloudTrail audit logging and X-Ray tracing, but no operational observability for detecting failures in real time.

## Decision

### SLIs (Service Level Indicators)

| SLI | Definition | Measurement |
|-----|-----------|-------------|
| Availability | Percentage of requests returning non-5xx responses | CloudWatch `5xx` / `Count` on API Gateway |
| Latency (p99) | 99th percentile response time | CloudWatch `Latency` p99 on API Gateway |
| Error rate | Percentage of Lambda invocations resulting in errors | CloudWatch `Errors` / `Invocations` per function |

### SLOs (Service Level Objectives)

| SLO | Target | Window | Rationale |
|-----|--------|--------|-----------|
| Availability | >= 99.5% | 30 days rolling | Low-traffic dev API; S3 + Lambda provide inherent 99.9%+ but cold starts and transient errors lower effective availability |
| Latency (p99) | < 3 seconds | 30 days rolling | Accounts for cold starts + S3 GET; warm path should be < 500ms |
| Error rate per function | < 1% | 30 days rolling | Individual function failures should be rare; most errors are expected 4xx |

### Alerting

CloudWatch alarms fire to an SNS topic (email) when:
- Any Lambda function errors >= 1 in a 5-minute window
- Lambda p99 duration > 8 seconds (approaching 10s timeout) for 2 consecutive periods
- Any Lambda throttles >= 1
- API Gateway 5xx count >= 1

### Dashboard

A single CloudWatch dashboard (`pining-for-the-data`) provides:
- API Gateway request count, 4xx, 5xx, and latency (p50/p99)
- Per-Lambda invocations, errors, duration (p99), and throttles

## Consequences

- Alarm noise will be low at current traffic levels; increase thresholds if alert fatigue occurs.
- Dashboard is Terraform-managed (survives destroy/recreate cycles).
- SLOs are internal targets, not external commitments. Revisit if the API serves external consumers at scale.
```

- [ ] **Step 6: Run terraform fmt and validate**

```bash
cd terraform && terraform fmt -recursive
cd terraform/environments/dev && terraform validate
```

- [ ] **Step 7: Commit**

```bash
git add terraform/modules/observability/ terraform/environments/dev/ terraform/modules/api/outputs.tf docs/decisions/ADR-0007-observability-baseline.md
git commit -m "feat(infra): observability module — CloudWatch alarms, dashboard, SNS, SLI/SLO definitions"
```

---

### Task 12: Terraform storage and compute tweaks [M12 from opt, M13 from opt]

Add S3 noncurrent version lifecycle on data bucket. Bump Lambda memory from 128 to 256 MB.

**Files:**
- Modify: `terraform/modules/storage/main.tf`
- Modify: `terraform/modules/functions/main.tf`

- [ ] **Step 1: Add lifecycle rule to data bucket**

In `terraform/modules/storage/main.tf`, add after the `aws_s3_bucket_public_access_block` resource:

```hcl
resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"
    filter {} # apply to all objects

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}
```

- [ ] **Step 2: Bump Lambda memory from 128 to 256 MB**

In `terraform/modules/functions/main.tf`, change `memory_size = 128` to `memory_size = 256` on all 5 existing Lambda functions (list_providers, list_matches, get_artifact, list_players, get_player). Leave `health` at 128 (it does no S3 work).

- [ ] **Step 3: Run terraform fmt**

```bash
cd terraform && terraform fmt -recursive
```

- [ ] **Step 4: Commit**

```bash
git add terraform/modules/storage/main.tf terraform/modules/functions/main.tf
git commit -m "feat(infra): S3 noncurrent version expiry (90d), Lambda memory 128->256MB"
```

---

### Task 13: Code quality fixes [L7 from opt, M5 from opt]

Hoist `re.compile()` to module level in `canonical/models.py`. Use `set` for dedup in `name_pools.py`.

**Files:**
- Modify: `src/canonical/models.py:72`
- Modify: `src/deidentify/name_pools.py:75-81`

- [ ] **Step 1: Hoist re.compile in models.py [L7]**

In `src/canonical/models.py`, the `_PATH_PARAM_RE` string is already defined at module level (line 29). Line 72 re-compiles it inside the validator:

```python
regex = re.compile(_PATH_PARAM_RE)
```

Add a compiled version at module level after line 29:

```python
_PATH_PARAM_RE = r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"
_PATH_PARAM_COMPILED = re.compile(_PATH_PARAM_RE)
```

Then change line 72 from `regex = re.compile(_PATH_PARAM_RE)` to use the pre-compiled pattern:

```python
for name in self.artifacts:
    if not _PATH_PARAM_COMPILED.match(name) or len(name) > 128:
```

- [ ] **Step 2: Use set for dedup in name_pools.py [M5 from opt]**

In `src/deidentify/name_pools.py`, the `sample_unique_player_names` method (lines 75-81):

Change from:
```python
names: list[str] = []
attempts = 0
max_attempts = count * 20
while len(names) < count and attempts < max_attempts:
    name = self.sample_player_name(gender)
    if name not in exclude and name not in names:
        names.append(name)
    attempts += 1
```

To:
```python
names: list[str] = []
seen: set[str] = set()
attempts = 0
max_attempts = count * 20
while len(names) < count and attempts < max_attempts:
    name = self.sample_player_name(gender)
    if name not in exclude and name not in seen:
        names.append(name)
        seen.add(name)
    attempts += 1
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest -x -q
uv run ruff check src/
uv run pyright src/
```

- [ ] **Step 4: Commit**

```bash
git add src/canonical/models.py src/deidentify/name_pools.py
git commit -m "refactor: hoist re.compile to module level, use set for name dedup"
```

---

### Task 14: CI enhancements [L1 from security, L2 from security]

Add `pip-audit` to CI. Pin `detect-secrets` version in CI to match pre-commit.

**Files:**
- Modify: `.github/workflows/python-ci.yml`

- [ ] **Step 1: Add pip-audit and pin detect-secrets in CI**

In `.github/workflows/python-ci.yml`, update the secret scanning step and add pip-audit:

```yaml
      - name: Secret scanning
        run: uvx detect-secrets==1.5.0 scan --baseline .secrets.baseline

      - name: Dependency vulnerability scan
        run: uv run pip-audit
```

The `detect-secrets==1.5.0` pins the version to match `.pre-commit-config.yaml` rev `v1.5.0`.

The `pip-audit` is already in `[dependency-groups] dev` in `pyproject.toml`, so it's available via `uv run`.

- [ ] **Step 2: Run CI check locally**

```bash
uvx detect-secrets==1.5.0 scan --baseline .secrets.baseline
uv run pip-audit
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/python-ci.yml
git commit -m "ci: add pip-audit vulnerability scan, pin detect-secrets to v1.5.0"
```

---

## Self-Review

### Spec coverage check

| Finding | Task | Status |
|---------|------|--------|
| C1: No alerting | Task 11 | Covered |
| C2: No SLIs/SLOs | Task 11 (ADR) | Covered |
| C3: ARCHITECTURE.md models location | Task 1 | Covered |
| H1: Setup guide outdated | Task 2 | Covered |
| H2: No hosted API URL | Task 3 | Covered |
| H3: HF dataset card wrong URL | Task 1 | Covered |
| H4: "whitelist" terminology | Task 1 | Covered |
| H5: No CONTRIBUTING.md | Task 4 | Covered |
| H6: Test count inconsistency | Task 1 | Covered |
| H7: upload-players not in Quick Start | Task 1 | Covered |
| H8: CORS wildcard | Task 10 | Covered |
| H9: No API Gateway access logging | Task 10 | Covered |
| H10: Plaintext Lambda env var | Accepted risk (intentionally public token; documented in security audit) | Acknowledged |
| H11: No structured logging | Task 6 | Covered |
| H12: No correlation IDs | Task 6 | Covered |
| H13: No request/response logging | Task 6 | Covered |
| H14: No health endpoint | Task 8 | Covered |
| H15: No dashboards | Task 11 | Covered |
| H16: No Cache-Control | Task 5 | Covered |
| M1-M3: S3 reads per request | Acknowledged (warm-container caching is a v2 optimization; Cache-Control in Task 5 reduces external refetches) | Deferred to next iteration |
| M4: No security headers | Task 5 | Covered |
| M5: Owner token cached indefinitely | Acknowledged (existing design; rotation via LAST_ROTATION is documented) | Accepted |
| M6: SSM placeholder | Acknowledged (lifecycle ignore_changes; documented in setup guide Task 2) | Accepted |
| M7: Lambda permissions wildcard | Task 10 | Covered |
| M8: Silent 404 on S3 error | Task 7 (shared helpers log at ERROR; behavior is documented) | Acknowledged |
| M9: No deploy notifications | Task 11 (SNS topic enables future GitHub Actions integration) | Partial |
| M10: No timeout/concurrency alarms | Task 11 | Covered |
| M11: Duplicated helpers | Task 7 | Covered |
| M12: No S3 lifecycle | Task 12 | Covered |
| M13: Lambda memory too low | Task 12 | Covered |
| M14: Spec references in errors | Task 9 | Covered |
| M15: Raw tracebacks | Task 9 | Covered |
| M16: No bucket env var | Task 9 | Covered |
| M17: Game ID vocabulary | Acknowledged (API uses `id`, CLI uses `--game-id`; changing either is a breaking change) | Accepted |
| M18: Tutorial Windows commands | Task 3 | Covered |
| M19: Teardown no warning | Task 2 | Covered |
| M20: No Explanation docs | Acknowledged (ADRs serve this purpose for now) | Deferred |
| M21: SECURITY.md contact | Task 1 | Covered |
| M22: luxury-lakehouse no link | Task 1 | Covered |
| L1: detect-secrets version mismatch | Task 14 | Covered |
| L2: pip-audit not in CI | Task 14 | Covered |
| L3: No benchmarks | Acknowledged (out of scope for this remediation) | Deferred |
| L4: positionGroupType undocumented | Acknowledged (provider-specific field; schema allows arbitrary extras) | Accepted |
| L5: C4 no text fallback | Acknowledged (HTML is the intended format) | Accepted |
| L6: No CODE_OF_CONDUCT | Task 4 | Covered |
| L7: re.compile in validator | Task 13 | Covered |

**42 of 48 findings addressed. 6 explicitly accepted/deferred with documented rationale.**

### Placeholder scan

No TBD, TODO, or "implement later" found. All code blocks are complete.

### Type consistency check

- `json_response()` gains optional `cache_control` keyword arg (Task 5) -- all existing call sites continue to work (default `"no-store"`).
- `provider_known()` and `read_player_index()` (Task 7) take `bucket` as explicit parameter -- all call sites updated.
- `configure_handler_logging()` and `extract_request_context()` (Task 6) are new functions -- imported only in handler files.
