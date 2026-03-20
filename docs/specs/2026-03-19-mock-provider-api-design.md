# Mock Provider API — Design Spec

**Date:** 2026-03-19
**Status:** Approved
**Scope:** Terraform infrastructure + Lambda handlers + upload CLI for the Level 2 mock provider API

---

## 1. Purpose

AWS-hosted REST API that mimics commercial tracking data provider download protocols. Consumers (like luxury-lakehouse's ingestion adapters) hit the mock API with the same code they'd use against the real provider — same bearer token auth, same endpoint shape, same response format.

Each provider gets its own path namespace with provider-faithful endpoint structure. Adding new providers or artifact types requires no Lambda or Terraform changes.

## 2. API Design

### 2.1 Endpoints

```
GET /v1/providers                              → list supported providers
GET /v1/{provider}/matches                     → list games + available artifacts
GET /v1/{provider}/matches/{id}/{artifact}     → serve artifact (presigned S3 URL)
```

`{provider}`: `metrica`, `respovision` (extensible)
`{artifact}`: provider-specific, e.g., `tracking`, `metadata`, `events`, `roster`, `summary`

All endpoints require `Authorization: Bearer <token>`.

### 2.2 Metrica Artifacts (initial)

| Artifact | File | Content |
|----------|------|---------|
| `tracking` | `tracking.txt` | EPTS raw tracking data (colon-delimited, 0-1 normalized) |
| `metadata` | `metadata.xml` | FIFA EPTS metadata XML (players, teams, half boundaries) |
| `events` | `events.xml` | Tactical patterns / event annotations |
| `roster` | `roster.json` | De-identified roster (pining-for-the-data generated, not provider artifact) |

### 2.3 Respo.Vision Artifacts (future, expected)

| Artifact | File | Content |
|----------|------|---------|
| `tracking` | `tracking.json` | 3D pose data (50+ keypoints per player per frame) |
| `summary` | `summary.json` | Match metadata |
| `roster` | `roster.json` | De-identified roster |

Actual artifact types TBD when sample data arrives from sales engagement.

### 2.4 Discovery Response

```json
// GET /v1/providers
{
  "providers": ["metrica", "respovision"]
}

// GET /v1/metrica/matches
{
  "provider": "metrica",
  "matches": [
    {
      "id": "game_03",
      "date": "2026-03-15",
      "home": "Wakanda FC",
      "away": "Shire Town",
      "artifacts": ["tracking", "metadata", "events", "roster"]
    }
  ]
}
```

### 2.5 Authentication

Static bearer token: `test-token-pining-for-the-data` (documented in README, configurable via Terraform variable). Public token — the data is open. Auth exists to exercise the same code path as real providers.

Clear upgrade path to JWT token vending (Option B) if needed later:
1. Add `/v1/auth/token` endpoint
2. Swap string comparison to JWT verification in handlers
3. Accept both during transition

## 3. S3 Layout

```
pining-for-the-data-{account_id}/
├── providers.json                    # ["metrica", "respovision"]
├── metrica/
│   ├── matches.json                  # discovery index
│   ├── game_03/
│   │   ├── tracking.txt
│   │   ├── metadata.xml
│   │   ├── events.xml
│   │   └── roster.json
│   └── game_04/
│       └── ...
└── respovision/
    ├── matches.json
    └── game_03/
        ├── tracking.json
        ├── summary.json
        └── roster.json
```

- SSE-KMS encryption at rest
- Versioning enabled
- No public access (all serving via Lambda presigned URLs)
- Bucket name: `pining-for-the-data-{account_id}` (avoids global naming collisions)

## 4. Lambda Handlers

### 4.1 Shared Auth

All handlers validate `Authorization: Bearer <token>` against the configured static token. Return 401 on mismatch.

### 4.2 Handlers

| Handler | Input | Logic | Response |
|---------|-------|-------|----------|
| `list_providers` | — | Read `providers.json` from S3 | 200 + JSON |
| `list_matches` | `{provider}` | Read `{provider}/matches.json` from S3 | 200 + JSON |
| `get_artifact` | `{provider}`, `{id}`, `{artifact}` | Generate presigned URL for `{provider}/{id}/{artifact}.*` | 302 redirect |

Three handlers total (not four — `get_tracking` and `get_roster` collapse into generic `get_artifact`).

- Runtime: Python 3.12
- Dependencies: boto3 only (Lambda built-in)
- Memory: 128 MB (minimal — just S3 reads and presigned URL generation)
- Timeout: 10 seconds

### 4.3 Artifact Resolution

`get_artifact` scans the S3 prefix `{provider}/{id}/` for files matching `{artifact}.*` (e.g., `tracking.txt`, `tracking.json`, `tracking.csv`). Returns the first match. If no match, returns 404 with `{"error": "Artifact not found", "provider": "...", "match_id": "...", "artifact": "..."}`.

This means adding a new artifact type to a game is just uploading a file with the right name — no config changes.

## 5. Terraform Structure

```
terraform/
├── environments/
│   └── dev/
│       ├── main.tf                   # module composition
│       ├── variables.tf              # region, project name, token
│       ├── outputs.tf                # API URL, bucket name, state bucket
│       ├── backend.tf                # S3 remote state
│       └── terraform.tfvars.example  # copy to terraform.tfvars, fill in
├── modules/
│   ├── state/                        # BOOTSTRAP: state bucket + DynamoDB lock
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── storage/                      # data bucket, KMS key, versioning, policy
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── api/                          # API Gateway REST, routes, deployment, stage
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   └── functions/                    # Lambda functions, IAM roles, permissions
│       ├── main.tf
│       ├── variables.tf
│       ├── outputs.tf
│       └── src/
│           ├── list_providers.py
│           ├── list_matches.py
│           └── get_artifact.py
├── shared/
│   └── versions.tf                   # AWS provider + Terraform version pins
└── docs/
    └── setup.md                      # step-by-step bootstrap guide
```

### 5.1 Configuration

```hcl
# terraform.tfvars.example
aws_region   = "us-east-1"           # default, match luxury-lakehouse
project_name = "pining-for-the-data"
api_token    = "test-token-pining-for-the-data"
```

All values configurable for forkers. Region defaults to us-east-1 but overridable.

### 5.2 State Backend

S3 remote backend with DynamoDB locking. Bootstrap module creates the state infrastructure first. Documented step-by-step in `terraform/docs/setup.md`.

## 6. CLI Entry Point

New command: `pining-upload`

```bash
# Upload all Metrica artifacts for a game
pining-upload game_03/ --provider metrica --game-id game_03

# Upload Respo.Vision artifacts (future)
pining-upload game_03/ --provider respovision --game-id game_03
```

Behavior:
1. Upload all files in the directory to `s3://{bucket}/{provider}/{game_id}/`
2. Read existing `{provider}/matches.json`
3. Add/update entry for this game (auto-detect artifacts from uploaded filenames)
4. Write updated `matches.json` back to S3
5. Update `providers.json` if this is a new provider

## 7. Bootstrap Guide (terraform/docs/setup.md)

### For the repo owner (first-time setup)

1. Configure AWS CLI profile
2. `cd terraform/modules/state && terraform init && terraform apply`
3. Copy state bucket name + DynamoDB table into `environments/dev/backend.tf`
4. `cd terraform/environments/dev`
5. `cp terraform.tfvars.example terraform.tfvars` and fill in values
6. `terraform init && terraform apply`
7. Note the API Gateway URL from outputs
8. Upload data: `pining-upload output/game_03/ --provider metrica --game-id game_03`
9. Test: `curl -H "Authorization: Bearer test-token-pining-for-the-data" https://{api-url}/v1/providers`

### For forkers

1. Fork the repo
2. Create an AWS account (free tier covers everything)
3. Install Terraform and AWS CLI
4. Follow the same steps above — all values are configurable in `terraform.tfvars`
5. Estimated cost: ~$0.02/month

## 8. Future Extensions

- **JWT token vending** — add `/v1/auth/token`, swap auth check, no endpoint changes
- **Custom domain** — add ACM cert + Route 53 + API Gateway domain mapping module
- **Webhook/polling delivery** — per-provider prefix isolation allows different delivery mechanisms without touching other providers
- **Rate limiting** — API Gateway usage plans + API keys (built-in, no Lambda changes)
- **Respo.Vision artifacts** — just upload files with the right names, update `matches.json`

## 9. Cost Estimate

| Resource | Free Tier | Expected Monthly |
|----------|-----------|-----------------|
| S3 storage | 5 GB free | ~$0.02 (20 games, ~200 MB) |
| S3 requests | 2,000 PUT + 20,000 GET free | $0.00 |
| API Gateway | 1M requests free | $0.00 |
| Lambda | 1M invocations free | $0.00 |
| KMS | 20,000 requests free | $0.00 |
| DynamoDB (state lock) | 25 GB + 25 WCU/RCU free | $0.00 |
| **Total** | | **~$0.02/month** |
