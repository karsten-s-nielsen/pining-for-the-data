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
  "providers": ["gradientsports", "idsse", "skillcorner", "statsbomb"]
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
      "updated_at": "2026-05-03T16:42:00Z",
      "competition_id": "1001",
      "competition_name": "Example League",
      "season_id": "2001",
      "season_name": "2025/2026",
      "artifact_meta": {
        "match": {
          "sha256": "2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae",
          "size": 2048
        },
        "tracking": {
          "sha256": "fcde2b2edba56bf408601fb721fe9b5c338d10ee429ea04fae5511b68fbf8fb9",
          "size": 1048576
        }
      }
    }
  ]
}
```

**Entry fields**

| Field | Type | Presence | Meaning |
|-------|------|----------|---------|
| `id` | string | always | Match id; matches `^[a-zA-Z0-9][a-zA-Z0-9_-]*$` (max 128) |
| `artifacts` | object | always | Artifact name → exact filename. The keys form the API's allowlist for Get Artifact |
| `visibility` | `"public"` \| `"private"` | always (missing = `"public"`, legacy) | Tier of the entry |
| `updated_at` | ISO 8601 UTC string | always | Changes only when the entry changes (see below) |
| `date` | `YYYY-MM-DD` | optional | Match date |
| `home`, `away` | string | optional | Team names |
| `provenance` | string | optional | `"redistributed"`, `"deidentified"` or `"original"` |
| `source` | object | optional | `{name, url, licence}` of the upstream source |
| `format_version` | integer | optional | `2` = the canonical Parquet/zstd set (ADR 0011); absent = legacy layout |
| `competition_id` | string | optional | The provider's competition id, verbatim |
| `competition_name` | string | optional | The provider's competition name, verbatim |
| `season_id` | string | optional | The provider's season id, verbatim |
| `season_name` | string | optional | The provider's season value, verbatim |
| `artifact_meta` | object | every entry (optional in the schema) | Artifact name → `{"sha256": <lowercase hex>, "size": <bytes>}` of the object bytes as stored and served. Its keys equal the `artifacts` keys |

`visibility` is `"public"` or `"private"` (missing = `"public"` for backwards compatibility). Unset optional fields are absent, not null.

**Competition and season fields.** Each value comes from the match's own metadata artifact, in the role the source gives it. A value the source does not give is absent: it is never inferred, and a name is never copied into an id or an id into a name (ADR 0010, ADR 0013). An integer source id is served as its decimal string. season_id absent means the source gives no season id; season_name is the source value verbatim.

| Provider | Metadata artifact | `competition_id` / `competition_name` | `season_id` / `season_name` |
|----------|-------------------|---------------------------------------|-----------------------------|
| `gradientsports` | `metadata` | `competition.id` / `competition.name` | absent / `season` |
| `idsse` | `metadata` | `General@CompetitionId` / `@CompetitionName` | `@SeasonId` / `@Season` |
| `skillcorner` (both tiers) | `metadata` (owner tier), `{id}_match` (public tier) | `competition_edition.competition.id` / `.name` | `competition_edition.season.id` / `.name` |
| `statsbomb` | `metadata` | `competition.competition_id` / `competition_name` | `season.season_id` / `season_name` |

`updated_at` changes only when the entry changes: any entry field, or the `sha256` of any artifact. A byte-identical re-upload does not change it. Clients can poll it with `updatedSince` for incremental refresh. Clients should compare each artifact's `artifact_meta.sha256` with their stored copy before they download it.

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

**Unset optional fields are absent, not null.** `pining-upload-players` serialises records with `model_dump(exclude_none=True)`, so an optional field a provider never populated is omitted from the served JSON rather than emitted as `null`. Consumers must test for **absence** (`"firstName" in record`) rather than for a null value — `record["firstName"] is None` raises `KeyError`. This is why some providers' records carry only `nickname` and no `firstName`/`lastName` split: those fields are never populated at source, so they never appear in the response at all.

`updated_at` changes only when the record changes: any record field. A byte-identical re-upload does not change it. Clients can poll it with `updatedSince` for incremental refresh.

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
├── providers.json              # ["gradientsports", "idsse", "skillcorner", "statsbomb"]
├── skillcorner/                # public-tier provider
│   ├── matches.json            # discovery index (object-form artifacts dict)
│   ├── players.json            # (optional) public-tier player catalogue
│   ├── game_03/
│   │   ├── match.json
│   │   └── tracking.jsonl
│   └── game_04/
│       └── ...
├── idsse/                      # public-tier provider (no _private/ subtree)
│   ├── matches.json            # all entries marked visibility:"public"
│   └── example-003/
│       ├── metadata.xml        # DFL matchinformation
│       ├── events.xml          # DFL events_raw
│       └── tracking.xml        # DFL positions_raw_observed
├── gradientsports/             # restricted-tier provider
│   ├── matches.json            # all entries marked visibility:"private"
│   └── _private/               # reserved segment — owner-tier only
│       ├── players.json        # private-tier player catalogue
│       └── example-001/
│           ├── metadata.json
│           ├── events.json
│           ├── roster.json
│           └── tracking.jsonl.bz2
└── statsbomb/                  # restricted-tier provider (owner tier only)
    ├── matches.json            # all entries marked visibility:"private"
    └── _private/               # reserved segment — owner-tier only
        ├── players.json        # private-tier player catalogue
        └── example-002/
            ├── metadata.json           # single StatsBomb match object
            ├── events.json.gz
            ├── freeze_frames.json.gz
            └── roster.json
```

- SSE-KMS encryption at rest (single CMK across data + audit buckets)
- Versioning enabled on both buckets
- No public access — all serving via Lambda presigned URLs
- The `_private/` path segment is reserved; path-param validators reject `_`-prefixed values (defense in depth alongside the application-layer tier check). See ADR 0002.
- `statsbomb` is **owner-tier only** — every entry is `visibility: "private"` and the provider has no public tier. It carries **two source families** under one slug, distinguished by each entry's `provenance`: the commercial 360 club delivery (`provenance: "original"`, ADR 0010) and the open-data tournaments corpus (`provenance: "redistributed"`, ADR 0012 — StatsBomb's public open-data 360 for six complete tournaments, freely accessible but not freely redistributable, so re-hosted single-user at the owner tier). A consumer that wants only one kind filters on `provenance`; each entry's `source.licence` records which agreement governs it. Both families share the same artifact keys `events`, `freeze_frames`, `roster` and `metadata`; there is deliberately **no `tracking` artifact**, because StatsBomb 360 supplies event-moment freeze frames rather than continuous tracking (ADR 0008 permits a provider to expose a subset of the role vocabulary). The `metadata` artifact is a **single JSON object in StatsBomb feed shape** (nested `competition{}`, `season{}`, `home_team{}`, `stadium{}`, `referee{}`), not an array. See ADR 0010 and ADR 0012.

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
