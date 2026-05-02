# Private Data Tier — Design Spec

**Date:** 2026-05-02
**Status:** Proposed
**Scope:** Two-tier auth model for the mock provider API (public + owner), enabling private data sources (PFF, Soccermatics Pro) to coexist with the existing redistributed open data on the same infrastructure. Adds provider-level reference resource endpoints (starting with `/players`) and CloudTrail audit logging.

---

## 1. Purpose

The mock provider API currently serves redistributed open data (SkillCorner V3 A-League) under a single shared bearer token, intentionally documented as public. Two upcoming use cases require the same API surface to also gate restricted content:

1. **PFF FIFA World Cup 2022** — access granted to the repo owner; licence terms being clarified with the source. Treated as restricted until clarification arrives in writing. Roughly 67 matches plus tournament-level reference files.
2. **Soccermatics Pro cohort data** — restricted by course enrolment terms. Provider identity not yet known; may overlap with a provider already in the public tier.

The goal is a single, formal API the Lakehouse adapter can call regardless of whether content is open or restricted. Mixing tiers cleanly — including within a single real-world provider — is a hard requirement.

This repository will be made public. The design has been chosen with that in mind: secrets live outside the repo (SSM Parameter Store), example payloads contain no third-party operational details, and the licence/provenance fields recorded for restricted content are deliberately neutral.

## 2. Threat model and non-goals

**In scope:** preventing accidental disclosure of private content to public-token holders; enforcing tier separation at both the application layer and the S3 path layer; making licence/provenance per-match visible to the upload tooling; producing an audit trail of access to restricted content.

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

Constant-time comparison (`hmac.compare_digest`) is preserved against both tokens before classifying. If both tokens are the same string, the handler classifies as `owner` (defensive default — should not happen but is fail-safe).

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

## 4. Authorisation: visibility at the match level

Visibility is **not** a property of the provider. The same real-world provider can supply both open and restricted content. (Concrete case: SkillCorner public V3 data in `skillcorner/`, plus potentially-SkillCorner Soccermatics Pro content; treating them as one provider keeps the Lakehouse adapter free of "is this provider public?" branching.)

### 4.1 Match-level visibility flag

`{provider}/matches.json` gains a `visibility` field per entry:

```json
{
  "provider": "pff",
  "matches": [
    {
      "id": "3812",
      "date": "2022-11-21",
      "home": "Senegal",
      "away": "Netherlands",
      "artifacts": ["metadata", "events", "roster", "tracking"],
      "visibility": "private",
      "provenance": "original",
      "source": {
        "name": "PFF FC",
        "url": "https://www.pff.com/",
        "licence": "Restricted; redistribution not permitted pending licence clarification"
      }
    }
  ]
}
```

`visibility` is required on new entries. For backwards compatibility, missing `visibility` is treated as `"public"` (matches the historical assumption for redistributed open data).

### 4.2 Listing semantics

`list_matches` filters its response by tier:
- `public` tier sees only entries with `visibility == "public"`.
- `owner` tier sees all entries.

If filtering produces an empty list and the underlying `matches.json` is non-empty, the response is still 200 with `{"matches": []}` — not 404 — so the public tier cannot probe for the existence of any private matches.

`list_providers` returns the same provider list to both tiers. Rationale: the *existence* of a provider is not a secret. Hiding providers would force the Lakehouse adapter to know which providers it has access to out-of-band, which is the opposite of what an API is for.

### 4.3 Artifact retrieval semantics

`get_artifact` resolves a match by reading `matches.json` once, then:

1. If the match doesn't exist → **404** "match not found".
2. If the match exists but `visibility == "private"` and the caller is `public` tier → **404** "match not found" (uniform with case 1 — no existence leak).
3. If the match exists and the tier is allowed → resolve the S3 prefix from the match's visibility, generate the presigned URL, return **302**.

The `matches.json` read adds one S3 GET per artifact request. At our scale (single-digit RPS at peak) this is free under the AWS Free Tier and cheap thereafter.

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
└── pff/
    ├── matches.json                    # all-private initially
    └── _private/
        ├── players.json                # 2,322 player records, all private
        ├── 3812/
        │   ├── metadata.json
        │   ├── events.json
        │   ├── roster.json
        │   └── tracking.jsonl.bz2
        └── 10502/
            └── ...
```

### 5.2 Reserved-segment rules

- `_private` is the only reserved path segment. The path-param validator (`_SAFE_PARAM` in both `shared.py` and `upload.py`) rejects any value that starts with `_` for `{provider}`, `{id}`, `{artifact}`, or `{name}`. Underscores remain valid mid-string (e.g., `game_03`).
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

### 6.2 Access pattern

Industry providers expose a list endpoint as the primary access pattern: consumers fetch the whole catalogue once, cache it locally, and join client-side. Individual `/players/{id}` exists for sparse-access convenience. Range/multi-id queries (e.g., `?ids=1,2,3`) are not idiomatic and not implemented.

For PFF World Cup 2022 (~2,322 records, ~350 KB JSON), the full list comfortably fits in a single response. Pagination is deferred until a provider's catalogue exceeds ~5 MB. Optional query filters on the list endpoint (`?team_id=`, `?competition_id=`) can be added later without breaking changes.

### 6.3 Index format

`{provider}/players.json` (public) and `{provider}/_private/players.json` (private) — two physically-separate files, one per tier. The owner-tier handler reads both and merges; the public-tier handler reads only the public file. This mirrors the `_private/` pattern used for matches and keeps the defense-in-depth posture: a private record never sits in a file the public-tier handler is allowed to open.

```json
{
  "provider": "pff",
  "players": [
    {
      "id": "8022",
      "firstName": "Jurrien",
      "lastName": "Timber",
      "nickname": "Jurrien Timber",
      "dob": "2001-06-17",
      "height": 182.0,
      "positionGroupType": "D",
      "visibility": "private",
      "source": {
        "name": "PFF FC",
        "url": "https://www.pff.com/",
        "licence": "Restricted; redistribution not permitted pending licence clarification"
      }
    }
  ]
}
```

Field names mirror the source provider's conventions for pass-through fidelity (`firstName`, `lastName`, `positionGroupType` here are PFF's). Cross-provider normalisation, if ever wanted, belongs in the Lakehouse adapter, not in this API.

### 6.4 Lambda handlers

Two new handlers, both small and following the same shape as existing ones:

| Handler | Input | Logic | Response |
|---------|-------|-------|----------|
| `list_players` | `{provider}` | Read `{provider}/players.json` (and `_private/players.json` for owner tier), merge, return | 200 + JSON |
| `get_player` | `{provider}`, `{id}` | Same read, find by `id`, return single record. 404 if not found OR if found-but-private-and-public-tier | 200 + JSON or 404 |

Both reuse `validate_token`, `validate_path_param`, the S3 client, and the json response builder from `shared.py`. Lambda memory/timeout/concurrency match the existing handlers. Note that unlike `get_artifact`, `get_player` returns the JSON record directly (no presigned URL) — player records are small structured data, not large binary artifacts.

### 6.5 Upload tooling

New CLI: `pining-upload-players`

```bash
pining-upload-players players.csv \
  --provider pff \
  --bucket karstenskyt-pining-for-the-data \
  --visibility private \
  --source-name "PFF FC" \
  --source-url "https://www.pff.com/" \
  --source-licence "Restricted; redistribution not permitted pending licence clarification"
```

Behaviour:
1. Read the input CSV (or JSON, autodetected by extension).
2. Validate that every row has at minimum `id` and one of `nickname` or (`firstName` + `lastName`).
3. Read-modify-write `{provider}/players.json` (public) or `{provider}/_private/players.json` (private). New player records are appended; re-uploads update existing records by `id`.
4. Tier mixing for a single player ID is rejected: if `id=X visibility=public` exists in `players.json` and a re-upload tries `--visibility private` for the same `X`, fail. Re-tiering requires an explicit move (out of scope for v1).

For PFF specifically, the source `players.csv` columns (`dob`, `firstName`, `height`, `id`, `lastName`, `nickname`, `positionGroupType`) map directly to the JSON shape above; no schema translation is needed.

### 6.6 What we do NOT do

- **No `/competitions` endpoint for v1.** PFF's `competitions.csv` is essentially a directory of match IDs by tournament — that's already reachable via `GET /v1/pff/matches` (which can be extended with a `?competition_id=` filter when needed). Republishing it as a separate resource would duplicate the navigation already provided by `/matches`.
- **No `/teams` endpoint for v1.** Per-match `metadata.json` already carries the team object (`{id, name, shortName, kit}`). There's no team-level data not already on matches. Easy to add later if a future provider ships team-level reference data not derivable from matches.
- **No generic `/files` escape hatch.** If a future provider ships something that genuinely doesn't fit a noun (a PDF schema, a binary calibration file), we'll add a noun for it then. Designing a generic escape hatch up front incentivises lazy modelling.

## 7. Audit logging

CloudTrail data events on the data bucket. Enabled at deploy time, not deferred — the licence ambiguity around the initial PFF load makes a defensible access trail valuable from day one.

### 7.1 Components

A new Terraform module `terraform/modules/audit/`:

- A second S3 bucket `karstenskyt-pining-for-the-data-audit` for log storage. Block all public access, SSE-KMS, versioning enabled, lifecycle policy expiring objects after 365 days (configurable variable).
- An organisation-independent CloudTrail trail capturing S3 data events scoped to *only* the data bucket ARN. Management events disabled — they're noise for this use case.
- IAM policy on the audit bucket allowing `cloudtrail.amazonaws.com` to `PutObject` with `bucket-owner-full-control`.

### 7.2 What gets logged

Per S3 GET/PUT/DELETE on the data bucket: timestamp, source IP, user agent, IAM principal (Lambda role for API-initiated reads; the requesting AWS identity for direct uploads), bucket name, object key, request parameters, response code.

Critically, presigned-URL access is captured: the GET that follows a 302 from `get_artifact` happens directly between the requester and S3, and CloudTrail records it with the requester's source IP and user agent. That's exactly what's needed to demonstrate (or refute) "did this artifact get downloaded, and by whom?" later.

### 7.3 Cost

CloudTrail data events: $0.10 per 100,000 events. Estimated annual events for our scale: low single-digit thousands of S3 reads, plus the bookkeeping reads from Lambda (matches.json, players.json) which can be excluded via the trail's advanced event selectors. Realistic annual cost: well under $1.

S3 storage for logs: gzipped JSON, ~2 KB per event. At the projected event count, far under 100 MB/year, effectively free under standard storage pricing.

### 7.4 Read access to logs

Owner-only via direct AWS Console / CLI against the audit bucket. No API endpoint to expose audit events publicly. If a consumer needs aggregated stats later, that's a derived view (separate skill, separate scope) — the raw events stay locked down.

### 7.5 Reserved naming and exclusions

- The audit bucket name is fixed by the storage module; no naming collision risk with the data bucket since a `-audit` suffix is appended.
- The trail's event selectors exclude reads of `matches.json` and `players.json` (the per-request bookkeeping objects) to keep the log clean and the cost negligible. Writes to those objects are still captured because they correspond to upload events.

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

- Reject any `{provider}`, `{id}`, `{artifact}`, or `{name}` value beginning with `_` (the leading underscore is reserved for tier and namespace markers).
- Reject mixing tiers within a single match: if `matches.json` has `id=X visibility=public` and a re-upload tries `--visibility private --game-id X`, fail with a clear error. Re-tiering a match requires an explicit move (out of scope for v1).

### 8.3 PFF-specific upload helper

For the initial PFF World Cup 2022 load, the source layout is:

```
FIFA World Cup 2022/
├── Event Data/{id}.json
├── Metadata/{id}.json
├── Rosters/{id}.json
├── Tracking Data/{id}.jsonl.bz2
├── competitions.csv          # not uploaded — directory data covered by /matches
├── players.csv               # tournament-level → pining-upload-players
└── PFF FC Change Log.docx    # not uploaded
```

A small adapter script (`scripts/upload_pff_wc2022.py`) is the right shape for this. Provider-specific reshaping doesn't belong in the public package surface, and `scripts/` is the conventional home for ops one-shots that should be checked in for repeatability without being shipped to consumers.

The script:

1. Iterates the 67 matches. For each, reshapes the source layout into a per-match staging directory:
   ```
   staging/{id}/
   ├── metadata.json
   ├── events.json
   ├── roster.json
   └── tracking.jsonl.bz2
   ```
2. Reads `Metadata/{id}.json` to extract `date`, `home.name`, `away.name`.
3. Invokes `pining-upload --provider pff --game-id {id} --visibility private --provenance original --source-name "PFF FC" --source-url "https://www.pff.com/" --source-licence "Restricted; redistribution not permitted pending licence clarification"` with those values.
4. After all matches: invokes `pining-upload-players players.csv --provider pff --visibility private` with the same source metadata.

`competitions.csv` is intentionally not uploaded. It is a tournament-level directory of match IDs, which is fully reachable via `GET /v1/pff/matches` once all 67 matches are loaded. Republishing it as a separate resource would duplicate navigation that the matches endpoint already provides.

The script is idempotent: re-running re-uploads everything but produces no duplicate index entries. Useful both for the initial load and for re-runs after correcting any per-match metadata.

### 8.4 Source data hygiene

Source metadata may include provider-platform URLs requiring source-side authentication. These are preserved in the uploaded `metadata.json` as-is — the API is a faithful pass-through, not a transformation layer. The Lakehouse adapter is responsible for deciding whether to dereference such URLs. If a future audit requires scrubbing fields, that transformation belongs on the adapter side, not at upload.

## 9. Lakehouse adapter contract

The Lakehouse will receive a single `PINING_API_TOKEN` env var (owner token) and call the API as today. Contract changes:

- New `visibility` field in `matches.json` responses. The adapter can ignore it for read-only consumption; it's there if the adapter ever wants to gate its own behaviour by visibility (e.g., "only export public-tier data to public dashboards").
- New `/players` endpoints. The adapter gains methods to enumerate and fetch player reference data, in the same shape as its existing match-fetching code.

The expected adapter pattern for player data: fetch `/v1/{provider}/players` once per session, cache locally, join player IDs from match rosters/events to the cached catalogue. Individual `/players/{id}` exists for sparse-access cases but is not the primary access pattern.

No client-side knowledge of which providers are public vs private. The API is the single source of truth.

## 10. Migration

Existing data is unaffected. Concretely:

1. Provision the SSM parameter for the owner token out-of-band before deployment.
2. Deploy the updated stack: storage module unchanged, audit module new, functions module gains `list_players` and `get_player` plus the SSM IAM grant, api module gains the `/players` routes.
3. `pining-upload` existing entries continue to work — they have no `visibility` field, are treated as public, served to both tiers.
4. Backfill `visibility: "public"` into existing `matches.json` entries opportunistically (next time each is re-uploaded for any reason). No urgency — the missing-means-public default is forward-compatible.
5. Verify the deployed stack with one PFF match before bulk-loading the rest.
6. Run `scripts/upload_pff_wc2022.py` to load the 67 matches plus the player catalogue.

No data movement of existing public matches. No breaking change to any existing API response (every new field is additive, every new endpoint is on a new path).

## 11. Future extensions

### 11.1 Per-token scopes
If a second private consumer is ever onboarded with access to a subset of private content, the two-tier model needs to grow into per-token scope lists. Today's two-tier design is a strict subset of that future model — migration is straightforward (owner becomes a token with `["*"]` scope).

### 11.2 Automated token rotation
Manual SSM rotation is sufficient for a single-user setup. If the operational pattern ever changes (multiple consumers, scheduled rotation policy), swap the SSM parameter for a Secrets Manager secret and add a rotation Lambda. The application-side change is two lines of boto3.

### 11.3 PFF licence clarification
Licence clarification has been requested from the source; awaiting response. Three outcomes change the design posture:

- **Confirmed open / formal permissive licence**: re-upload PFF content as public-tier; the provider stays one logical entity; no API change beyond `pining-upload --visibility public` for the same content (or a small helper to flip visibility on existing entries).
- **Confirmed restricted**: keep current design; record the licence text exactly as given.
- **No response**: stay private indefinitely. Re-evaluate after a reasonable window.

## 12. Test plan

Unit (in `src/tests/test_lambda_handlers.py`):

- `validate_token` returns `Tier.PUBLIC` for the public token, `Tier.OWNER` for the owner token, error response for any other input.
- `list_matches` returns only public entries to the public tier; returns all entries to the owner tier; returns 200 with empty list (not 404) when filtering removes everything.
- `list_players` mirrors the same tier-filter semantics: public tier sees only `players.json`; owner tier sees the merge of `players.json` and `_private/players.json`.
- `get_artifact` returns 404 (not 403) for `visibility=private` matches accessed with the public tier; returns 302 for the same match accessed with the owner tier.
- `get_artifact` resolves the correct S3 prefix (`_private/` vs not) based on the match's recorded visibility.
- `get_player` returns 404 (not 403) for private player IDs accessed with the public tier; returns 200 + JSON for the same player with the owner tier.
- Path-param validator rejects any value starting with `_`.

Integration:

- End-to-end smoke test against the deployed dev stack with both tokens, asserting tier-correct responses on 1 public + 1 private match, and on `/players` listings/lookups for both tiers.
- Negative test: attempt `pining-upload --provider _private`, `pining-upload --game-id _private`, and `pining-upload-players --provider _private` — all must fail before any S3 calls.

Manual:

- Upload one PFF match (`3812`) end-to-end before bulk-loading the 67. Verify the Lakehouse adapter pulls it cleanly with the owner token and that the public token gets a 404.
- Verify a CloudTrail data event lands in the audit bucket within a few minutes of an artifact fetch.

## 13. Resolved design questions

- **Owner-token storage** → SSM Parameter Store SecureString. Maintenance is identical to Secrets Manager for manual rotation; Secrets Manager only differentiates if you adopt automatic rotation, which adds a rotation Lambda to maintain for no security uplift in this setup. The swap, if ever needed, is mechanical.
- **Tournament-level reference data** → in scope for v1, modelled as first-class resource nouns rather than as opaque files. `/players` is the v1 noun, sourced from PFF's `players.csv`. `competitions.csv` is dropped since it duplicates the navigation already provided by `/matches`. `/teams` is deferred — current per-match metadata is sufficient. Section 6.
- **CloudTrail audit logging** → enabled at deploy time. Section 7. Cost is well under $1/year and the trail is the defensible record if PFF (or any future restricted-content owner) asks "who downloaded what, when?"
- **PFF upload helper location** → `scripts/upload_pff_wc2022.py`. Provider-specific reshaping doesn't belong in the public package surface; `scripts/` is the conventional home for repeatable ops one-shots.
