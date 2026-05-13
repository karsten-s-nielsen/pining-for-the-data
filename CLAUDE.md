# pining-for-the-data

Open soccer tracking data — redistribution, validation, and mock provider API.
Companion repo to luxury-lakehouse.

## Architecture

- `src/deidentify/` — name pools, roster generation, two-layer jersey→identity mapping
- `src/formats/` — provider format readers/writers (SkillCorner V3 JSON/JSONL, Respo.Vision JSON future)
- `src/publish/` — HuggingFace Hub dataset publishing
- `src/mock_api/` — Upload CLIs (pining-upload, pining-upload-players)
- `src/tests/` — pytest test suite
- `schemas/` — Published JSON Schemas for `matches.json` and `players.json` (generated from Pydantic models in `src/canonical/models.py`; drift-tested in CI; models kept out of the Lambda zip so the runtime stays pydantic-free)
- `src/canonical/` — Canonical Pydantic models (`MatchEntry`, `PlayerRecord`); imported by upload CLIs + schema regenerator + tests
- `scripts/` — One-shot ops scripts (regenerate_schemas.py, upload_gradient_wc2022.py, verify_gradient_load.py)
- `name_pools/` — JSON name lists (fictional first/last names, cities)
- `rosters/` — generated de-identified roster JSONs per game
- `terraform/` — AWS infrastructure (S3 + API Gateway + Lambda + SSM + KMS + CloudTrail)
- `terraform/modules/functions/src/` — Lambda handlers + shared utilities (auth, response builders, query filters)
- `terraform/modules/audit/` — Audit module (CloudTrail data events on data bucket)
- `terraform/modules/observability/` — CloudWatch alarms, SNS topic, dashboard (ADR 0007)
- `docs/decisions/` — Architecture Decision Records
- `docs/superpowers/{specs,plans}/` — Brainstormed specs and implementation plans

## Conventions

- Python 3.12+, hatch build system
- Ruff for linting/formatting (line-length 120)
- Pyright for type checking (basic mode)
- pytest for testing (src/tests/)
- Pre-commit hooks: ruff, yaml checks, secret scanning
- No tracking data files in the repo — too large for git
- MIT license (code + redistributed SkillCorner data), CC-BY-4.0 (dataset card on HF Hub)

## De-identification (for future private data)

SkillCorner open data is redistributed as-is (MIT license) — no de-identification applied.
The de-identification engine is retained for future use with private/commercial tracking data:

- Home team: Wakanda FC
- 4 featured names: Fezzik Took, Tormund Tully, Westley Montoya, T'Challa Stark
- Remaining players: randomly generated from name pools (GOT, LOTR, BB/BCS, Princess Bride, EEAAO, Ghibli, Elf)
- Opponent teams: 20 pre-generated fictional names (Asgard Athletic, Krypton City, etc.)
- Two-layer mapping: stable synthetic identity (Layer 1) → per-game jersey mapping (Layer 2)

## CLI Entry Points

- `pining-generate-roster` — generate a synthetic roster for a game
- `pining-ingest` — validate SkillCorner V3 match JSON + tracking JSONL
- `pining-publish` — push Parquet to HuggingFace Hub
- `pining-upload` — upload game artifacts to S3 and update provider indexes (supports `--visibility public|private`)
- `pining-upload-players` — upload provider-level player reference catalogue (canonical JSON only)

## Mock Provider API: two-tier auth

The mock API serves two visibility tiers:

- **Public tier**: documented `api_token` in `terraform.tfvars`. Serves redistributed open data (e.g., SkillCorner).
- **Owner tier**: bearer token stored in SSM Parameter Store SecureString (`/pining-for-the-data/api_token_owner`). Serves restricted private-tier content (e.g., Gradient Sports). Set out-of-band via `aws ssm put-parameter`; never committed.

`validate_token` (in `terraform/modules/functions/src/shared.py`) returns a `Tier` enum (`PUBLIC` or `OWNER`); handlers filter responses by tier. Tier mismatch returns uniform `404` (not `403`) to avoid existence leaks. Duplicate-token misconfiguration classifies as `PUBLIC` (fail closed).

Rotation: bump `LAST_ROTATION` env var on all 6 Lambdas via `terraform apply -var=last_rotation=$(date -u +%Y%m%dT%H%M%SZ)` after `aws ssm put-parameter --overwrite`. No dual-validity in v1; consumers must implement 401 retry during the rotation window.

Full design: `docs/superpowers/specs/2026-05-02-private-data-tier.md`. Architectural decisions: `docs/decisions/`.
