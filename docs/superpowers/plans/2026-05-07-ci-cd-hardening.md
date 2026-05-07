# CI/CD Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Terraform CI/CD (plan on PR, auto-apply on merge), harden Python CI, and add repo governance — all prerequisite for making the repo public.

**Architecture:** Two new GitHub Actions workflows (`terraform-plan.yml`, `terraform-apply.yml`) using OIDC for AWS auth. Existing `python-ci.yml` gains concurrency control and secret scanning. DynamoDB state locking enabled in `backend.tf`. Governance files (CODEOWNERS, PR template) follow silly-kicks patterns.

**Tech Stack:** GitHub Actions, Terraform ~1.10, AWS OIDC, detect-secrets (via uvx), DynamoDB

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `.github/workflows/terraform-plan.yml` | Plan + PR comment on PRs touching `terraform/**` |
| Create | `.github/workflows/terraform-apply.yml` | Auto-apply on main push touching `terraform/**` |
| Modify | `.github/workflows/python-ci.yml` | Add concurrency control + secret scanning step |
| Modify | `.github/dependabot.yml` | Add terraform ecosystem |
| Modify | `.gitignore:27,43` | Remove `.secrets.baseline` and `.terraform.lock.hcl` from ignore list |
| Create | `.secrets.baseline` | Shared detect-secrets baseline for CI + pre-commit |
| Commit | `terraform/environments/dev/.terraform.lock.hcl` | Provider lock file — enables dependabot terraform updates |
| Modify | `terraform/environments/dev/backend.tf` | Add `dynamodb_table` for state locking |
| Create | `.github/CODEOWNERS` | Auto-request review from repo owner |
| Create | `.github/pull_request_template.md` | PR checklist template |

---

### Task 1: Secrets baseline — unblock CI secret scanning

The `.secrets.baseline` file is gitignored but referenced by both pre-commit and the planned CI step. Fix this first so Task 2 can add the CI step.

**Files:**
- Modify: `.gitignore:27`
- Create: `.secrets.baseline`

- [ ] **Step 1: Remove `.secrets.baseline` from `.gitignore`**

Edit `.gitignore` line 27. Change:

```
# Pre-commit
.secrets.baseline
```

to:

```
# Pre-commit
```

(Delete the `.secrets.baseline` line entirely. Keep the `# Pre-commit` comment for section structure.)

- [ ] **Step 2: Generate the secrets baseline**

Run:
```bash
uvx detect-secrets scan > .secrets.baseline
```

Expected: Creates `.secrets.baseline` in the repo root. The file is a JSON document listing any detected "secrets" (false positives like test tokens in fixture files). Verify it's valid JSON:

```bash
uv run python -c "import json; json.load(open('.secrets.baseline'))" && echo "Valid JSON"
```

- [ ] **Step 3: Verify pre-commit hook works with the baseline**

Run:
```bash
uv run pre-commit run detect-secrets --all-files
```

Expected: `Passed` (or specific false-positive entries that are already in the baseline).

- [ ] **Step 4: Commit**

```bash
git add .gitignore .secrets.baseline
git commit -m "chore: commit .secrets.baseline for CI secret scanning

Remove .secrets.baseline from .gitignore so the baseline is shared
across contributors and CI. Previously gitignored, causing the
pre-commit hook to operate without a baseline."
```

---

### Task 2: Harden `python-ci.yml` — concurrency + secret scanning

**Files:**
- Modify: `.github/workflows/python-ci.yml`

- [ ] **Step 1: Add concurrency control and secret scanning step**

Replace the entire content of `.github/workflows/python-ci.yml` with:

```yaml
name: Python CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.12"]

    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2

      - name: Install uv
        uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b # v8.1.0
        with:
          enable-cache: true

      - name: Set up Python ${{ matrix.python-version }}
        run: uv python install ${{ matrix.python-version }}

      - name: Install dependencies
        run: uv sync --frozen --extra dev

      - name: Lint with ruff
        run: |
          uv run ruff check src/
          uv run ruff format --check src/

      - name: Type check with pyright
        run: uv run pyright src/

      - name: Run tests
        run: uv run pytest --cov=src --cov-report=term-missing

      - name: Secret scanning
        run: uvx detect-secrets scan --baseline .secrets.baseline
```

Changes from the original:
- Added `concurrency` block (lines 12-14): PR runs cancel superseded; main runs complete
- Added `Secret scanning` step at the end: uses `uvx` (ephemeral install, not a project dependency)

- [ ] **Step 2: Verify the workflow YAML is valid**

```bash
uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/python-ci.yml')); print('Valid YAML')"
```

Expected: `Valid YAML`

- [ ] **Step 3: Verify existing tests still pass**

```bash
uv run pytest src/tests/ -q --tb=short
```

Expected: All 155 tests pass (no regressions from config-only changes).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/python-ci.yml
git commit -m "ci: add concurrency control and secret scanning to Python CI

PR runs cancel superseded older runs; main runs complete.
detect-secrets via uvx scans for leaked secrets as a CI safety net
(complements the pre-commit hook for contributors who skip it)."
```

---

### Task 3: Repo governance — CODEOWNERS, PR template, dependabot

**Files:**
- Create: `.github/CODEOWNERS`
- Create: `.github/pull_request_template.md`
- Modify: `.github/dependabot.yml`
- Modify: `.gitignore:43` (remove `.terraform.lock.hcl`)
- Commit: `terraform/environments/dev/.terraform.lock.hcl`

- [ ] **Step 1: Create CODEOWNERS**

Create `.github/CODEOWNERS` with:

```
* @karsten-s-nielsen
```

- [ ] **Step 2: Create PR template**

Create `.github/pull_request_template.md` with:

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

- [ ] **Step 3: Remove `.terraform.lock.hcl` from `.gitignore` and commit it**

Dependabot's terraform integration reads `.terraform.lock.hcl` to detect provider version updates. Without a committed lock file, the terraform ecosystem entry is inert.

Edit `.gitignore` line 43. Change:

```
.terraform.lock.hcl
```

to remove the line entirely. The `.terraform/` directory (line 42) stays ignored — that's the local plugin cache, not the lock file.

Then commit the existing lock file:

```bash
git add terraform/environments/dev/.terraform.lock.hcl
```

If the file doesn't exist locally, generate it:

```bash
cd terraform/environments/dev && terraform init && cd -
git add terraform/environments/dev/.terraform.lock.hcl
```

- [ ] **Step 4: Add terraform ecosystem to dependabot**

Replace the entire content of `.github/dependabot.yml` with:

```yaml
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"

  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"

  - package-ecosystem: "terraform"
    directory: "/terraform/environments/dev"
    schedule:
      interval: "weekly"
```

Change from original: added the `terraform` ecosystem block (lines 13-16).

- [ ] **Step 5: Verify YAML files are valid**

```bash
uv run python -c "import yaml; yaml.safe_load(open('.github/dependabot.yml')); print('dependabot.yml: Valid')"
```

Expected: `dependabot.yml: Valid`

- [ ] **Step 6: Commit**

```bash
git add .gitignore terraform/environments/dev/.terraform.lock.hcl .github/CODEOWNERS .github/pull_request_template.md .github/dependabot.yml
git commit -m "chore: add CODEOWNERS, PR template, terraform dependabot, commit lock file

CODEOWNERS auto-requests review from repo owner on all PRs.
PR template enforces a checklist (tests, lint, types).
Dependabot now covers terraform provider versions alongside pip
and github-actions. Commit .terraform.lock.hcl (removed from
.gitignore) so dependabot can detect provider updates."
```

---

### Task 4: DynamoDB state locking — update `backend.tf`

The DynamoDB table does not exist in AWS yet. The operator will create it manually (spec §5.3 Step 1) and run `terraform init -reconfigure` after this code change is merged. This task only updates the backend config file.

**Files:**
- Modify: `terraform/environments/dev/backend.tf`

- [ ] **Step 1: Add `dynamodb_table` to backend config**

Replace the entire content of `terraform/environments/dev/backend.tf` with:

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

Change from original: added `dynamodb_table` line; aligned `=` signs for readability.

- [ ] **Step 2: Verify HCL syntax**

```bash
cd terraform/environments/dev && terraform fmt -check backend.tf && echo "Format OK" && cd -
```

Expected: `Format OK` (no output from `fmt -check` means the file is already formatted).

If `terraform fmt -check` reports a diff, run `terraform fmt backend.tf` to fix it, then re-check.

- [ ] **Step 3: Commit**

```bash
git add terraform/environments/dev/backend.tf
git commit -m "infra: enable DynamoDB state locking in backend.tf

Add dynamodb_table = pining-for-the-data-tflock to the S3 backend.
The table must be created by the operator (aws dynamodb create-table)
and terraform init -reconfigure run before this takes effect.
Prevents state corruption from concurrent local + CI applies."
```

---

### Task 5: Create `terraform-plan.yml`

**Files:**
- Create: `.github/workflows/terraform-plan.yml`

- [ ] **Step 1: Create the workflow file**

Create `.github/workflows/terraform-plan.yml` with:

```yaml
name: Terraform Plan

# Plan is a PR-review tool only. On push-to-main, terraform-apply.yml runs
# and publishes its own plan inline, so a parallel Plan job would race Apply
# for the S3 state lock. See luxury-lakehouse PR #131.
on:
  pull_request:
    paths:
      - "terraform/**"
      - ".github/workflows/terraform-plan.yml"

env:
  TF_WORKING_DIR: terraform/environments/dev
  AWS_REGION: us-east-1
  TF_VAR_api_token: ${{ secrets.API_TOKEN }}
  TF_VAR_last_rotation: ${{ vars.LAST_ROTATION }}

permissions:
  contents: read
  id-token: write
  pull-requests: write

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  plan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2

      - name: Setup Terraform
        uses: hashicorp/setup-terraform@5e8dbf3c6d9deaf4193ca7a8fb23f2ac83bb6c85 # v4.0.0
        with:
          terraform_version: "~> 1.10"

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@ec61189d14ec14c8efccab744f656cffd0e33f37 # v6.1.0
        with:
          role-to-assume: ${{ vars.AWS_OIDC_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}

      - name: Terraform Init
        working-directory: ${{ env.TF_WORKING_DIR }}
        run: terraform init

      - name: Terraform Format Check
        working-directory: ${{ env.TF_WORKING_DIR }}
        run: terraform fmt -check -recursive -diff

      - name: Terraform Validate
        working-directory: ${{ env.TF_WORKING_DIR }}
        run: terraform validate

      - name: Terraform Plan
        working-directory: ${{ env.TF_WORKING_DIR }}
        run: terraform plan -no-color -out=tfplan

      - name: Comment Plan on PR
        uses: actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3 # v9.0.0
        if: github.event_name == 'pull_request'
        with:
          script: |
            const { execSync } = require('child_process');
            const plan = execSync('terraform show -no-color tfplan', {
              cwd: '${{ env.TF_WORKING_DIR }}'
            }).toString();

            const truncated = plan.length > 60000
              ? plan.substring(0, 60000) + '\n... (truncated)'
              : plan;

            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: `### Terraform Plan\n<details>\n<summary>Show plan output</summary>\n\n\`\`\`hcl\n${truncated}\n\`\`\`\n</details>`
            });
```

- [ ] **Step 2: Verify YAML syntax**

```bash
uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/terraform-plan.yml')); print('Valid YAML')"
```

Expected: `Valid YAML`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/terraform-plan.yml
git commit -m "ci: add Terraform Plan workflow for PR review

Runs terraform fmt, validate, and plan on PRs touching terraform/.
Comments the plan output on the PR (collapsible, truncated to 60KB).
Uses GitHub OIDC for AWS auth — no static credentials.
PR-only: not triggered on push to main (avoids state-lock race with
terraform-apply.yml)."
```

---

### Task 6: Create `terraform-apply.yml`

**Files:**
- Create: `.github/workflows/terraform-apply.yml`

- [ ] **Step 1: Create the workflow file**

Create `.github/workflows/terraform-apply.yml` with:

```yaml
name: Terraform Apply

# Auto-apply on push to main when terraform/ files change.
# The plan was already reviewed during the PR (terraform-plan.yml);
# this workflow executes the approved changes.
on:
  push:
    branches: [main]
    paths:
      - "terraform/**"
      - ".github/workflows/terraform-apply.yml"

env:
  TF_WORKING_DIR: terraform/environments/dev
  AWS_REGION: us-east-1
  TF_VAR_api_token: ${{ secrets.API_TOKEN }}
  TF_VAR_last_rotation: ${{ vars.LAST_ROTATION }}

permissions:
  contents: read
  id-token: write

jobs:
  apply:
    runs-on: ubuntu-latest
    concurrency:
      group: terraform-apply
      cancel-in-progress: false
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2

      - name: Setup Terraform
        uses: hashicorp/setup-terraform@5e8dbf3c6d9deaf4193ca7a8fb23f2ac83bb6c85 # v4.0.0
        with:
          terraform_version: "~> 1.10"

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@ec61189d14ec14c8efccab744f656cffd0e33f37 # v6.1.0
        with:
          role-to-assume: ${{ vars.AWS_OIDC_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}

      - name: Terraform Init
        working-directory: ${{ env.TF_WORKING_DIR }}
        run: terraform init

      - name: Terraform Apply
        working-directory: ${{ env.TF_WORKING_DIR }}
        run: terraform apply -auto-approve -no-color
```

- [ ] **Step 2: Verify YAML syntax**

```bash
uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/terraform-apply.yml')); print('Valid YAML')"
```

Expected: `Valid YAML`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/terraform-apply.yml
git commit -m "ci: add Terraform Apply workflow for auto-deploy on merge

Runs terraform apply -auto-approve on push to main when terraform/
files change. The plan was reviewed in the PR; this executes it.
Uses GitHub OIDC for AWS auth. Concurrency: sequential only
(cancel-in-progress: false) — never interrupts a running apply."
```

---

### Task 7: Operator prerequisites (manual — not automated)

This task documents the manual steps the operator must complete for the workflows to function. These cannot be automated by the implementing agent. The implementing agent should **skip this task** and note it as a manual prerequisite in the PR description.

**No files changed. Operator actions only.**

- [ ] **Step 1: Create the DynamoDB lock table**

```bash
MSYS_NO_PATHCONV=1 aws dynamodb create-table \
  --table-name pining-for-the-data-tflock \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1
```

Verify:
```bash
MSYS_NO_PATHCONV=1 aws dynamodb describe-table --table-name pining-for-the-data-tflock --region us-east-1 --query 'Table.TableStatus'
```

Expected: `"ACTIVE"`

- [ ] **Step 2: Reinitialize Terraform backend with state locking**

```bash
cd terraform/environments/dev
terraform init -reconfigure
```

Expected: `Terraform has been successfully initialized!` (with a message about the DynamoDB table being used for state locking).

- [ ] **Step 3: Add pining-for-the-data to the OIDC role trust policy**

In the AWS console (IAM → Roles → `<DevOpsAgent-role-name>` → Trust relationships → Edit):

Add `repo:karsten-s-nielsen/pining-for-the-data:*` to the `Condition.StringLike["token.actions.githubusercontent.com:sub"]` array.

- [ ] **Step 4: Set GitHub secrets and variables**

In the GitHub repo settings (Settings → Secrets and variables → Actions):

**Secrets:**
- `API_TOKEN` = `test-token-pining-for-the-data`

**Variables:**
- `AWS_OIDC_ROLE_ARN` = `arn:aws:iam::454762693631:role/<DevOpsAgent-role-name>` (same as lakehouse)
- `LAST_ROTATION` = `20260504T024554Z`

- [ ] **Step 5: Verify with a no-op PR**

Create a test PR that touches any file under `terraform/` (e.g., add a comment to `backend.tf`). Verify:
- `terraform-plan.yml` triggers and posts a plan comment
- `python-ci.yml` runs and passes (including the new secret scanning step)
- On merge, `terraform-apply.yml` triggers and applies successfully

---

## Operator Prerequisites Summary

Tasks 1-6 are code changes that can be committed and merged immediately. Task 7 is a manual operator checklist that must be completed for the Terraform workflows to function. The workflows will fail gracefully (OIDC auth error) until the operator completes the setup — they will not cause any damage if merged before the prerequisites are met.

**Recommended merge order:**
1. Merge Tasks 1-6 as a single PR (all code changes)
2. Operator completes Task 7 steps 1-4
3. Operator verifies with Task 7 step 5
