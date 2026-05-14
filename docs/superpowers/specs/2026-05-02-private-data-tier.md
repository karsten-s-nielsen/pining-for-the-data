# Private Data Tier — Design Spec

**Date:** 2026-05-02
**Status:** Proposed
**Scope:** Two-tier auth model for the mock provider API (public + owner), enabling private data sources (Gradient Sports, Soccermatics Pro) to coexist with the existing redistributed open data on the same infrastructure. Adds provider-level reference resource endpoints (starting with `/players`) and CloudTrail audit logging.

---

## 1. Purpose

The mock provider API currently serves redistributed open data (SkillCorner V3 A-League) under a single shared bearer token, intentionally documented as public. Two upcoming use cases require the same API surface to also gate restricted content:

1. **Gradient Sports FIFA World Cup 2022** — access granted to the repo owner; licence terms being clarified with the source. Treated as restricted until clarification arrives in writing. Roughly 64 matches plus tournament-level reference files.
2. **Soccermatics Pro cohort data** — restricted by course enrolment terms. Provider identity not yet known; may overlap with a provider already in the public tier.

The goal is a single, formal API the Lakehouse adapter can call regardless of whether content is open or restricted. Mixing tiers cleanly — including within a single real-world provider — is a hard requirement.

This repository will be made public. The design has been chosen with that in mind: secrets live outside the repo (SSM Parameter Store), example payloads contain no third-party operational details, and the licence/provenance fields recorded for restricted content are deliberately neutral.

## 2. Threat model and non-goals

**In scope:** preventing accidental disclosure of private content to public-token holders; enforcing tier separation at both the application layer and the S3 path layer; making licence/provenance per-match visible to the upload tooling; producing an audit trail of access to restricted content. The threat model assumes a single-owner setup — the OWNER token is held by the operator (or systems they control like the Lakehouse adapter) only; loading the operator's own copy of a restricted dataset into their own private-tier API is data movement within their own systems, not redistribution.

**Out of scope:** multi-user access control (only the repo owner consumes private content), token vending/JWT (deferred per the original API design's upgrade path), tampering by a malicious owner-token holder (the owner is the only party with that token), data exfiltration via side channels (timing, error message inference — addressed below by uniform 404 semantics, not by full mitigation).

**Trust boundary:** the API is the only sanctioned read path. The S3 bucket remains private (block all public access, KMS-encrypted); presigned URLs from the artifact and file handlers are the only egress channel.

## 3. Authentication: two-tier bearer tokens

### 3.1 Tokens

| Tier | Source | Visibility | Purpose |
|------|--------|------------|---------|
| `public` | `var.api_token` in tfvars | Documented in README | Open data — anyone, including forkers |
| `owner` | SSM Parameter Store SecureString `/pining-for-the-data/api_token_owner` | Never committed | Private data — repo owner only, used by the Lakehouse adapter |

Both tokens are accepted on every endpoint. The handler classifies the request by which token matched and uses that tier to decide what content to expose.

### 3.2 Validator change

`shared.validate_token` currently returns `None` on success or an error response on failure. It will return one of:

```python
class Tier(StrEnum):
    PUBLIC = "public"
    OWNER = "owner"

# returns Tier on success, error response dict on failure
def validate_token(event: dict) -> Tier | dict:
    ...
```

Constant-time comparison (`hmac.compare_digest`) is preserved against both tokens before classifying. If both tokens are the same string, the handler classifies as `public` — fail closed. The only realistic way both tokens collide is operator misconfiguration (e.g., the SSM parameter is left at its placeholder, or both env vars get pasted from the same source); in that scenario, granting owner-tier visibility to anyone holding the public token would silently expose private content. Failing closed degrades the owner consumer (visibly broken) instead of leaking data (silently broken), and a visibly broken consumer gets fixed.

### 3.3 SSM Parameter Store integration

The owner token lives in SSM Parameter Store as a `SecureString`, encrypted with the same KMS key as the data bucket. Lambda fetches it once at cold start and caches in module scope:

```python
@functools.cache
def _get_owner_token() -> str:
    ssm = boto3.client("ssm")
    return ssm.get_parameter(Name=os.environ["OWNER_TOKEN_PARAM"], WithDecryption=True)["Parameter"]["Value"]
```

Rotation: update the SSM value, force-refresh the Lambda by editing an env var or publishing a new version (Terraform `null_resource` with `triggers`). Cache TTL of "lifetime of warm container" is acceptable — worst case is a few minutes of stale token after rotation.

Lambda IAM gains:

```hcl
{
  Effect   = "Allow"
  Action   = ["ssm:GetParameter"]
  Resource = aws_ssm_parameter.api_token_owner.arn
}
{
  Effect   = "Allow"
  Action   = ["kms:Decrypt"]
  Resource = aws_kms_key.data.arn  # already present, reused for SSM SecureString
}
```

The public token continues to ride on the existing `API_TOKEN` env var for parity with the original deployment. The owner token does not, because env vars are visible in tfstate plaintext and in the Lambda console.

### 3.4 Why SSM Parameter Store, not Secrets Manager

For a single owner-controlled consumer, the maintenance profile of the two services is:

- **SSM Parameter Store**: one parameter, no rotation framework, no scheduled jobs. Manual rotation is `aws ssm put-parameter --overwrite ...` followed by an env-var bump on the Lambdas to bust the warm cache. Ongoing maintenance: zero unless and until you choose to rotate.
- **Secrets Manager**: same one-line manual rotation works the same way. The differentiator is built-in *automatic* rotation via a rotation Lambda you write and own — but automatic rotation is designed for credentials whose consumers must handle change gracefully (database passwords, OAuth tokens). A static bearer used by one CLI consumer under your control doesn't benefit. If you adopted the rotation feature, you'd be taking on a rotation Lambda as a new piece of infrastructure to maintain (Python upgrades, IAM, error handling, alarms) for no actual security uplift.

Net: maintenance is **identical** across the two if you don't use automatic rotation; **higher with Secrets Manager** if you do. SSM is the simpler primitive that fits the use case. The cost gap ($0.40/mo) is irrelevant either way; the design rationale is "fewer moving parts."

If the assumption changes — e.g., a second private consumer is onboarded and the token starts being shared in places where automatic rotation would actually help — the SSM-to-Secrets-Manager swap is mechanical (change one Terraform resource and one IAM action; the boto3 call is two lines different).

### 3.5 Consumer onboarding and rotation

This section defines how the owner-tier token reaches the consumer (currently only the Lakehouse adapter, owned by the same operator) and how a rotation is coordinated end-to-end. The transfer of a bearer token is operationally significant; an undocumented handshake invites the operator to invent a quick procedure that ships the token through Slack or pastes it into a CI variable inadvertently.

**Initial delivery.** The operator generates the token via `secrets.token_urlsafe(32)`, writes it to SSM, and transfers it to the consumer's secret store out-of-band — password manager export, signed envelope, or a one-time secret link. Never email, chat, ticket comment, screenshot, or commit. The consumer reads it from its own secret manager into a `PINING_API_TOKEN` env var at process start; it is not persisted to disk inside the consumer.

**Rotation handshake.** The owner-token cache (`_get_owner_token`) is `functools.cache`-decorated and lives for the lifetime of a warm Lambda container. A bare `aws ssm put-parameter --overwrite` updates the source of truth but warm containers continue serving the old token until they recycle (could be hours under low traffic). Rotation therefore takes three coordinated steps:

1. **Operator** writes the new token to SSM and to the consumer's secret store, in either order.
2. **Operator** invalidates the Lambda cache by bumping a no-op env var (e.g., `LAST_ROTATION=2026-05-02T14:00:00Z`) on **every Lambda function** in the API: `list_providers`, `list_matches`, `get_artifact`, `list_players`, `get_player`. All five must be updated in the same `terraform apply`; otherwise endpoints will diverge — `/matches` accepts the new token while `/players` still accepts the old one, depending on which handler was bumped — and a consumer making mixed-endpoint calls will see split-brain auth behaviour. The env-var bump forces AWS to provision new containers, which fetch the new SSM value on first invocation.
3. **Consumer** reloads its process (or rotates its env var via the secret manager's standard mechanism) so the next call uses the new token.

**No dual-validity guarantee in v1.** The API does NOT keep both tokens valid during the rotation window. Concretely:

- After step 1 but before step 2: every warm container is still on the old token. A consumer that has already adopted the new token will see 401 on every call until step 2 lands and AWS replaces the warm containers (which can take seconds to minutes depending on invocation rate).
- After step 2 but before step 3: the operator may have bumped the env vars, but warm containers carrying the old cached token continue to serve it until AWS recycles them. A consumer that has already dropped the old token will see random 401s from those surviving warm containers; a consumer still holding the old token will see random 401s from new containers carrying the new token. The two warm-container populations coexist for a window that depends on Lambda's container-replacement cadence under load.

Symmetric consequence: during any rotation window, both consumer-old-only and consumer-new-only fail. Consumers MUST therefore implement transient-401 retry (with backoff) for the duration of any planned rotation, OR drain themselves before the operator runs step 2. The Lakehouse adapter holds only one token at a time and retries on 401 with exponential backoff up to 60 seconds, which covers the typical Lambda container recycle window. If the operational pattern ever needs zero-downtime rotation, §11.6 sketches the dual-validity upgrade path.

**Recommended cadence.** No scheduled rotation for v1. Rotate on suspected exposure or a six-monthly cadence at most — the rotation cost (manual Terraform + consumer redeploy) outweighs the security benefit at this scale.

**Recovery.** If the SSM parameter is corrupted or lost, generate a new token, follow the rotation handshake, and audit CloudTrail (section 7) for any access between corruption and recovery.

## 4. Authorisation: visibility at the match level

Visibility is **not** a property of the provider. The same real-world provider can supply both open and restricted content. (Concrete case: SkillCorner public V3 data in `skillcorner/`, plus potentially-SkillCorner Soccermatics Pro content; treating them as one provider keeps the Lakehouse adapter free of "is this provider public?" branching.)

### 4.1 Match-level visibility flag

`{provider}/matches.json` gains two fields per entry: `visibility` and `updated_at`.

```json
{
  "provider": "gradientsports",
  "matches": [
    {
      "id": "example-001",
      "date": "2022-11-21",
      "home": "Team Alpha",
      "away": "Team Beta",
      "artifacts": {
        "metadata": "metadata.json",
        "events":   "events.json",
        "roster":   "roster.json",
        "tracking": "tracking.jsonl.bz2"
      },
      "visibility": "private",
      "provenance": "original",
      "updated_at": "2026-05-02T14:23:11Z",
      "source": {
        "name": "Gradient Sports",
        "url": "https://www.gradientsports.com/",
        "licence": "Restricted; redistribution not permitted pending licence clarification"
      }
    }
  ]
}
```

Identifiers and team names in the example above are illustrative — the spec deliberately does not commit any (provider id → real entity) mapping for Gradient Sports or any other restricted provider, since that mapping is itself the licensed data the redistribution-licence gate (§8.3) protects.

`visibility` is required on new entries. For backwards compatibility, missing `visibility` is treated as `"public"` (matches the historical assumption for redistributed open data).

`updated_at` is an ISO 8601 UTC timestamp set by the upload tooling on every write. Re-uploads update the timestamp even if no other field changes; this gives consumers a single signal to drive incremental refresh ("anything updated since my last poll?") without per-artifact content hashing. Stronger integrity signals (per-artifact ETags, SHA256 manifests) are deferred until a consumer asks for them — `updated_at` is sufficient for the polling pattern the Lakehouse adapter will use.

`artifacts` is an object mapping artifact name → exact filename (relative to the match's S3 prefix). The keys form the artifact whitelist (consumed by `get_artifact`, see §4.3); the values let the API return a presigned URL without per-request S3 listing. This is a deliberate change from the original sketch, which used an array of names and forced `get_artifact` to list S3 by prefix on every request to discover the file extension. The object form costs one extra string per artifact in the index and removes one S3 call per artifact request — the trade is overwhelmingly in favour of the object form even at our scale, and necessary if the Lakehouse ever does parallel ingest of all matches.

The full canonical shape of a match entry is defined by a Pydantic model `MatchEntry` in `src/canonical/models.py` and published as JSON Schema at `schemas/matches.schema.json` (see section 6.6 for the schema lifecycle and the rationale for keeping the models outside the Lambda source dir). The example above is illustrative; the schema is authoritative.

### 4.2 Listing semantics

`list_matches` filters its response by tier:
- `public` tier sees only entries with `visibility == "public"`.
- `owner` tier sees all entries.

If filtering produces an empty list and the underlying `matches.json` is non-empty, the response is still 200 with `{"matches": []}` — not 404 — so the public tier cannot probe for the existence of any private matches.

`list_providers` returns the same provider list to both tiers — explicitly. Rationale: the *existence* of a provider is not a secret; the per-match visibility flag is the only enforcement boundary. Hiding providers from the public tier would force the Lakehouse adapter to know which providers it has access to out-of-band, which is the opposite of what an API is for. A consumer hitting `/v1/gradientsports/matches` with the public token sees an empty matches list — which is correct: `gradientsports` exists, you just don't have access to anything in it.

A unit test (`test_public_tier_sees_gradientsports_in_provider_list`) pins this behaviour so a future contributor doesn't quietly add tier filtering thinking it tightens security.

`list_players` and `get_player` (section 6) follow the same rule for unknown-provider 404 as `list_matches`: gate on `providers.json` membership before reading the per-provider index, so an unknown provider returns 404 from every endpoint and the public tier cannot enumerate the provider namespace by behaviour fingerprinting.

### 4.3 Artifact retrieval semantics

`get_artifact` resolves a match by reading `matches.json` once, then:

1. If the match doesn't exist → **404** "match not found".
2. If the match exists but `visibility == "private"` and the caller is `public` tier → **404** "match not found" (uniform with case 1 — no existence leak).
3. If the match exists and the tier is allowed but the requested artifact name is not a key in the entry's `artifacts: {...}` object → **404** "artifact not found". The `artifacts` object is a **whitelist**, not advisory documentation: a name outside it is unreachable through the API even if a matching file exists in S3 under the match prefix. This prevents accidental exposure if an upload script lands a file (debug dump, leftover staging) under a match's prefix without registering it in the index.
4. If the artifact name is whitelisted, look up its filename via `artifacts[name]`, build the S3 key as `{prefix_root}/{filename}`, generate the presigned URL directly via `s3.generate_presigned_url(...)` and return **302**. No S3 list, no head_object — the index is the source of truth for the file's existence (the upload tool wrote both atomically).

If the operator deletes a file out-of-band without updating the index, the presigned URL will return 404 from S3 when the consumer follows the redirect. That's acceptable degradation: the consumer sees "the API thinks this exists but S3 disagrees," which is exactly what's true. CloudTrail (§7) records the failed GET so the inconsistency is investigable.

The `matches.json` read adds one S3 GET per artifact request. At our scale this is free under the AWS Free Tier and cheap thereafter; with the per-request list call removed, the steady-state cost is one S3 GET per artifact request rather than two (one GET + one LIST).

### 4.4 Why uniform 404, not 401/403

Returning 403 on tier mismatch leaks the existence of private matches: an attacker with the public token could enumerate match IDs and learn which exist privately. 404 collapses both "doesn't exist" and "exists but you can't see it" into one response. The owner-tier consumer (Lakehouse) never hits this path because it presents the owner token.

## 5. S3 layout: physical separation by tier

Visibility is enforced both in the application layer (sections 3 and 4) and at the S3 path layer. Physical separation enables future IAM-level enforcement (separate Lambdas with separate roles, each scoped to its own prefix) without restructuring data.

### 5.1 Layout

```
karstenskyt-pining-for-the-data/
├── providers.json
├── skillcorner/
│   ├── matches.json                    # public + private entries together
│   ├── players.json                    # public-tier player reference (empty if absent)
│   ├── game_03/                        # public match
│   │   ├── match.json
│   │   └── tracking.jsonl
│   └── _private/                       # reserved segment, owner-tier only
│       ├── players.json                # private-tier player reference
│       └── soccermatics_match_42/
│           └── ...
└── gradientsports/
    ├── matches.json                    # all-private initially
    └── _private/
        ├── players.json                # ~2,300 player records, all private
        ├── example-001/
        │   ├── metadata.json
        │   ├── events.json
        │   ├── roster.json
        │   └── tracking.jsonl.bz2
        └── example-002/
            └── ...
```

The `example-NNN` IDs above are placeholders. Real Gradient Sports IDs are short numeric strings (e.g., 4–5 digits); they're not pinned to specific match identities in this spec because that mapping is itself the licensed data the redistribution-licence gate (§8.3) protects.

### 5.2 Reserved-segment rules

- All path parameters (`{provider}`, `{id}`, `{artifact}`, `{name}`) MUST match `^[a-zA-Z0-9][a-zA-Z0-9_-]*$` and be 128 characters or fewer. The leading-alphanumeric requirement is what reserves leading `_` for namespace markers (`_private` is currently the only one). Underscores remain valid mid-string (e.g., `game_03`).
- The same regex governs upload tooling, so it is impossible to write S3 objects under a path that the API cannot then validate as input.
- Future providers MUST choose IDs that satisfy the regex. Gradient Sports' short numeric IDs and SkillCorner's `game_NN`-style IDs both qualify.
- `pining-upload` writes to `{provider}/_private/{game_id}/...` when `--visibility private` is passed; otherwise to `{provider}/{game_id}/...` as today.
- `pining-upload-players` (section 6.4) writes the index to `{provider}/players.json` (public) or `{provider}/_private/players.json` (private), depending on visibility.
- Handlers resolve the prefix from the recorded visibility, never by trial-and-error scanning. This avoids any timing channel that could distinguish private-but-tier-rejected from genuinely-absent.

### 5.3 Why one bucket and one KMS key

Separate buckets per tier was considered and rejected. With a single private user, two buckets doubles operational surface — duplicate Terraform, duplicate KMS rotation, duplicate logging — for blast-radius isolation that is already adequate at the application layer. The IAM hook in section 5.1 leaves the door open to harden further if the assumption changes (e.g., a second private consumer is onboarded).

## 6. Reference resources (provider-level nouns)

Some provider data is not associated with a single match: player biographical data, team metadata, competition catalogues. Industry practice (StatsBomb, Wyscout, Opta) treats these as first-class API resources with proper nouns — `/players`, `/teams`, `/competitions` — rather than as opaque files. This API follows the same pattern.

For v1, only `/players` is implemented. Adding more nouns (e.g., `/teams`) follows the same template and requires no architectural change.

### 6.1 Endpoints

```
GET /v1/{provider}/players              → list players (full catalogue, filtered by tier)
GET /v1/{provider}/players/{id}         → individual player record
```

Both endpoints require the same auth as existing endpoints. Tier filtering and uniform-404 semantics carry over from sections 4.2 and 4.3.

**Reserved query parameters.** The `/players` list endpoint reserves `?limit=`, `?offset=`, `?cursor=`, `?team_id=`, and `?competition_id=` for forward-compatible pagination and filtering. v1 ignores these silently (the API serves the full catalogue regardless). They are documented as reserved so a future implementation can switch them on without consumers needing to re-think URL construction or request signing. v1 catalogues fit comfortably in the Lambda 6 MB sync response cap (Gradient Sports WC2022 ≈ 500 KB for 2,322 players); pagination becomes load-bearing when a single provider catalogue exceeds ~5 MB.

### 6.2 Access pattern

Industry providers expose a list endpoint as the primary access pattern: consumers fetch the whole catalogue once, cache it locally, and join client-side. Individual `/players/{id}` exists for sparse-access convenience. Range/multi-id queries (e.g., `?ids=1,2,3`) are not idiomatic and not implemented.

For Gradient Sports World Cup 2022 (~2,322 records, ~350 KB JSON), the full list comfortably fits in a single response. Pagination is deferred until a provider's catalogue exceeds ~5 MB. Optional query filters on the list endpoint (`?team_id=`, `?competition_id=`) can be added later without breaking changes.

### 6.3 Index format and canonical player record

`{provider}/players.json` (public) and `{provider}/_private/players.json` (private) — two physically-separate files, one per tier. The owner-tier handler reads both and merges; the public-tier handler reads only the public file. This mirrors the `_private/` pattern used for matches and keeps the defense-in-depth posture: a private record never sits in a file the public-tier handler is allowed to open.

A canonical player record is defined here so a future provider's data shape doesn't quietly become whatever the second uploader needs. The shape is enforced by a Pydantic model `PlayerRecord` in `src/canonical/models.py`, published as JSON Schema at `schemas/players.schema.json`, and validated at write time by `pining-upload-players`.

**Required fields:**
- `id` (string): provider-stable player identifier; matches `^[a-zA-Z0-9][a-zA-Z0-9_-]*$`. Provider IDs need not be globally unique across providers (different providers may both use a numeric id `1234` for unrelated players); the `(provider, id)` pair is the global key.
- One of (`nickname`) **or** (`firstName` + `lastName`): at least one human-readable name handle is required. Gradient Sports supplies all three.
- `visibility`: `"public"` or `"private"`. Set by the upload tooling, not the source data.
- `updated_at`: ISO 8601 UTC timestamp; same semantics as `matches.json` `updated_at` — set on every write, including no-op re-uploads.

**Optional fields (recognised by the canonical schema):**
- `firstName`, `lastName`, `nickname` (any subset of the three not covered by the required-name rule above)
- `dob`: ISO 8601 date string `YYYY-MM-DD`
- `height`: number, centimetres
- `position`: free-text positional code (single position; provider-defined vocabulary)
- `positionGroupType`: free-text grouped positional code (e.g., Gradient Sports' `D`, `M`, `F`, `GK`)
- `nationality`: ISO 3166-1 alpha-3 country code (recognised; not required by Gradient Sports data)
- `source`: object with `name`, `url`, `licence` strings

**Provider-specific extensions.** Anything beyond the canonical fields is allowed and round-tripped verbatim — `additionalProperties: true` in the JSON Schema. A consumer reading the canonical schema can safely ignore unknown fields. This keeps Gradient Sports' pass-through fidelity (any field Gradient Sports ships is preserved) without requiring the canonical schema to enumerate every provider's bespoke columns.

**Example (Gradient Sports):**

```json
{
  "provider": "gradientsports",
  "players": [
    {
      "id": "example-007",
      "firstName": "Example",
      "lastName": "Player",
      "nickname": "Example Player",
      "dob": "2001-06-17",
      "height": 182.0,
      "positionGroupType": "D",
      "visibility": "private",
      "updated_at": "2026-05-02T14:23:11Z",
      "source": {
        "name": "Gradient Sports",
        "url": "https://www.gradientsports.com/",
        "licence": "Restricted; redistribution not permitted pending licence clarification"
      }
    }
  ]
}
```

Field names mirror the source provider's conventions for pass-through fidelity (`firstName`, `lastName`, `positionGroupType` here are Gradient Sports'). Cross-provider normalisation, if ever wanted, belongs in the Lakehouse adapter, not in this API.

### 6.3.1 Cross-tier ID precedence

A single `(provider, id)` SHOULD NOT appear in both `{provider}/players.json` and `{provider}/_private/players.json`. The upload tooling (section 6.5) actively prevents this by checking both files before writing. If it ever happens at runtime — race during upload, manual S3 edit, or a re-tiering operation that didn't clean up — the precedence rule is **private wins for owner-tier list/get**: the owner sees the more-specific real record, the public record is masked. Public-tier behaviour is unaffected (it never reads the private index). This precedence is documented so behaviour is predictable; the ID-collision case is treated as the data error it is, not a feature.

### 6.4 Lambda handlers

Two new handlers, both small and following the same shape as existing ones:

| Handler | Input | Logic | Response |
|---------|-------|-------|----------|
| `list_players` | `{provider}` | Verify `{provider}` is in `providers.json`; read `{provider}/players.json` (and `_private/players.json` for owner tier), merge with private precedence, return | 200 + JSON, or 404 on unknown provider |
| `get_player` | `{provider}`, `{id}` | Verify `{provider}` is in `providers.json`; same read, find by `id`, return single record. 404 if provider unknown OR player not found OR found-but-private-and-public-tier | 200 + JSON or 404 |

Both reuse `validate_token`, `validate_path_param`, the S3 client, and the json response builder from `shared.py`. Lambda memory/timeout/concurrency match the existing handlers. Note that unlike `get_artifact`, `get_player` returns the JSON record directly (no presigned URL) — player records are small structured data, not large binary artifacts.

The unknown-provider 404 matches `list_matches`'s behaviour (which 404s on `NoSuchKey` for `{provider}/matches.json`). Without this gate, `list_players` would return `200 + {"players": []}` for any made-up provider name, which fingerprints "unknown provider" vs "known provider with all-private content" — exactly the side channel uniform-404 elsewhere is designed to suppress. The gate adds one extra S3 GET (`providers.json`) per `/players` request; cost is negligible at our scale and the file is tiny.

### 6.5 Upload tooling

New CLI: `pining-upload-players`. Input is canonical-JSON only — a list of `PlayerRecord` objects matching the schema in section 6.3. Provider-specific shapes (Gradient Sports' CSV, future providers' formats) are normalised to canonical JSON by a one-shot script under `scripts/` before upload, not by the CLI itself. This keeps the CLI and Lambda handlers free of per-provider branching, and forces the canonical schema to be the explicit contract every provider goes through.

```bash
pining-upload-players players.json \
  --provider gradientsports \
  --bucket karstenskyt-pining-for-the-data \
  --visibility private \
  --source-name "Gradient Sports" \
  --source-url "https://www.gradientsports.com/" \
  --source-licence "Restricted; redistribution not permitted pending licence clarification"
```

The input file is either a JSON array of player objects, or `{"players": [...]}`. Each object is validated against the `PlayerRecord` Pydantic model before any S3 call; validation errors abort the upload with line/field-level diagnostics.

If the operator passes a CSV (or any non-JSON file), the CLI rejects it before reading with the message:

```
pining-upload-players accepts canonical JSON only (a list of PlayerRecord objects, or {"players": [...]}).
CSV input is not supported by this CLI — provider-specific shapes must be normalised to canonical JSON
by a provider-specific adapter. See scripts/upload_gradient_wc2022.py for a worked example.
```

The error names the reference adapter so the operator's next step is obvious without reading the spec.

Behaviour:
1. Read the input JSON.
2. Validate every record against `PlayerRecord`. Required fields: `id` and one of (`nickname` | `firstName`+`lastName`). `visibility` and `updated_at` are added by the CLI based on the `--visibility` flag and the current UTC time.
3. Read-modify-write `{provider}/players.json` (public) or `{provider}/_private/players.json` (private). Existing entries (matched by `id`) are replaced; new entries are appended. The output is sorted by `id` for deterministic diffs.
4. **Cross-tier dedup check.** Before writing, the CLI reads BOTH `{provider}/players.json` and `{provider}/_private/players.json`. If any incoming `id` already exists in the *other* tier, the upload fails. This catches the "tier mixing for a single player ID" case described in section 6.3.1, including the case where a public record exists for some `id` and a private re-upload would silently shadow it (or vice versa).
5. Re-tiering an existing record (changing its visibility) is not supported in v1 — the CLI rejects it. The manual procedure is documented in section 11.

**Justification for canonical JSON-only input.** The original sketch had `pining-upload-players` autodetect CSV vs JSON. That made it convenient to throw Gradient Sports' CSV at the CLI directly, but it also meant Gradient Sports' column names became the de-facto canonical shape via the path of least resistance. Forcing canonical JSON makes the schema boundary explicit: a new provider must write a CSV→canonical adapter (or use whatever shape they natively ship in) and that adapter is reviewable code, not a hidden code path inside the CLI.

For Gradient Sports specifically, the CSV→canonical adapter lives in `scripts/upload_gradient_wc2022.py` (section 8.3) — the same script that orchestrates the per-match uploads. Gradient Sports' columns (`dob`, `firstName`, `height`, `id`, `lastName`, `nickname`, `positionGroupType`) map directly to canonical fields with no semantic translation; the adapter is a few lines of `csv.DictReader` plus type coercion.

### 6.6 JSON Schemas and Pydantic models

The canonical shapes for `matches.json` entries and `players.json` entries are defined as Pydantic models (`MatchEntry`, `PlayerRecord`) in `src/canonical/models.py`. The upload CLIs validate against these models before any S3 write. Lambda handlers do NOT import the models — they consume already-validated dict payloads from S3 and only need the runtime utilities in `terraform/modules/functions/src/shared.py`. This keeps the Lambda zip dependency-free (no pydantic at runtime) while preserving a single schema source of truth on the write path.

The models are also published as JSON Schema files at:

```
schemas/
├── matches.schema.json
└── players.schema.json
```

These are committed to the repo and generated from the Pydantic models by `scripts/regenerate_schemas.py`. A unit test asserts the committed files match `Model.model_json_schema()` output for the current models, so a Pydantic edit that doesn't regenerate the schemas fails CI.

Consumers that aren't Python (or that don't want to depend on this repo's source) can validate against the JSON Schemas directly, or generate Pydantic models from them via `datamodel-code-generator` in their own codebase. The schemas are the public contract; Pydantic is one of several ways to consume that contract.

**Schema metadata.** Each generated schema file embeds two top-level fields by JSON Schema convention:

- `$schema`: `"https://json-schema.org/draft/2020-12/schema"` — declares the JSON Schema draft the file conforms to. Pydantic v2 emits this by default.
- `$id`: a stable URN identifying the schema. v1 uses URN form to signal that consumers SHOULD NOT attempt HTTP resolution: `urn:pining-for-the-data:schema:matches:v1` and `urn:pining-for-the-data:schema:players:v1`. The URN is stable across schema-file edits (additive changes don't bump the `:v1` segment); a future breaking change publishes a new schema file with `:v2` alongside the current one.

The drift test (asserts `model.model_json_schema()` matches the committed file) covers `$id` and `$schema` automatically — these are part of the Pydantic-generated output. The Pydantic models therefore declare the URN via `model_config = ConfigDict(json_schema_extra={"$id": "urn:..."})`.

**Versioning.** The schemas are unversioned-as-content in v1 (the `:v1` URN segment is the version handle). Fields are added; never removed or renamed. If a breaking change ever becomes necessary, it'll be expressed as a new schema file with a `:v2` URN, alongside the current one — but section 9 (Consumer contract) declares the additive-only policy, and v1 has no consumers depending on the current shape, so breaking changes are still free until the first external dependency is taken.

### 6.7 What we do NOT do

- **No `/competitions` endpoint for v1.** Gradient Sports' `competitions.csv` is essentially a directory of match IDs by tournament — that's already reachable via `GET /v1/gradientsports/matches` (which can be extended with a `?competition_id=` filter when needed). Republishing it as a separate resource would duplicate the navigation already provided by `/matches`.
- **No `/teams` endpoint for v1.** Per-match `metadata.json` already carries the team object (`{id, name, shortName, kit}`). There's no team-level data not already on matches. Easy to add later if a future provider ships team-level reference data not derivable from matches.
- **No generic `/files` escape hatch.** If a future provider ships something that genuinely doesn't fit a noun (a PDF schema, a binary calibration file), we'll add a noun for it then. Designing a generic escape hatch up front incentivises lazy modelling.

## 7. Audit logging

CloudTrail data events on the data bucket. Enabled at deploy time, not deferred — the licence ambiguity around the initial Gradient Sports load makes a defensible access trail valuable from day one.

### 7.1 Components

A new Terraform module `terraform/modules/audit/`:

- A second S3 bucket `karstenskyt-pining-for-the-data-audit` for log storage. Block all public access, SSE-KMS, versioning enabled, lifecycle policy expiring objects after 365 days (configurable variable).
- An organisation-independent CloudTrail trail capturing S3 data events scoped to *only* the data bucket ARN. Management events disabled — they're noise for this use case.
- IAM policy on the audit bucket allowing `cloudtrail.amazonaws.com` to `PutObject` with `bucket-owner-full-control`.

### 7.2 What gets logged

Per S3 GET/PUT/DELETE on the data bucket: timestamp, source IP, user agent, IAM principal (Lambda role for API-initiated reads; the requesting AWS identity for direct uploads), bucket name, object key, request parameters, response code.

Critically, presigned-URL access is captured: the GET that follows a 302 from `get_artifact` happens directly between the requester and S3, and CloudTrail records it with the requester's source IP and user agent. That's exactly what's needed to demonstrate (or refute) "did this artifact get downloaded, and by whom?" later.

### 7.3 Cost

CloudTrail data events: $0.10 per 100,000 events. Estimated annual events for our scale: low single-digit thousands of S3 reads, including the bookkeeping reads of `matches.json` and `players.json` from every Lambda invocation. Even if every API request generated three S3 GETs (an exaggeration), that's still under $1/year at our traffic. Realistic annual cost: well under $1.

S3 storage for logs: gzipped JSON, ~2 KB per event. At the projected event count, far under 100 MB/year, effectively free under standard storage pricing.

### 7.4 Read access to logs

Owner-only via direct AWS Console / CLI against the audit bucket. No API endpoint to expose audit events publicly. If a consumer needs aggregated stats later, that's a derived view (separate skill, separate scope) — the raw events stay locked down.

### 7.5 Reserved naming and exclusions

- The audit bucket name is fixed by the storage module; no naming collision risk with the data bucket since a `-audit` suffix is appended.
- The trail's event selectors exclude reads of `providers.json` only. This file is read on every `list_providers` invocation and contains no per-tier or per-provider sensitive content — it's a true bookkeeping object. Excluding it keeps the trail clean of the noisiest source of read traffic without losing any signal.
- Reads of `matches.json` and `players.json` are deliberately **kept** in the trail. These are the two endpoints most likely to be abused for enumeration: a public-tier holder repeatedly listing matches or players to map the catalogue's edges and infer the existence or shape of private content. Logging every read of these files preserves the audit trail's value as the primary forensic record of "who tried to enumerate what". The cost (a few hundred extra events per day at peak) is well within the under-$1/year budget, so the trade is overwhelmingly in favour of keeping them logged.
- Writes to all three files are always captured (writes correspond to upload events; the operator wants those visible).

## 8. Upload tooling

### 8.1 CLI changes to `pining-upload`

New flag:

```
--visibility {public,private}        default: public
```

Behaviour:
- `--visibility public` (default): writes to `{provider}/{game_id}/...`, records `visibility: "public"` in the match entry. Matches today's behaviour byte-for-byte.
- `--visibility private`: writes to `{provider}/_private/{game_id}/...`, records `visibility: "private"`.

The flag is recorded in the match entry; downstream tooling and the API resolve the actual S3 prefix from the entry, never from the flag passed at request time.

### 8.2 Validation hardening

- Reject any `{provider}`, `{id}`, `{artifact}`, or `{name}` value not matching `^[a-zA-Z0-9][a-zA-Z0-9_-]*$` (max 128 chars). The leading-alphanumeric requirement reserves leading `_` for tier and namespace markers.
- Reject mixing tiers within a single match: if `matches.json` has `id=X visibility=public` and a re-upload tries `--visibility private --game-id X`, fail with a clear error. Re-tiering a match requires an explicit move (manual procedure documented in section 11; not supported by tooling in v1).
- Both upload CLIs validate against the canonical Pydantic models (`MatchEntry`, `PlayerRecord`) before any S3 call. Validation errors include the offending field path and value.

### 8.2.1 CLI flag spelling

The canonical spelling for the `--source-licence` flag and the `licence` field in records is **British** — both upload CLIs (`pining-upload`, `pining-upload-players`) accept it. The American `--source-license` is accepted as a quiet alias on both CLIs (no deprecation warning; both spellings coexist indefinitely). The internal field name in the JSON output is always `licence` (British), regardless of which flag spelling was used at the CLI. Picking one canonical form prevents the "two-CLI inconsistency" caused by the original sketch shipping `--source-license` in `pining-upload` and `--source-licence` in `pining-upload-players` — which would have made operator-facing scripts brittle to which command they invoke.

### 8.3 Gradient Sports-specific upload helper

For the initial Gradient Sports World Cup 2022 load, the source layout is:

```
FIFA World Cup 2022/
├── Event Data/{id}.json
├── Metadata/{id}.json
├── Rosters/{id}.json
├── Tracking Data/{id}.jsonl.bz2
├── competitions.csv          # not uploaded — directory data covered by /matches
├── players.csv               # tournament-level → pining-upload-players
└── Gradient Sports Change Log.docx    # not uploaded
```

A small adapter script (`scripts/upload_gradient_wc2022.py`) is the right shape for this. Provider-specific reshaping doesn't belong in the public package surface, and `scripts/` is the conventional home for ops one-shots that should be checked in for repeatability without being shipped to consumers.

The script:

1. **Loads private-tier only — no licence gate.** Visibility is hardcoded to `"private"` for both matches and players. A single-owner private-tier load (data goes only into the operator's own private S3 bucket, served back only to the operator's own owner-token holder) does not engage redistribution licence concerns: it's the operator moving their own data between their own systems, not redistribution to a third party. The recorded `SOURCE_LICENCE` constant still reads `"Restricted; redistribution not permitted pending licence clarification"` — that's accurate metadata for the entries, but it's a record of provenance, not a runtime gate. If a public-tier upload mode is ever added to this script (e.g., after a permissive licence clarifies), THAT path will need its own licence-clarification gate before serving — but the private-tier path stays gate-free.
2. Iterates the 64 matches. For each, reshapes the source layout into a per-match staging directory:
   ```
   staging/{id}/
   ├── metadata.json
   ├── events.json
   ├── roster.json
   └── tracking.jsonl.bz2
   ```
3. Reads `Metadata/{id}.json` to extract `date`, `home.name`, `away.name`.
4. Invokes `pining-upload --provider gradientsports --game-id {id} --visibility private --provenance original --source-name "Gradient Sports" --source-url "https://www.gradientsports.com/" --source-licence "Restricted; redistribution not permitted pending licence clarification"` with those values.
5. After all matches: reads `players.csv`, normalises each row into a canonical `PlayerRecord` JSON dict (renaming/coercing fields to match the schema in section 6.3), writes the normalised list to a temp `players.json`, then invokes `pining-upload-players players.json --provider gradientsports --visibility private --source-licence "..."` with the same source metadata. The CSV→canonical normaliser lives in this script; `pining-upload-players` only consumes canonical JSON (per section 6.5).

`competitions.csv` is intentionally not uploaded. It is a tournament-level directory of match IDs, which is fully reachable via `GET /v1/gradientsports/matches` once all 64 matches are loaded. Republishing it as a separate resource would duplicate navigation that the matches endpoint already provides.

The script is idempotent: re-running re-uploads everything but produces no duplicate index entries. Useful both for the initial load and for re-runs after correcting any per-match metadata.

### 8.3.1 Post-load verification

After the bulk load (or after any future bulk-load of a different provider), `scripts/verify_gradient_load.py` (and the analogous `verify_<provider>_load.py` for future providers) runs an automated post-condition check that exits non-zero on:

- match count mismatch (owner-tier `/gradientsports/matches` does not return exactly 67 entries)
- player count mismatch (owner-tier `/gradientsports/players` does not return exactly 2,322 entries)
- visibility leak (public-tier `/gradientsports/matches` or `/gradientsports/players` returns any entries)
- artifact-fetch failure on a small spot-check set (e.g., 5 random matches × 4 artifacts each, owner-tier presigned-URL follow returns 200 + non-empty body)
- player spot-check failure (specific known-good IDs return 200 with the expected `firstName`/`lastName`)

The script replaces the manual `curl` smoke tests in the original sketch. Manual smoke is fine for a one-time check; it rots the moment the load is re-run six months later.

### 8.4 Source data hygiene

Source metadata may include provider-platform URLs requiring source-side authentication. These are preserved in the uploaded `metadata.json` as-is — the API is a faithful pass-through, not a transformation layer. The Lakehouse adapter is responsible for deciding whether to dereference such URLs. If a future audit requires scrubbing fields, that transformation belongs on the adapter side, not at upload.

## 9. Consumer contract

The API serves a single contract to every consumer; v1 has one consumer (the Lakehouse adapter) and no production traffic from external parties.

**Authentication:** consumers receive a single bearer token via the channel described in section 3.5. The Lakehouse adapter reads it from a `PINING_API_TOKEN` env var.

**Response shape:** every endpoint returns JSON conforming to the schemas published at `schemas/{matches,players}.schema.json` (section 6.6). Consumers SHOULD validate against the schemas; the API guarantees nothing about the parsed shape beyond what those files declare.

**Evolution policy: additive fields only.** Pining commits to adding fields and adding endpoints without ever removing or renaming. Specifically:

- A new field in a list-entry or record (e.g., today's `updated_at` addition) MUST NOT break consumers — they should ignore unknown fields. Pydantic models default to `extra="allow"`, which round-trips unknown fields rather than rejecting them.
- A new endpoint or query parameter is always safe to add.
- A field's *value type* never changes (string never becomes int; ISO date never becomes Unix timestamp).
- A field is never removed or renamed.
- An HTTP status code's meaning never changes (200 always means success; 404 always means not-found-or-tier-hidden).

If a genuinely breaking change is ever needed, it'll be expressed as a new schema file (`v2.matches.schema.json`) with a clear deprecation timeline communicated out-of-band — but at v1, with no external consumers and the data tier still private, breaking changes are free; the additive policy is forward-looking, not historical.

**Players access pattern:** fetch `/v1/{provider}/players` once per session, cache the catalogue locally, join player IDs from match rosters/events to the cached catalogue. The single-record `/v1/{provider}/players/{id}` exists for sparse-access cases but is not the primary access pattern.

**Provider visibility:** consumers SHOULD NOT hard-code which providers are public vs private. The API is the single source of truth — call `/v1/{provider}/matches` and react to the contents (or emptiness) of the response.

## 10. Migration

Existing data is unaffected. Concretely:

1. Provision the SSM parameter for the owner token out-of-band before deployment.
2. Deploy the updated stack: storage module unchanged, audit module new, functions module gains `list_players` and `get_player` plus the SSM IAM grant, api module gains the `/players` routes.
3. `pining-upload` existing entries continue to work — they have no `visibility` field, are treated as public, served to both tiers.
4. Backfill `visibility: "public"` into existing `matches.json` entries opportunistically (next time each is re-uploaded for any reason). No urgency — the missing-means-public default is forward-compatible.
5. Verify the deployed stack with one Gradient Sports match before bulk-loading the rest.
6. Run `scripts/upload_gradient_wc2022.py` to load the 64 matches plus the player catalogue.

No data movement of existing public matches. No breaking change to any existing API response (every new field is additive, every new endpoint is on a new path).

## 11. Future extensions

### 11.1 Per-token scopes
If a second private consumer is ever onboarded with access to a subset of private content, the two-tier model needs to grow into per-token scope lists. Today's two-tier design is a strict subset of that future model — migration is straightforward (owner becomes a token with `["*"]` scope).

### 11.2 Automated token rotation
Manual SSM rotation is sufficient for a single-user setup. If the operational pattern ever changes (multiple consumers, scheduled rotation policy), swap the SSM parameter for a Secrets Manager secret and add a rotation Lambda. The application-side change is two lines of boto3.

### 11.3 Gradient Sports licence clarification
Licence clarification has been requested from the source; awaiting response. Three outcomes change the design posture:

- **Confirmed open / formal permissive licence**: re-upload Gradient Sports content as public-tier; the provider stays one logical entity; no API change beyond `pining-upload --visibility public` for the same content (or a small helper to flip visibility on existing entries).
- **Confirmed restricted**: keep current design; record the licence text exactly as given.
- **No response**: stay private indefinitely. Re-evaluate after a reasonable window.

### 11.4 Re-tiering procedure (manual)

v1 doesn't ship a `pining-retier` CLI; the upload tooling rejects any re-upload that flips a match's or player's visibility (section 8.2). Re-tiering is a deliberately rare operation (typically: Gradient Sports licence clarifies as permissive, and 64 matches need to flip from private to public). The manual procedure:

1. **Plan the move.** List affected match IDs (or player IDs) up front. For Gradient Sports: every match in `matches.json`. For a partial re-tier: the explicit subset.
2. **Copy the S3 objects to the new prefix.** For each match: `aws s3 cp --recursive s3://$BUCKET/gradientsports/_private/$MATCH_ID/ s3://$BUCKET/gradientsports/$MATCH_ID/`. For the players index: copy the file to its new tier path.
3. **Edit `matches.json` (and `players.json`) under optimistic concurrency control.** Read the current index, capture its S3 ETag from the GET response (`obj["ETag"]`), flip every affected entry's `visibility` field, update `updated_at` to the current UTC time, write back via `s3.put_object(..., IfMatch=<previous_etag>)`. The conditional write fails fast (S3 returns 412 Precondition Failed) if the index was modified between read and write — typically by a concurrent `pining-upload` invocation. The operator handles failure by re-reading and retrying. A small one-shot Python script using boto3 is the right shape — do not hand-edit JSON.
4. **Delete the old objects.** `aws s3 rm --recursive s3://$BUCKET/gradientsports/_private/$MATCH_ID/` after step 2 has succeeded for every match. Skipping this leaves orphan files in the old prefix, invisible to the API but billed for storage. CloudTrail (section 7) captures the deletes.
5. **Verify.** Run the post-load verification script (section 8.3.1) against the new tier configuration: counts unchanged, public-tier now sees what owner-tier saw (or the agreed subset), no 404s on previously-working artifacts.

The orphan-file risk in step 4 is the main reason this isn't a one-button CLI: a pining-retier helper that did steps 2 and 3 but forgot step 4 would silently degrade to "data exists in two places, only one is served, storage doubles" — the kind of failure that goes unnoticed for months. A documented manual procedure makes the operator do step 4 deliberately.

If re-tiering becomes routine (more than once a quarter), the procedure earns a CLI then.

**Concurrency note for regular uploads.** The `pining-upload` and `pining-upload-players` tools also read-modify-write `matches.json` / `players.json`, but they do **not** use IfMatch — they assume the v1 single-operator model (§3, §8). The re-tiering procedure above adds IfMatch as a precaution because it's a high-stakes one-shot where a silent overwrite would be more damaging than a noisy retry, and the operator is already in "careful mode." If a future operational pattern introduces concurrent uploads (multiple ops running simultaneously, automated re-uploads from CI), the upload tools should adopt the same IfMatch pattern, and this assumption-call-out becomes a tracked debt.

### 11.5 Pagination
Reserved query parameters on `/v1/{provider}/players` (section 6.1) become load-bearing when a single provider catalogue exceeds ~5 MB. The forward path: implement `?limit=` and `?offset=` (offset-based for simplicity; cursor-based if a stability guarantee under concurrent writes ever matters), preserve current full-fetch behaviour when neither is supplied, document the sort order (`id` ascending) as the pagination invariant. The same surface extends naturally to `/v1/{provider}/matches` if its catalogue ever scales.

### 11.6 Zero-downtime token rotation
v1 accepts only a single owner token at a time (section 3.5). When the operational pattern can no longer tolerate the consumer 401-retry window (multiple consumers, scheduled rotation policy, SLA on the consumer that doesn't permit transient failures), the upgrade path is dual-validity: the API accepts both the current and a previous owner token for a configurable grace period.

Sketch:
- Replace the single SSM `SecureString` parameter with two parameters (`api_token_owner_current`, `api_token_owner_previous`) or a single JSON-valued parameter holding both plus an expiry timestamp.
- `validate_token` checks `hmac.compare_digest` against both, classifies any match as `Tier.OWNER`.
- Rotation procedure becomes: set `previous = current`, set `current = new`, bump env vars (existing step 2). After the grace period, run a cleanup that clears `previous`. Consumers can rotate at any point during the grace window.
- The grace period defaults to "one warm-container lifetime" — typically 15-60 minutes under steady traffic. Conservative default: 24h. Cleanup is automatic via a small Lambda triggered by EventBridge.

Cost vs current single-token model: one extra SSM parameter, one cleanup Lambda, ~20 lines in `validate_token`. The current single-token design is the strict subset; the migration is mechanical and additive (no breaking change to the public token or the visibility model).

## 12. Test plan

Unit (in `src/tests/test_lambda_handlers.py`):

- `validate_token` returns `Tier.PUBLIC` for the public token, `Tier.OWNER` for the owner token, error response for any other input. Duplicate-token misconfiguration classifies as `Tier.PUBLIC` (fail closed; section 3.2).
- `list_providers` returns the same provider list to both tiers (`test_public_tier_sees_gradientsports_in_provider_list`).
- `list_matches` returns only public entries to the public tier; returns all entries to the owner tier; returns 200 with empty list (not 404) when filtering removes everything; returns 404 for unknown providers.
- `list_players` mirrors the same tier-filter semantics: public tier sees only `players.json`; owner tier sees the merge of `players.json` and `_private/players.json`. Returns 404 for unknown providers (gated on `providers.json` membership). Cross-tier ID collision: owner-tier sees the private record (private wins; section 6.3.1).
- `get_artifact` returns 404 (not 403) for `visibility=private` matches accessed with the public tier; returns 302 for the same match accessed with the owner tier.
- `get_artifact` resolves the correct S3 prefix (`_private/` vs not) based on the match's recorded visibility.
- `get_artifact` returns 404 if the requested artifact name is not a key in the match entry's `artifacts: {...}` object, even if a file with that name exists under the match prefix in S3.
- `get_artifact` resolves the S3 key as `{prefix_root}/{artifacts[name]}` and calls `generate_presigned_url` exactly once per request; no `list_objects_v2` and no `head_object` calls happen on the success path.
- `get_player` returns 404 (not 403) for private player IDs accessed with the public tier; returns 200 + JSON for the same player with the owner tier; returns 404 for unknown providers.
- Path-param validator: accepts `game_03`, rejects `_private`, rejects empty, rejects values >128 chars, rejects values not matching `^[a-zA-Z0-9][a-zA-Z0-9_-]*$`.
- `MatchEntry` and `PlayerRecord` Pydantic models accept canonical examples; reject missing required fields with field-level diagnostics. `updated_at` is auto-populated on serialisation if absent.

Schema-drift test (in `src/tests/test_schemas.py`):

- For each Pydantic model with a published schema file (`MatchEntry → schemas/matches.schema.json`, `PlayerRecord → schemas/players.schema.json`), assert `model.model_json_schema()` equals the on-disk file. Editing a model without regenerating the schema file fails this test.

Upload-CLI tests (in `src/tests/test_upload.py` and `src/tests/test_upload_players.py`):

- `pining-upload` writes to `_private/` when `--visibility private`; rejects tier mixing on re-upload of an existing match ID; `--source-license` is accepted as alias for `--source-licence`; output JSON always uses `licence` (British) regardless of which input flag was used.
- `pining-upload-players` consumes canonical JSON only; rejects CSV input with the exact message documented in §6.5 (mentioning `scripts/upload_gradient_wc2022.py` as the reference adapter); rejects records that fail Pydantic validation; rejects cross-tier ID collision (incoming `id` already exists in the *other* tier's index).
- Schema files at `schemas/{matches,players}.schema.json` embed `$id` (URN form) and `$schema` (`json-schema.org/draft/2020-12/schema`); the drift test asserts both fields are present and match `model.model_json_schema()` output.

Integration:

- End-to-end smoke test against the deployed dev stack with both tokens, asserting tier-correct responses on 1 public + 1 private match, and on `/players` listings/lookups for both tiers.
- Negative test: attempt `pining-upload --provider _private`, `pining-upload --game-id _private`, and `pining-upload-players --provider _private` — all must fail before any S3 calls.
- `scripts/verify_gradient_load.py` (section 8.3.1) runs after the bulk load and exits non-zero on any post-condition failure.

Manual:

- Upload one Gradient Sports match end-to-end (operator picks any one ID via `$GRADIENT_SMOKE_MATCH_ID`) before bulk-loading the 67. Verify the Lakehouse adapter pulls it cleanly with the owner token and that the public token gets a 404.
- Verify a CloudTrail data event lands in the audit bucket within a few minutes of an artifact fetch and within a few minutes of a `/matches` or `/players` read.
- Rotation rehearsal: rotate the owner token via the procedure in section 3.5; confirm the API returns 401 for the old token within ~1 minute of step 2 and 200 for the new token immediately.

## 13. Resolved design questions

- **Owner-token storage** → SSM Parameter Store SecureString. Maintenance is identical to Secrets Manager for manual rotation; Secrets Manager only differentiates if you adopt automatic rotation, which adds a rotation Lambda to maintain for no security uplift in this setup. The swap, if ever needed, is mechanical. Section 3.4.
- **Duplicate-token classification** → `Tier.PUBLIC` (fail closed). A misconfiguration that accidentally collapses both tokens to the same string degrades the owner consumer (visibly broken) instead of leaking private content (silently broken). Section 3.2.
- **Consumer onboarding and rotation handshake** → documented inline in section 3.5. No separate `docs/CONSUMER_AUTH.md`; the spec is the canonical reference for the contract between operator and consumer.
- **Token rotation dual-validity** → not in v1. The API accepts only the current owner token; consumers MUST implement transient-401 retry during the rotation window in either direction (operator-ahead-of-consumer OR consumer-ahead-of-operator both produce random 401s from the in-flight warm-container population). Zero-downtime rotation is sketched in §11.6 as a future upgrade if the operational pattern ever needs it. Section 3.5.
- **Tournament-level reference data** → in scope for v1, modelled as first-class resource nouns rather than as opaque files. `/players` is the v1 noun, sourced from Gradient Sports' `players.csv` via a canonical-JSON normalisation step in `scripts/upload_gradient_wc2022.py`. `competitions.csv` is dropped since it duplicates the navigation already provided by `/matches`. `/teams` is deferred — current per-match metadata is sufficient. Section 6.
- **Canonical schemas** → defined as Pydantic models in `shared.py`, published as JSON Schema files in `schemas/` (with `$id` URN and `$schema` Draft-2020-12 metadata), drift-tested in CI. Both upload CLIs validate against the Pydantic models before any S3 write. Section 6.6.
- **`updated_at` per entry** → adopted in v1 for both matches and players. ISO 8601 UTC; set on every write including no-op re-uploads. Sufficient for the polling pattern Lakehouse will use; per-artifact ETag/SHA256 deferred until a consumer asks for stronger integrity. Section 4.1, 6.3.
- **Pagination** → query-param surface (`?limit=&offset=&cursor=&team_id=&competition_id=`) reserved on `/players` in v1; not enforced. v1 catalogues fit in the Lambda 6 MB sync cap. Becomes load-bearing at ~5 MB per provider. Section 6.1, 11.5.
- **Re-tiering** → manual procedure documented in section 11.4; no `pining-retier` CLI in v1. The orphan-file risk in step 4 (delete the old prefix) is the main reason this isn't a one-button operation. The procedure uses S3 `IfMatch=<etag>` for the read-modify-write of the indexes — fail-fast on concurrent modification — even though regular uploads accept the v1 single-operator assumption without IfMatch.
- **`list_providers` tier filtering** → returns the same provider list to both tiers. Existence of a provider is not a secret; per-match visibility is the only enforcement boundary. Pinned by a unit test. Section 4.2.
- **Unknown-provider 404 on `/players`** → `list_players` and `get_player` gate on `providers.json` membership before reading per-provider indexes, mirroring `list_matches`. Closes the side channel that would otherwise distinguish unknown-provider from known-provider-with-all-private-content. Section 4.2, 6.4.
- **Cross-tier ID collision** → upload tooling rejects it; runtime precedence is private wins for owner-tier. Documented as predictable behaviour for a data-error case, not a feature. Section 6.3.1.
- **CloudTrail data-event filter** → exclude only `providers.json` reads (true bookkeeping). `matches.json` and `players.json` reads stay logged because enumeration via `/matches` and `/players` is the most likely abuse vector and the trail is its forensic record. Cost stays well under $1/year either way. Section 7.5.
- **Artifacts as object (name → filename)** → `artifacts` is an object in `matches.json` rather than an array of names. Keys form the whitelist consumed by `get_artifact`; values are the exact filenames that let the API skip per-request S3 listing. One field, both purposes, no `list_objects_v2` on the artifact-fetch hot path. Sections 4.1, 4.3.
- **CLI flag spelling** → British (`--source-licence`, `licence`) is canonical; American (`--source-license`) is a quiet alias on both upload CLIs. Internal field name is always British. Section 8.2.1.
- **Gradient Sports licence redistribution gate** → no runtime gate for private-tier loads. The script always uploads private; a single-owner private-tier load is the operator moving their own data into their own systems and does not engage redistribution licence concerns. The `SOURCE_LICENCE` constant is recorded as accurate provenance metadata. If a public-tier upload mode is ever added, THAT path will need its own licence-clarification gate. Section 8.3.
- **Post-load verification** → automated by `scripts/verify_gradient_load.py`. Replaces manual `curl` smoke tests, which rot the moment the load is re-run. Section 8.3.1.
- **CloudTrail audit logging enablement** → enabled at deploy time. Section 7. Cost is well under $1/year and the trail is the defensible record if Gradient Sports (or any future restricted-content owner) asks "who downloaded what, when?"
- **Gradient Sports upload helper location** → `scripts/upload_gradient_wc2022.py`. Provider-specific reshaping doesn't belong in the public package surface; `scripts/` is the conventional home for repeatable ops one-shots.
- **API versioning** → no `/v2/` path versioning in v1. Additive-fields contract declared in section 9; v1 has no consumers, so breaking changes are still free until the first external dependency is taken.
