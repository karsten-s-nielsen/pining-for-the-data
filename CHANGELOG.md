# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.3.0] - 2026-06-29

### Added
- Restricted SkillCorner Real Madrid tracking data ingested as **owner-tier** content under the existing `skillcorner` provider (`visibility="private"`, `provenance="original"`) — served only to the owner bearer token; public consumers see only the redistributed open data. No new provider slug and no Lambda/Terraform change (the API is already tier/provider-generic).
- `src/formats/skillcorner_bundle.py` — pure, stdlib-only reader for the SkillCorner multi-artifact bundle (Soccermatics-course distribution). Parses only the small `meta/*.json` for index metadata (`date` derived in `Europe/Madrid` local time) and derives the owner-tier player catalogue from the self-contained `meta.players` lists; the large tracking/events/freeze/physical bodies are never parsed.
- Role-aligned artifact keys `tracking` / `events` / `freeze_frames` / `metadata` / `physical` (ADR 0008); `freeze_frames` and `physical` registered in the shared role vocabulary. `tracking` JSON is gzip-compressed (streamed) before upload; the reproducible `velocities` artifact is excluded.
- `scripts/upload_skillcorner_realmadrid.py` — owner-tier worked adapter: stages each match's artifacts into a fresh temp dir, calls `pining-upload` at the private tier, then derives + uploads the player catalogue, **skipping (and reporting) any player id already present in the public tier** so a cross-tier collision never aborts the load. Source root read from `$SKILLCORNER_RESTRICTED_DIR`.
- `scripts/verify_skillcorner_realmadrid_load.py` — owner+public post-load verification: owner sees the restricted matches/artifacts/players, public gets a uniform `404` (no existence leak), large `tracking` validated via a `Range: bytes=0-0` GET. Identifiers sampled from the live response (no licensed ids committed).
- ADR 0009 — restricted data under an existing public provider (tier dimension over a new slug; `meta`-as-index-source over the mismatched `matches.parquet`; cross-tier player-collision skip-and-report).

### Changed
- Test count: 187 → 210 (+24 new unit tests; synthetic fixtures only).
- Documentation: README (owner-tier note), CLAUDE.md (new reader + scripts), and the ADR index updated.

## [0.2.0] - 2026-05-29

### Removed
- `get_artifact` legacy array-form fallback (and its 2 regression tests). All deployed `matches.json` entries are now in canonical object form, so the dead code path is gone.

### Migrated
- 10 SkillCorner matches in `skillcorner/matches.json` migrated from legacy array-form `artifacts: ["..."]` to canonical object-form `artifacts: {name: filename}`, `visibility: "public"` and `updated_at` added, `source.license` (American) renamed to `source.licence` (British, per spec §8.2.1). One-shot script `scripts/backfill_skillcorner_artifacts.py` is idempotent and uses S3 `IfMatch=<etag>` for optimistic concurrency control.

### Added
- IDSSE/Sportec open Bundesliga as a new **public** provider (`idsse`): 7 matches of raw DFL XML (matchinformation / events / positions at 25 fps), redistributed as-is under CC-BY 4.0 with DFL/Sportec authorization, served through the existing provider API. New `src/formats/idsse.py` reader parses only the ~12 KB matchinformation XML for index metadata (`date` derived in `Europe/Berlin` local time); positions/events are served byte-for-byte. `scripts/upload_idsse_bundesliga.py` fetches the version-pinned figshare release (`/versions/1`), verifies it against a committed md5 manifest (`scripts/idsse_figshare_manifest.json`, regenerable via `--write-manifest`), and bulk-loads via `pining-upload`. `scripts/verify_idsse_load.py` runs size-aware post-load checks (full GET for `metadata`, `Range: bytes=0-0` GET for the large `events`/`tracking`). Role-aligned artifact keys `metadata` / `events` / `tracking` (ADR 0008).
- ADR 0008 — role-aligned artifact-key vocabulary across providers (`metadata`/`events`/`tracking`; SkillCorner's id-prefixed keys documented as the legacy exception).
- Two-tier auth on the mock provider API: PUBLIC tier (existing `api_token`) and OWNER tier (SSM Parameter Store SecureString). `validate_token` returns a `Tier` enum; tier mismatch returns uniform `404` to avoid existence leaks (no 403). Duplicate-token misconfiguration classifies as PUBLIC (fail closed).
- Match-level visibility flag (`public` / `private`) with reserved `_private/` S3 prefix for tier separation (defense in depth alongside the application-layer tier check).
- New `/v1/{provider}/players` and `/v1/{provider}/players/{id}` reference resource endpoints, with provider-gated 404 and private-wins precedence on cross-tier ID collision.
- Canonical Pydantic models (`MatchEntry`, `PlayerRecord`) in `src/canonical/models.py` as the schema source of truth; published JSON Schemas in `schemas/` (URN `$id`, Draft 2020-12 `$schema`); drift-tested in CI via `scripts/regenerate_schemas.py`. Models live outside the Lambda source dir so the Lambda zip stays dependency-free (no pydantic at runtime).
- `pining-upload --visibility public|private` flag with cross-tier mixing rejection.
- `pining-upload-players` CLI accepting canonical JSON only (CSV explicitly rejected with reference to `scripts/upload_gradient_wc2022.py` adapter).
- `updated_at` ISO 8601 UTC timestamp on every match and player entry, refreshed on every write (drives consumer incremental refresh).
- Object-form `artifacts: {name: filename}` in `matches.json` (replaces array form). `get_artifact` resolves filenames via dict lookup with no per-request `list_objects_v2`; the keys form the API's allowlist.
- CloudTrail data events on the data bucket (`terraform/modules/audit/`), landing in a separate audit bucket with 365-day retention and SSE-KMS. Only `/providers.json` reads are excluded from the trail; `/matches.json` and `/players.json` reads stay logged.
- `LAST_ROTATION` env var on all 6 Lambdas — bumped via `terraform apply -var=last_rotation=...` to invalidate the warm-container `_get_owner_token` cache during a rotation.
- `scripts/upload_gradient_wc2022.py` — orchestrator for bulk-loading Gradient Sports FIFA World Cup 2022 as private-tier data (no licence gate; single-owner private-tier load is data movement within the operator's own systems).
- `scripts/verify_gradient_load.py` — automated post-load verification (counts, visibility leak checks, content-agnostic spot-check sampling).
- ADRs 0001-0006 covering owner-token storage, `_private/` convention, resource-noun endpoints, CloudTrail audit, single-bucket multi-tier prefix isolation, and canonical-models-outside-Lambda placement.
- British `--source-licence` is the canonical CLI flag spelling on both upload CLIs; American `--source-license` accepted as a quiet alias.

### Changed
- Documentation: README, ARCHITECTURE.md, CLAUDE.md, and docs/api-reference.md updated to reflect the new two-tier auth, `/players` resource, audit logging, and infrastructure additions.
- Test count: 64 → 187.
- C4 architecture diagram regenerated to include the new Lambdas, audit module, SSM Parameter Store, KMS, the `canonical/` package, and the IDSSE provider (orchestrator + verify script + external source).

### Fixed (deploy-time hardening)
- `get_artifact` accepts both legacy array-form `artifacts: [...]` and current object-form `artifacts: {name: filename}`. Legacy entries (uploaded pre-Task-8) fall back to per-request S3 list; object-form entries skip listing entirely. Two regression tests cover the legacy path so it doesn't bit-rot.
- `reserved_concurrent_executions` removed from all 5 pre-existing Lambdas (was = 5); the health Lambda was created without it. Account limit is 10 ConcurrentExecutions with 10-min unreserved required; reserving any concurrency on this account fails the API call. Existing 3 Lambdas had no reservation in production despite TF claiming = 5 (silent drift); new declaration brings TF state into alignment with reality.
- CloudTrail `advanced_event_selector` field selectors merged: CloudTrail rejects two `field_selector` blocks for the same field. Combined `starts_with` + `not_ends_with` on `resources.ARN` into a single block.
- `verify_gradient_load.py` corrected to FIFA WC 2022 actual counts: 64 matches (not 67 — standard tournament size) and 829 unique player IDs (Gradient Sports CSV has ~2321 (player, team) rows that dedupe by `id`). The script's `_follow_redirect` now strips the `Authorization: Bearer` header on the 302 follow to S3 — S3 rejects presigned URLs with a stray bearer as conflicting auth.
- Docs (CHANGELOG, CLAUDE.md, ARCHITECTURE.md, api-reference, spec §6.6) updated to reference `src/canonical/models.py` as the canonical model location.

### Operational milestones (this branch)
- Dev stack deployed end-to-end: SSM owner token set, audit module + CloudTrail provisioned, all 6 Lambdas live (5 data-tier with both tokens + unauthenticated health).
- Gradient Sports FIFA WC 2022 successfully bulk-loaded into the private tier: 64 matches (256 artifact files) + 829 unique player records. `scripts/verify_gradient_load.py` post-conditions all pass: counts correct, zero visibility leaks, 20/20 artifact downloads via presigned URLs, 5/5 player spot-checks, public-tier 404 on private artifacts.
- IDSSE Bundesliga bulk-loaded into the **public** tier on dev: 7 matches (21 artifacts, ~2.63 GB). `scripts/verify_idsse_load.py` post-conditions all pass: 7 matches, `/providers` includes `idsse`, `dateFrom`/`dateTo` filter parity, and size-aware artifact checks (`metadata` 200 + body; `events`/`tracking` 206 + positive `Content-Range` total without downloading the 418 MB positions XML).

## [0.1.0] - 2026-03-20

### Added
- SkillCorner V3 format reader and writer (`pining-ingest` CLI)
- Automated HuggingFace Hub publishing (`pining-publish` CLI)
- De-identification engine with synthetic roster generation (`pining-generate-roster` CLI)
- Mock provider REST API on AWS (S3 + API Gateway + Lambda)
- Upload CLI for mock API data management (`pining-upload` CLI)
- Terraform modules for full infrastructure deployment
- 10 A-League Men matches redistributed in SkillCorner V3 format
- ARCHITECTURE.md with C4 diagrams
- CI pipeline (ruff, pyright, pytest) via GitHub Actions

[Unreleased]: https://github.com/karsten-s-nielsen/pining-for-the-data/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/karsten-s-nielsen/pining-for-the-data/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/karsten-s-nielsen/pining-for-the-data/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/karsten-s-nielsen/pining-for-the-data/releases/tag/v0.1.0
