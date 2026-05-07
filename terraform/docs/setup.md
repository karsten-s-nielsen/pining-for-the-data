# Mock Provider API — Setup Guide

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

## Step 0: API Gateway CloudWatch Role (One-Time)

API Gateway HTTP API access logging requires an account-level CloudWatch Logs role.
This is a one-time setup per AWS account — if it's already configured, skip to Step 1.

1. **Create the IAM role:**
   - IAM → Roles → Create role
   - Trusted entity type: **Custom trust policy**
   - Paste:
     ```json
     {
         "Version": "2012-10-17",
         "Statement": [{
             "Effect": "Allow",
             "Principal": { "Service": "apigateway.amazonaws.com" },
             "Action": "sts:AssumeRole"
         }]
     }
     ```
   - Attach policy: `AmazonAPIGatewayPushToCloudWatchLogs`
   - Role name: `api-gateway-cloudwatch-logs`

2. **Configure API Gateway:**
   - API Gateway → Settings (left sidebar, bottom)
   - Set **CloudWatch log role ARN** to:
     ```
     arn:aws:iam::<ACCOUNT_ID>:role/api-gateway-cloudwatch-logs
     ```
   - Save

Without this, `terraform apply` will fail with "Insufficient permissions to enable logging"
on the API Gateway stage.

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

The public token is intentionally documented — the open data is freely available.
Auth exists to exercise the same code path as real providers.

The API also supports a **private (owner) tier** for operator-loaded restricted content.
Owner-tier setup is covered in Step 6 below.

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
  --home "Wakanda FC" \
  --away "Shire Town"
```

The upload CLI:
1. Uploads all files in the directory to `s3://{bucket}/skillcorner/game_03/`
2. Creates/updates `skillcorner/matches.json` with the game entry
3. Creates/updates `providers.json` with the provider

---

## Step 5b: Upload Player Catalogue (Optional)

Upload a player reference catalogue for a provider:

```bash
uv run pining-upload-players players.json \
  --provider skillcorner \
  --bucket "karstenskyt-pining-for-the-data" \
  --visibility public
```

For private-tier player data, use `--visibility private` along with `--source-name`, `--source-url`, and `--source-licence` flags.

---

## Step 6: Set Up Owner-Tier Auth (Optional)

The API supports two authentication tiers. The public token (Step 3) serves open data.
An optional **owner token** grants access to private-tier content uploaded with `--visibility private`.

Create the owner token in SSM Parameter Store:

```bash
aws ssm put-parameter \
  --name "/pining-for-the-data/api_token_owner" \
  --type SecureString \
  --value "your-secret-owner-token" \
  --overwrite
```

Then invalidate Lambda caches so the new token takes effect:

```bash
terraform apply -var=last_rotation=$(date -u +%Y%m%dT%H%M%SZ)
```

See [`docs/api-reference.md`](../../docs/api-reference.md) for the full two-tier auth contract.

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
  "$API/skillcorner/matches/game_03/tracking" -o tracking.txt
ls -la tracking.txt

# Verify auth rejection
curl -s "$API/providers"  # should return 401
```

---

## For Forkers

Everything is configurable — no hardcoded values. To deploy your own instance:

1. Fork this repo
2. Create an AWS account ([Free Tier](https://aws.amazon.com/free/) covers everything)
3. Install Terraform and AWS CLI
4. Follow Steps 1-7 above, changing `project_name` in `terraform.tfvars` to your own

What you'll customize:

| Setting | Where | Default |
|---------|-------|---------|
| Project name | `terraform.tfvars` | `pining-for-the-data` |
| AWS region | `terraform.tfvars` | `us-east-1` |
| API token | `terraform.tfvars` | `test-token-pining-for-the-data` |
| Provider name | `pining-upload --provider` | `skillcorner` |

### CI/CD: GitHub Actions OIDC Role

The Terraform Plan/Apply workflows use OIDC to assume an IAM role. The role needs:

- **Trust policy:** Allow `sts:AssumeRoleWithWebIdentity` from `token.actions.githubusercontent.com` with subject `repo:<org>/<repo>:*`
- **Permissions policy:** S3 (state bucket), KMS, IAM, Lambda, API Gateway, CloudWatch (logs, alarms, dashboards), SNS, SSM, CloudTrail, and `logs:PutResourcePolicy` / `logs:DescribeResourcePolicies` / `logs:DescribeLogGroups` for API Gateway access logging

Set the role ARN as a GitHub repository variable: `AWS_OIDC_ROLE_ARN`.

### Adding a New Provider

No infrastructure changes needed. Upload files with a new provider prefix:

```bash
uv run pining-upload path/to/game/ \
  --provider my_provider \
  --game-id game_01 \
  --bucket your-bucket-name
```

The Lambda handlers discover providers and artifacts dynamically from S3.

### Adding New Artifact Types

Upload files with the desired name. The `get_artifact` handler resolves
`{artifact}` to any file matching `{provider}/{game_id}/{artifact}.*` in S3.

For example, uploading `events.json` makes it available at
`GET /v1/{provider}/matches/{game_id}/events`.

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
│   ├── api/              # API Gateway REST routes
│   ├── audit/            # CloudTrail data events on data bucket
│   └── observability/    # CloudWatch alarms, SNS topic, dashboard
└── shared/               # Provider + version pins
```

### API Endpoints

| Method | Path | Handler | Response |
|--------|------|---------|----------|
| GET | `/v1/providers` | `list_providers` | JSON list of providers |
| GET | `/v1/{provider}/matches` | `list_matches` | JSON list of games + artifacts |
| GET | `/v1/{provider}/matches/{id}/{artifact}` | `get_artifact` | 302 redirect to presigned S3 URL |
| GET | `/v1/{provider}/players` | `list_players` | JSON list of player reference records |
| GET | `/v1/{provider}/players/{id}` | `get_player` | Single player reference record |
| GET | `/v1/health` | `health` | Health check (unauthenticated) |

All endpoints except `/v1/health` require `Authorization: Bearer <token>`.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `terraform init` fails with "Failed to get existing workspaces" | Backend S3 bucket doesn't exist yet | Run Step 1 (Bootstrap) first — the state module creates the bucket |
| `terraform apply` fails with "Access Denied" | IAM permissions insufficient | Your AWS credentials need `s3:*`, `apigateway:*`, `lambda:*`, `iam:*`, `kms:*`, `dynamodb:*`, `logs:*` permissions. Use an admin role for initial setup |
| `pining-upload` fails with "NoSuchBucket" | Bucket name mismatch | Check `terraform output bucket_name` and pass the exact value to `--bucket` |
| `curl` returns `{"error":"Invalid token"}` or `{"error":"Missing or malformed Authorization header"}` | Wrong or missing token | Verify `Authorization: Bearer <token>` header matches `api_token` in `terraform.tfvars` |
| `curl` returns `{"message":"Forbidden"}` | API Gateway stage mismatch | Ensure the URL ends with `/v1/...` — the stage name is part of the path |
| `curl` returns `{"error":"Artifact not found"}` | File not uploaded or wrong artifact name | Check S3: `aws s3 ls s3://{bucket}/{provider}/{game_id}/` — artifact name must match the filename prefix (e.g., `tracking` matches `tracking.jsonl`) |
| `terraform destroy` fails with "BucketNotEmpty" | S3 bucket still has objects | Empty it first: `aws s3 rm s3://{bucket} --recursive` |

---

## Teardown

> **Warning:** This permanently deletes all uploaded data, audit logs, and infrastructure.
> There is no undo. Make sure you have backups of any data you want to keep.

To remove all AWS resources:

```bash
# Empty both buckets first (Terraform can't delete non-empty buckets)
aws s3 rm s3://karstenskyt-pining-for-the-data --recursive
aws s3 rm s3://karstenskyt-pining-for-the-data-audit --recursive

cd terraform/environments/dev
terraform destroy

# Optionally remove state infrastructure
cd terraform/modules/state
terraform destroy -var="project_name=pining-for-the-data" -var="aws_region=us-east-1"
```
