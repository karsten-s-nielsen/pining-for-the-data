# Mock Provider API Reference

REST API serving open and (operator-loaded) restricted soccer tracking data. Mimics commercial provider download protocols so ingestion adapters work against both mock and real endpoints.

**Base URL:** `https://{api-gateway-id}.execute-api.{region}.amazonaws.com/v1`

---

## Authentication

All endpoints require a bearer token in the `Authorization` header:

```
Authorization: Bearer <token>
```

The API recognises **two tiers** of bearer token:

| Tier | Source | Visibility | Use |
|------|--------|------------|-----|
| `PUBLIC` | `api_token` in `terraform.tfvars` (the documented default is `test-token-pining-for-the-data`) | Documented in README | Anyone — open-data consumers, CI, demos |
| `OWNER` | SSM Parameter Store SecureString `/pining-for-the-data/api_token_owner` | Never committed | Operator-owned consumer (e.g., the Lakehouse adapter); sees private-tier content |

`validate_token` returns one of `Tier.PUBLIC` / `Tier.OWNER` / `401 Invalid token`. Constant-time comparison via `hmac.compare_digest`. **If both tokens collide due to operator misconfiguration, the request classifies as `PUBLIC`** (fail closed; ADR 0001).

**Tier semantics — uniform 404 on mismatch.** Private-tier content accessed with the public token returns `404`, identical to "this resource doesn't exist". This prevents enumeration of private content via behaviour fingerprinting. The owner-token holder never hits this path.

**Rotation.** Update the SSM value via `aws ssm put-parameter --overwrite`, then bump `LAST_ROTATION` env var on all 6 Lambdas via `terraform apply -var=last_rotation=$(date -u +%Y%m%dT%H%M%SZ)`. No dual-validity in v1: consumers MUST implement 401 retry with backoff during the rotation window. See `docs/superpowers/specs/2026-05-02-private-data-tier.md` §3.5.

---

## Endpoints

### List Providers

```
GET /v1/providers
```

Returns all registered tracking data providers. **Tier-blind** — returns the same list to both PUBLIC and OWNER (the existence of a provider is not a secret; per-match `visibility` is the only enforcement boundary).

**Response** `200 OK`

```json
{
  "providers": ["skillcorner", "gradientsports"]
}
```

---

### List Matches

```
GET /v1/{provider}/matches
```

Returns games and their artifact maps for a provider, **filtered by tier**.

**Path parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `provider` | string | Provider name (must match `^[a-zA-Z0-9][a-zA-Z0-9_-]*$`) |

**Query parameters** (all optional, combined with AND logic)

| Parameter | Format | Semantics | Field filtered |
|-----------|--------|-----------|----------------|
| `updatedSince` | ISO 8601 UTC (e.g., `2026-05-01T00:00:00Z`) | Exclusive: `updated_at > value` | `updated_at` |
| `dateFrom` | `YYYY-MM-DD` | Inclusive: `date >= value` | `date` |
| `dateTo` | `YYYY-MM-DD` | Exclusive: `date < value` | `date` |

Records where the filtered field is `null` or missing are excluded when that filter is active. Unrecognised query parameters are silently ignored.

**Incremental polling pattern:**

```bash
# First poll — get everything
curl -H "Authorization: Bearer $TOKEN" "$API_URL/v1/skillcorner/matches"

# Subsequent polls — only records updated after last poll
curl -H "Authorization: Bearer $TOKEN" \
  "$API_URL/v1/skillcorner/matches?updatedSince=2026-05-01T00:00:00Z"

# Date range
curl -H "Authorization: Bearer $TOKEN" \
  "$API_URL/v1/skillcorner/matches?dateFrom=2022-11-20&dateTo=2022-12-19"
```

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
      "artifacts": {
        "match": "match.json",
        "tracking": "tracking.jsonl"
      },
      "visibility": "public",
      "updated_at": "2026-05-03T16:42:00Z"
    }
  ]
}
```

`artifacts` is an object mapping artifact name → exact filename. The keys form the API's allowlist: `get_artifact` rejects any name not present here. `visibility` is `"public"` or `"private"` (missing = `"public"` for backwards compatibility). `updated_at` is set by the upload tooling on every write, including no-op re-uploads — consumers can poll it for incremental refresh.

**Tier behaviour**

- PUBLIC tier: receives only entries with `visibility == "public"` (or missing). An empty result on a known provider returns `200 + {"matches": []}`, not 404 — no existence leak.
- OWNER tier: receives all entries.

**Error responses**

| Status | Body | Cause |
|--------|------|-------|
| `400` | `{"error": "Invalid provider: ..."}` | Path param fails the safe-character allowlist |
| `400` | `{"error": "Invalid updatedSince: ..."}` | `updatedSince` not parseable as ISO 8601 |
| `400` | `{"error": "Invalid dateFrom: ..."}` | `dateFrom` not parseable as YYYY-MM-DD |
| `400` | `{"error": "Invalid dateTo: ..."}` | `dateTo` not parseable as YYYY-MM-DD |
| `401` | `{"error": "Missing or malformed Authorization header"}` | No `Authorization: Bearer <token>` header present |
| `401` | `{"error": "Invalid token"}` | Token does not match either configured value |
| `404` | `{"error": "Provider not found"}` | No `matches.json` exists for this provider |

---

### Get Artifact

```
GET /v1/{provider}/matches/{id}/{artifact}
```

Redirects to a time-limited presigned S3 URL for the requested artifact. The handler reads `matches.json` once, looks up the artifact's filename via `artifacts[name]`, and generates the presigned URL directly (no per-request `list_objects_v2`).

**Path parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `provider` | string | Provider name |
| `id` | string | Game identifier (must match the safe-character allowlist) |
| `artifact` | string | Artifact name — MUST be a key in the match entry's `artifacts` object |

**Response** `302 Found`

The `Location` header contains a presigned S3 URL (valid for 1 hour by default). Follow the redirect to download the file.

**Error responses**

| Status | Body | Cause |
|--------|------|-------|
| `400` | `{"error": "Invalid {provider/id/artifact}: ..."}` | A path param fails the safe-character allowlist |
| `401` | `{"error": "Missing or malformed Authorization header"}` | No bearer header present |
| `401` | `{"error": "Invalid token"}` | Token does not match either configured value |
| `404` | `{"error": "Match not found"}` | Match doesn't exist OR is private and the caller is PUBLIC tier |
| `404` | `{"error": "Artifact not found"}` | The artifact name is not a key in the entry's `artifacts` allowlist |

---

### List Players

```
GET /v1/{provider}/players
```

Returns the player reference catalogue for a provider, **filtered by tier**. Provider must be registered in `providers.json` (unknown-provider returns `404`, same as `list_matches`).

**Path parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `provider` | string | Provider name |

**Query parameters** (optional)

| Parameter | Format | Semantics | Field filtered |
|-----------|--------|-----------|----------------|
| `updatedSince` | ISO 8601 UTC (e.g., `2026-05-01T00:00:00Z`) | Exclusive: `updated_at > value` | `updated_at` |

`dateFrom` and `dateTo` are **not supported** on this endpoint (players have no `date` field). Passing either returns `400`.

**Reserved query parameters** (ignored in v1, reserved for future pagination): `?limit=`, `?offset=`, `?cursor=`, `?team_id=`, `?competition_id=`. v1 catalogues fit the Lambda 6 MB sync response cap.

**Response** `200 OK`

```json
{
  "provider": "gradientsports",
  "players": [
    {
      "id": "example-007",
      "firstName": "Example",
      "lastName": "Player",
      "nickname": "Example Player",
      "dob": "2000-01-01",
      "height": 180.0,
      "positionGroupType": "D",
      "visibility": "private",
      "updated_at": "2026-05-03T16:42:00Z",
      "source": {
        "name": "Gradient Sports",
        "url": "https://www.gradientsports.com/",
        "licence": "Restricted; redistribution not permitted pending licence clarification"
      }
    }
  ]
}
```

The canonical `PlayerRecord` shape: required `id` + (`nickname` OR `firstName`+`lastName`); optional `dob`, `height`, `position`, `positionGroupType`, `nationality`, `source`. Provider-specific extension fields (anything beyond the canonical set) are round-tripped verbatim. JSON Schema published at `schemas/players.schema.json` with stable URN `urn:pining-for-the-data:schema:players:v1`.

**Tier behaviour**

- PUBLIC tier: receives only entries from `{provider}/players.json`.
- OWNER tier: receives the union of `{provider}/players.json` and `{provider}/_private/players.json`. On cross-tier ID collision, the **private record wins** (private is the more specific real record; spec §6.3.1).

**Error responses**

| Status | Body | Cause |
|--------|------|-------|
| `400` | `{"error": "Filter 'dateFrom' is not supported on this endpoint"}` | `dateFrom` or `dateTo` passed to players endpoint |
| `400` | `{"error": "Invalid updatedSince: ..."}` | `updatedSince` not parseable as ISO 8601 |
| `401` | `{"error": "..."}` | Auth failure (same as other endpoints) |
| `404` | `{"error": "Provider not found"}` | Provider not registered in `providers.json` |

---

### Get Player

```
GET /v1/{provider}/players/{id}
```

Returns a single player reference record. Same provider-gating + private-precedence semantics as `list_players`.

**Path parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `provider` | string | Provider name |
| `id` | string | Player identifier (must match the safe-character allowlist) |

**Response** `200 OK` — the player record (same shape as a single entry from `list_players`).

**Error responses**

| Status | Body | Cause |
|--------|------|-------|
| `401` | `{"error": "..."}` | Auth failure |
| `404` | `{"error": "Provider not found"}` | Provider not registered |
| `404` | `{"error": "Player not found"}` | Player ID not in either index OR found in private-only and caller is PUBLIC tier |

---

### Health Check

```
GET /v1/health
```

Unauthenticated health check for synthetic monitoring and uptime probes. Returns `200 OK` with `{"status": "ok"}` when the Lambda is responsive. No bearer token required.

**Response** `200 OK`

```json
{"status": "ok"}
```

---

## S3 Data Layout

```
{bucket}/
├── providers.json              # ["skillcorner", "gradientsports"]
├── skillcorner/                # public-tier provider
│   ├── matches.json            # discovery index (object-form artifacts dict)
│   ├── players.json            # (optional) public-tier player catalogue
│   ├── game_03/
│   │   ├── match.json
│   │   └── tracking.jsonl
│   └── game_04/
│       └── ...
└── gradientsports/            # restricted-tier provider
    ├── matches.json            # all entries marked visibility:"private"
    └── _private/               # reserved segment — owner-tier only
        ├── players.json        # private-tier player catalogue
        └── example-001/
            ├── metadata.json
            ├── events.json
            ├── roster.json
            └── tracking.jsonl.bz2
```

- SSE-KMS encryption at rest (single CMK across data + audit buckets)
- Versioning enabled on both buckets
- No public access — all serving via Lambda presigned URLs
- The `_private/` path segment is reserved; path-param validators reject `_`-prefixed values (defense in depth alongside the application-layer tier check). See ADR 0002.

---

## Audit Logging

CloudTrail data events on the data bucket land in a separate audit bucket (`{project}-audit-{account_id}`) with a 365-day retention lifecycle. The trail captures all S3 GET/PUT/DELETE on the data bucket, including the GETs that follow presigned URLs (attributed to the requester's source IP and IAM principal). Only `/providers.json` reads are excluded from the trail (true bookkeeping; never reveals private content). `/matches.json` and `/players.json` reads stay logged because enumeration via those indexes is the most likely abuse vector. See ADR 0004.

---

## Rate Limits

API Gateway throttling: 10 requests/second sustained, 50-request burst (per stage). For the dataset's expected volume, throttling will not apply.

---

## Adding Providers, Matches, and Players

New providers and artifact types require no infrastructure changes.

**Match upload** (game artifacts + matches.json + providers.json):

```bash
uv run pining-upload path/to/game/ \
  --provider new_provider \
  --game-id game_01 \
  --bucket your-bucket-name \
  --visibility public           # or --visibility private
```

**Player catalogue upload** (canonical JSON only — provider-specific shapes must be normalised first; see `scripts/upload_gradient_wc2022.py` for a worked example):

```bash
uv run pining-upload-players players.json \
  --provider new_provider \
  --bucket your-bucket-name \
  --visibility public           # or --visibility private
```

Both CLIs validate against the canonical Pydantic models (`MatchEntry`, `PlayerRecord`) before any S3 call; validation errors abort the upload with field-level diagnostics. Cross-tier mixing is rejected (re-uploading a public ID with `--visibility private` for the same `id` fails; cross-file ID collisions between public and private indexes are likewise rejected).

---

## Schema Files

Canonical JSON Schemas for the index entry shapes are published in `schemas/` and drift-tested in CI:

- `schemas/matches.schema.json` (URN `urn:pining-for-the-data:schema:matches:v1`)
- `schemas/players.schema.json` (URN `urn:pining-for-the-data:schema:players:v1`)

Generated from the Pydantic models in `src/canonical/models.py` via `python scripts/regenerate_schemas.py`. Both schemas embed `$id` (URN) and `$schema` (Draft 2020-12) metadata so consumers can pin to schema identity. The contract is **additive only**: fields are added; never removed or renamed (see spec §9). The models live outside `terraform/modules/functions/src/` so the Lambda zip remains dependency-free (no pydantic at runtime; handlers consume already-validated dict payloads from S3).
