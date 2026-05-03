# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Two-tier auth on the mock provider API: PUBLIC tier (existing `api_token`) and OWNER tier (SSM Parameter Store SecureString). `validate_token` returns a `Tier` enum; tier mismatch returns uniform `404` to avoid existence leaks (no 403). Duplicate-token misconfiguration classifies as PUBLIC (fail closed).
- Match-level visibility flag (`public` / `private`) with reserved `_private/` S3 prefix for tier separation (defense in depth alongside the application-layer tier check).
- New `/v1/{provider}/players` and `/v1/{provider}/players/{id}` reference resource endpoints, with provider-gated 404 and private-wins precedence on cross-tier ID collision.
- Canonical Pydantic models (`MatchEntry`, `PlayerRecord`) in `terraform/modules/functions/src/shared.py` as the schema source of truth; published JSON Schemas in `schemas/` (URN `$id`, Draft 2020-12 `$schema`); drift-tested in CI via `scripts/regenerate_schemas.py`.
- `pining-upload --visibility public|private` flag with cross-tier mixing rejection.
- `pining-upload-players` CLI accepting canonical JSON only (CSV explicitly rejected with reference to `scripts/upload_pff_wc2022.py` adapter).
- `updated_at` ISO 8601 UTC timestamp on every match and player entry, refreshed on every write (drives consumer incremental refresh).
- Object-form `artifacts: {name: filename}` in `matches.json` (replaces array form). `get_artifact` resolves filenames via dict lookup with no per-request `list_objects_v2`; the keys form the API's whitelist.
- CloudTrail data events on the data bucket (`terraform/modules/audit/`), landing in a separate audit bucket with 365-day retention and SSE-KMS. Only `/providers.json` reads are excluded from the trail; `/matches.json` and `/players.json` reads stay logged.
- `LAST_ROTATION` env var on all 5 Lambdas — bumped via `terraform apply -var=last_rotation=...` to invalidate the warm-container `_get_owner_token` cache during a rotation.
- `scripts/upload_pff_wc2022.py` — orchestrator for bulk-loading PFF FIFA World Cup 2022 as private-tier data (no licence gate; single-owner private-tier load is data movement within the operator's own systems).
- `scripts/verify_pff_load.py` — automated post-load verification (counts, visibility leak checks, content-agnostic spot-check sampling).
- ADRs 0001-0005 covering owner-token storage, `_private/` convention, resource-noun endpoints, CloudTrail audit, and single-bucket multi-tier prefix isolation.
- British `--source-licence` is the canonical CLI flag spelling on both upload CLIs; American `--source-license` accepted as a quiet alias.

### Changed
- Documentation: README, ARCHITECTURE.md, CLAUDE.md, and docs/api-reference.md updated to reflect the new two-tier auth, `/players` resource, audit logging, and infrastructure additions.
- Test count: 64 → 125.
- C4 architecture diagram regenerated to include the new Lambdas, audit module, SSM Parameter Store, and KMS.

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

[Unreleased]: https://github.com/karsten-s-nielsen/pining-for-the-data/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/karsten-s-nielsen/pining-for-the-data/releases/tag/v0.1.0
