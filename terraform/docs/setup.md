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

The token is intentionally public and documented — the data is open.
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
  --home "Wakanda FC" \
  --away "Shire Town"
```

The upload CLI:
1. Uploads all files in the directory to `s3://{bucket}/skillcorner/game_03/`
2. Creates/updates `skillcorner/matches.json` with the game entry
3. Creates/updates `providers.json` with the provider

---

## Step 6: Test the API

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
4. Follow Steps 1-6 above, changing `project_name` in `terraform.tfvars` to your own

What you'll customize:

| Setting | Where | Default |
|---------|-------|---------|
| Project name | `terraform.tfvars` | `pining-for-the-data` |
| AWS region | `terraform.tfvars` | `us-east-1` |
| API token | `terraform.tfvars` | `test-token-pining-for-the-data` |
| Provider name | `pining-upload --provider` | `skillcorner` |

### Adding a New Provider

No infrastructure changes needed. Just upload files with a new provider prefix:

```bash
uv run pining-upload path/to/game/ \
  --provider my_provider \
  --game-id game_01 \
  --bucket your-bucket-name
```

The Lambda handlers discover providers and artifacts dynamically from S3.

### Adding New Artifact Types

Just upload files with the desired name. The `get_artifact` handler resolves
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
│   └── api/              # API Gateway REST routes
└── shared/               # Provider + version pins
```

### API Endpoints

| Method | Path | Handler | Response |
|--------|------|---------|----------|
| GET | `/v1/providers` | `list_providers` | JSON list of providers |
| GET | `/v1/{provider}/matches` | `list_matches` | JSON list of games + artifacts |
| GET | `/v1/{provider}/matches/{id}/{artifact}` | `get_artifact` | 302 redirect to presigned S3 URL |

All endpoints require `Authorization: Bearer <token>`.

---

## Teardown

To remove all AWS resources:

```bash
# Empty the data bucket first (Terraform can't delete non-empty buckets)
aws s3 rm s3://karstenskyt-pining-for-the-data --recursive

cd terraform/environments/dev
terraform destroy

# Optionally remove state infrastructure
cd terraform/modules/state
terraform destroy -var="project_name=pining-for-the-data" -var="aws_region=us-east-1"
```
