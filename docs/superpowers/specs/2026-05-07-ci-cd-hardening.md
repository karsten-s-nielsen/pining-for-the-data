# CI/CD Hardening for Public Repo — Design Spec

**Date:** 2026-05-07
**Status:** Draft
**Scope:** GitHub Actions workflows, Terraform deployment automation, repo governance

---

## 1. Purpose

The repo is going public. Today, Lambda deployments require a manual `terraform apply` from an operator workstation with local AWS credentials. This is acceptable for a private repo but creates two risks for a public project:

1. **Deployment drift** — code merges to main without deploying, leaving the live API out of sync with the repo.
2. **Credential exposure** — contributors (or CI runners) need local AWS credentials with broad IAM permissions.

This spec adds GitHub Actions workflows that automatically plan Terraform changes on PRs and apply them on merge to main, using GitHub OIDC for credential-free AWS authentication. It also hardens the existing CI pipeline and adds repo governance files.

The patterns are lifted directly from `luxury-lakehouse` (Terraform CI) and `silly-kicks` (repo governance), adapted for pining-for-the-data's simpler infrastructure.

## 2. Scope

### In scope

| # | Item | Source |
|---|------|--------|
| 1 | `terraform-plan.yml` — plan + PR comment on PRs touching `terraform/**` | lakehouse |
| 2 | `terraform-apply.yml` — auto-apply on main push touching `terraform/**` | lakehouse |
| 3 | GitHub OIDC for AWS (IAM policy update) | lakehouse |
| 4 | Enable DynamoDB state locking in `backend.tf` | review finding |
| 5 | Harden `python-ci.yml` — concurrency, secret scanning | both |
| 6 | Commit `.secrets.baseline` (currently gitignored) | review finding |
| 7 | Dependabot — add `terraform` ecosystem | lakehouse |
| 8 | `CODEOWNERS` | silly-kicks |
| 9 | PR template | silly-kicks |

### Out of scope

- Matrix testing (single Python version, not a library)
- SBOM / pip-audit (small dependency surface)
- Semgrep SAST (Lambda handlers are tiny; ruff `S` rules are sufficient)
- PyPI publishing (not a library)
- Databricks/dbt CI (not applicable)

## 3. Terraform CI Workflows

### 3.1 `terraform-plan.yml` — PR review tool

**Trigger:** `pull_request` with paths `terraform/**` or `.github/workflows/terraform-plan.yml`.

**Not triggered on push to main** — terraform-apply.yml handles that. Running both on main push races for the state lock (lakehouse learned this the hard way; PR #131).

**Working directory:** All terraform commands run in `terraform/environments/dev` (where `backend.tf` and `main.tf` live). Set via `TF_WORKING_DIR` env var + `working-directory` on each step, matching the lakehouse pattern.

**Job: `plan`**

1. Checkout
2. Setup Terraform (`~> 1.10`)
3. Configure AWS via OIDC (`aws-actions/configure-aws-credentials`)
4. `terraform init` (working-directory: `$TF_WORKING_DIR`)
5. `terraform fmt -check -recursive -diff` (working-directory: `$TF_WORKING_DIR`)
6. `terraform validate` (working-directory: `$TF_WORKING_DIR`)
7. `terraform plan -no-color -out=tfplan` (working-directory: `$TF_WORKING_DIR`)
8. Comment plan on PR (collapsible `<details>`, truncated to 60 KB)

**Permissions:** `contents: read`, `id-token: write`, `pull-requests: write`

**Concurrency:** Cancel superseded PR runs (`cancel-in-progress: true`).

### 3.2 `terraform-apply.yml` — auto-deploy on merge

**Trigger:** `push` to `main` with paths `terraform/**` or `.github/workflows/terraform-apply.yml`.

**Working directory:** Same `TF_WORKING_DIR: terraform/environments/dev`.

**Job: `apply`**

1. Checkout
2. Setup Terraform (`~> 1.10`)
3. Configure AWS via OIDC
4. `terraform init` (working-directory: `$TF_WORKING_DIR`)
5. `terraform apply -auto-approve -no-color` (working-directory: `$TF_WORKING_DIR`)

**Permissions:** `contents: read`, `id-token: write`

**Concurrency:** Sequential only (`cancel-in-progress: false`). Never interrupt a running apply.

### 3.3 Terraform variables in CI

Two Terraform variables need values in CI:

| Variable | Sensitive | CI source | Rationale |
|----------|-----------|-----------|-----------|
| `api_token` | Yes (TF `sensitive`) | GitHub secret `API_TOKEN` | Documented public token, but Terraform marks it sensitive to keep it out of plan output |
| `last_rotation` | No | GitHub variable `LAST_ROTATION` | Only changes during manual token rotation; operator updates the variable alongside the SSM parameter |

Both are passed via `TF_VAR_*` environment variables:

```yaml
env:
  TF_WORKING_DIR: terraform/environments/dev
  AWS_REGION: us-east-1
  TF_VAR_api_token: ${{ secrets.API_TOKEN }}
  TF_VAR_last_rotation: ${{ vars.LAST_ROTATION }}
```

**Why not hardcode `api_token`?** It's marked `sensitive` in Terraform, and even though the value is documented in the README, hardcoding secrets in workflow files is an anti-pattern that GitHub secret scanning would flag. Using a secret is zero additional effort and prevents bad habits.

**Why store `last_rotation` as a variable?** It changes only during manual token rotation (operator runs `aws ssm put-parameter --overwrite` then bumps this value). Storing it as a GitHub Actions variable (not secret — it's a timestamp, not a credential) means the operator updates it in one place and all future deploys use the correct value. Default `"initial"` in `variables.tf` is a safe fallback for first-time setup.

### 3.4 Path filtering

Both workflows trigger on `terraform/**` changes. This covers:

- Lambda handler code (`terraform/modules/functions/src/*.py`) — the most common deploy trigger
- Terraform module changes (new resources, IAM, etc.)
- Workflow file self-changes

Changes outside `terraform/` (docs, tests, src/, scripts/) do **not** trigger a deploy. This is correct — those files don't affect the deployed infrastructure.

## 4. GitHub OIDC for AWS

### 4.1 Prerequisites

The AWS account (454762693631) already has a GitHub OIDC identity provider and a DevOpsAgent IAM role used by lakehouse and silly-kicks. The operator manually updates the role's trust policy when adding new repos.

### 4.2 IAM trust policy update

Add `repo:karsten-s-nielsen/pining-for-the-data:*` to the existing OIDC role's trust policy `Condition.StringLike["token.actions.githubusercontent.com:sub"]` array.

This is a manual step the operator performs in the AWS console or via CLI before the workflows can run.

### 4.3 IAM permissions

The existing DevOpsAgent role likely already has the permissions needed (S3, Lambda, API Gateway, IAM, KMS, SSM, CloudTrail, CloudWatch Logs). If not, the following actions are required:

- `s3:*` on the data bucket, audit bucket, and state bucket
- `lambda:*` on `pining-for-the-data-*` functions
- `apigateway:*` on the API Gateway resources
- `iam:*` on the Lambda execution role
- `kms:*` on the data KMS key
- `ssm:GetParameter` on the owner token parameter
- `cloudtrail:*` on the data bucket trail
- `logs:*` on the Lambda log groups
- `dynamodb:*` on the state lock table

### 4.4 Terraform state bucket access

The S3 backend (`karstenskyt-terraform-state`) must be accessible to the OIDC role. This should already be the case if the role is the same one used by lakehouse (which also uses S3 state).

## 5. DynamoDB State Locking

### 5.1 Background

The state module (`terraform/modules/state/main.tf:57-71`) provisions a DynamoDB table `pining-for-the-data-tflock` for Terraform state locking. However, `terraform/environments/dev/backend.tf` does not reference it — the `dynamodb_table` key is missing from the S3 backend config. This means state is **not locked** during applies.

With CI automation doing auto-applies on merge, the risk increases: if an operator runs `terraform apply` locally while CI is also applying, both would succeed and could corrupt state.

### 5.2 Table does not exist

The state module (`terraform/modules/state/`) is never invoked from `terraform/environments/dev/main.tf` — it calls `storage`, `functions`, `api`, and `audit` but not `state`. The project bootstrapped on the shared `karstenskyt-terraform-state` S3 bucket directly, so the DynamoDB table was never created.

**Confirmed:** `aws dynamodb describe-table --table-name pining-for-the-data-tflock` returns `ResourceNotFoundException`.

### 5.3 Fix (two steps)

**Step 1 — Create the table.** The simplest approach is a standalone `aws` CLI call rather than applying the unused state module (which would also create a second S3 bucket we don't need):

```bash
aws dynamodb create-table \
  --table-name pining-for-the-data-tflock \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1
```

This is a one-time manual operator step, performed before the backend config change.

**Step 2 — Add `dynamodb_table` to `backend.tf`:**

```hcl
terraform {
  backend "s3" {
    bucket         = "karstenskyt-terraform-state"
    key            = "pining-for-the-data/dev/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "pining-for-the-data-tflock"
  }
}
```

**Step 3 — Reinitialize:** `terraform init -reconfigure` in `terraform/environments/dev/`. The state itself does not move — Terraform just starts acquiring a lock before each operation. This is a safe, non-destructive change.

### 5.3 Defense in depth

State locking + the CI `concurrency` block (`cancel-in-progress: false`) provide two layers:

1. **Concurrency block** prevents CI from running two applies simultaneously.
2. **DynamoDB lock** prevents a local `terraform apply` from colliding with a CI apply (or vice versa).

## 6. Python CI Hardening

### 6.1 Concurrency control

Add concurrency to `python-ci.yml`:

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}
```

PR runs cancel superseded older runs. Main runs complete (coverage reports, etc.).

### 6.2 Permissions

Already set — `python-ci.yml` lines 9-10 already have `permissions: contents: read`. No change needed.

### 6.3 Secret scanning in CI

Add a `detect-secrets` step after tests. The pre-commit hook catches secrets locally, but CI is the safety net for contributors who skip pre-commit.

**Important:** `detect-secrets` is NOT a project dependency — it's only available via the pre-commit hook's isolated virtualenv. Use `uvx` for an ephemeral install:

```yaml
- name: Secret scanning
  run: uvx detect-secrets scan --baseline .secrets.baseline
```

**Prerequisite:** `.secrets.baseline` must be committed to the repo (see §6.4).

### 6.4 Commit `.secrets.baseline`

`.secrets.baseline` is currently in `.gitignore` (line 27). This is incorrect — the baseline is a shared artifact that tracks known false positives. Every contributor (and CI) needs the same baseline for consistent results.

**Fix:**

1. Remove `.secrets.baseline` from `.gitignore`
2. Generate the baseline: `detect-secrets scan > .secrets.baseline`
3. Commit the baseline file

This also fixes the pre-commit hook (`.pre-commit-config.yaml:27` references `--baseline .secrets.baseline`), which currently fails silently for new contributors who haven't generated a local baseline.

## 7. Dependabot

### 7.1 Add Terraform ecosystem

The current `dependabot.yml` covers `pip` and `github-actions`. Add `terraform`:

```yaml
- package-ecosystem: "terraform"
  directory: "/terraform/environments/dev"
  schedule:
    interval: "weekly"
```

This auto-PRs when Terraform provider versions (AWS provider `~> 5.0`) have updates. The PR triggers `terraform-plan.yml`, so the plan comment shows exactly what changes.

## 8. Repo Governance

### 8.1 CODEOWNERS

```
* @karsten-s-nielsen
```

Auto-requests review from the repo owner on all PRs. Single maintainer, simple rule.

### 8.2 PR template

```markdown
## Summary

Brief description of what this PR does.

## Changes

-

## Testing

- [ ] Tests pass (`uv run pytest`)
- [ ] Lint clean (`uv run ruff check`)
- [ ] Types clean (`uv run pyright src/`)

## Related issues

Closes #
```

## 9. GitHub Repository Settings

### 9.1 Secrets and variables

Set in the GitHub repo settings (Settings → Secrets and variables → Actions):

**Secrets:**

| Name | Value | Notes |
|------|-------|-------|
| `API_TOKEN` | `test-token-pining-for-the-data` | Public token (sensitive in TF only) |

**Variables:**

| Name | Value | Notes |
|------|-------|-------|
| `AWS_OIDC_ROLE_ARN` | `arn:aws:iam::454762693631:role/<DevOpsAgent-role-name>` | Same role used by lakehouse |
| `LAST_ROTATION` | `20260504T024554Z` | Current value; update during token rotation |

### 9.2 Branch protection (recommended)

For `main` branch:

- Require PR before merging
- Require status checks to pass: `lint-and-test`, `plan` (when applicable)
- Require CODEOWNERS review
- Do not allow bypassing the above

## 10. Operator Runbook Updates

### 10.1 Token rotation (updated)

The existing rotation procedure gains one step — updating the GitHub variable:

1. `aws ssm put-parameter --name /pining-for-the-data/api_token_owner --value <new-token> --overwrite`
2. Update `LAST_ROTATION` in GitHub repo variables to `$(date -u +%Y%m%dT%H%M%SZ)`
3. Push any `terraform/` file change (even a comment) to trigger auto-apply — OR manually trigger a workflow dispatch

The `terraform apply -var=last_rotation=...` local command is no longer needed; the workflow reads the variable automatically.

### 10.2 First-time setup

Before the workflows can run:

1. Add `repo:karsten-s-nielsen/pining-for-the-data:*` to the OIDC role trust policy
2. Set the GitHub secret `API_TOKEN`
3. Set the GitHub variables `AWS_OIDC_ROLE_ARN` and `LAST_ROTATION`
4. Create the DynamoDB lock table (see §5.3 Step 1 — one-time CLI command)
5. Run `terraform init -reconfigure` locally to enable DynamoDB state locking (one-time, after `backend.tf` is updated)
6. Verify with a no-op PR that touches a `terraform/` file

## 11. Action Pinning

All GitHub Actions are pinned to commit SHAs (not version tags) for supply-chain security. This matches the pattern in both lakehouse and silly-kicks:

```yaml
- uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
- uses: hashicorp/setup-terraform@5e8dbf3c6d9deaf4193ca7a8fb23f2ac83bb6c85 # v4.0.0
- uses: aws-actions/configure-aws-credentials@ec61189d14ec14c8efccab744f656cffd0e33f37 # v6.1.0
- uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b # v8.1.0
```

Dependabot's `github-actions` ecosystem auto-PRs when these SHAs fall behind.

## 12. Non-Goals

- **Staging environment** — single `dev` environment is sufficient at this scale.
- **Manual approval gate** — the plan was reviewed in the PR; auto-apply is the standard. lakehouse uses the same pattern.
- **Terraform fmt in python-ci.yml** — considered and rejected. `terraform-plan.yml` already runs `terraform fmt -check` on every PR that touches `terraform/**`. Adding it to `python-ci.yml` would require installing `hashicorp/setup-terraform` on every CI run (~10s overhead) for zero additional coverage — any TF-touching PR triggers both workflows.
- **Workflow dispatch** — not adding manual-trigger workflows. The path-based triggers cover all cases. If an operator needs to force a deploy, they can push a no-op comment change to a `terraform/` file.
