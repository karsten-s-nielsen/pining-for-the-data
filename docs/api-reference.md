# Mock Provider API Reference

REST API serving open soccer tracking data. Mimics commercial provider download protocols so ingestion adapters work against both mock and real endpoints.

**Base URL:** `https://{api-gateway-id}.execute-api.{region}.amazonaws.com/v1`

---

## Authentication

All endpoints require a bearer token in the `Authorization` header:

```
Authorization: Bearer <token>
```

The default token is `test-token-pining-for-the-data` (configurable via `api_token` in `terraform.tfvars`). Requests without a valid token receive a `401` response.

---

## Endpoints

### List Providers

```
GET /v1/providers
```

Returns all registered tracking data providers.

**Response** `200 OK`

```json
{
  "providers": ["skillcorner"]
}
```

Providers are discovered dynamically from S3 — each directory with a `matches.json` file is a provider.

---

### List Matches

```
GET /v1/{provider}/matches
```

Returns all available games and their artifacts for a provider.

**Path parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `provider` | string | Provider name (e.g., `skillcorner`) |

**Response** `200 OK`

```json
{
  "provider": "skillcorner",
  "matches": [
    {
      "id": "game_03",
      "date": "2026-01-03",
      "home": "Auckland FC",
      "away": "Wellington Phoenix FC",
      "artifacts": ["match", "tracking"]
    }
  ]
}
```

**Error responses**

| Status | Body | Cause |
|--------|------|-------|
| `401` | `{"error": "Missing or malformed Authorization header"}` | No `Authorization: Bearer <token>` header present |
| `401` | `{"error": "Invalid token"}` | Token does not match the configured value |
| `404` | `{"error": "Provider not found"}` | No `matches.json` exists for this provider |

---

### Get Artifact

```
GET /v1/{provider}/matches/{id}/{artifact}
```

Redirects to a time-limited presigned S3 URL for the requested artifact.

**Path parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `provider` | string | Provider name (e.g., `skillcorner`) |
| `id` | string | Game identifier (e.g., `game_03`) |
| `artifact` | string | Artifact name without extension (e.g., `match`, `tracking`) |

**Response** `302 Found`

The `Location` header contains a presigned S3 URL (valid for 1 hour by default). Follow the redirect to download the file.

Artifact resolution: the handler scans `{provider}/{id}/` in S3 for files whose name (without extension) matches `{artifact}`. For example, requesting `tracking` matches `tracking.jsonl`.

**Error responses**

| Status | Body | Cause |
|--------|------|-------|
| `401` | `{"error": "Missing or malformed Authorization header"}` | No `Authorization: Bearer <token>` header present |
| `401` | `{"error": "Invalid token"}` | Token does not match the configured value |
| `404` | `{"error": "Artifact not found"}` | No file matching `{artifact}.*` in the game directory |

---

## S3 Data Layout

```
{bucket}/
├── providers.json                    # ["skillcorner"]
├── skillcorner/
│   ├── matches.json                  # discovery index (all games + artifacts)
│   ├── game_03/
│   │   ├── match.json                # match metadata
│   │   └── tracking.jsonl            # tracking data (10fps)
│   └── game_04/
│       └── ...
└── {future-provider}/
    ├── matches.json
    └── ...
```

- SSE-KMS encryption at rest
- Versioning enabled
- No public access — all serving via Lambda presigned URLs

---

## Rate Limits

No rate limiting configured. The API runs on AWS API Gateway + Lambda with default throttling (10,000 requests/second burst, 5,000 sustained). For this dataset's expected volume, throttling will not apply.

---

## Adding Providers and Artifacts

New providers and artifact types require no infrastructure changes. Upload files to S3 with the correct path structure and update the index files:

```bash
uv run pining-upload path/to/game/ \
  --provider new_provider \
  --game-id game_01 \
  --bucket your-bucket-name
```

The upload CLI creates and updates `providers.json` and `{provider}/matches.json` automatically.
