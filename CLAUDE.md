# pining-for-the-data

Open soccer tracking data — redistribution, validation, and mock provider API.
Companion repo to luxury-lakehouse.

## Architecture

- `src/deidentify/` — name pools, roster generation, two-layer jersey→identity mapping
- `src/formats/` — provider format readers/writers (SkillCorner V3 JSON/JSONL, SkillCorner multi-artifact bundle + raw-JSON family for restricted owner-tier data, IDSSE/Sportec DFL XML, StatsBomb commercial 360 club bundle + open-data tournaments family (`statsbomb_open.py`) for restricted owner-tier data, Respo.Vision JSON future). Owner-tier SkillCorner is stored as the canonical columnar Parquet/zstd set (nested `tracking.parquet`, `events`/`physical` Parquet, freeze dropped, per-match `format_version` marker — ADR 0011; pure transforms in `skillcorner_canonical.py`). The two StatsBomb source families share one faithful-feed gzip-JSON shape (ADR 0010) and one artifact vocabulary; the open family (`statsbomb_open.py`) is the purest ADR-0010 case (the real published feed — no de-pivot/join/resolution) and is served `provenance="redistributed"` (ADR 0012)
- `src/publish/` — HuggingFace Hub dataset publishing
- `src/mock_api/` — Upload CLIs (pining-upload, pining-upload-players)
- `src/tests/` — pytest test suite
- `schemas/` — Published JSON Schemas for `matches.json` and `players.json` (generated from Pydantic models in `src/canonical/models.py`; drift-tested in CI; models kept out of the Lambda zip so the runtime stays pydantic-free)
- `src/canonical/` — Canonical Pydantic models (`MatchEntry`, `PlayerRecord`); imported by upload CLIs + schema regenerator + tests
- `scripts/` — One-shot ops scripts, grouped by role:
  - Per-provider load + post-load verify pairs: `upload_gradient_wc2022.py` / `verify_gradient_load.py`, `upload_idsse_bundesliga.py` / `verify_idsse_load.py`. StatsBomb has two source-family loaders sharing one verify: `upload_statsbomb_club.py` (commercial 360, `provenance="original"`) and `upload_statsbomb_open.py` (open-data tournaments, `provenance="redistributed"`, ADR 0012; fetch+cache the real feed, coherence pre-flight, same-tier skip-and-report players) / `verify_statsbomb_load.py`
  - Owner-tier SkillCorner canonical ingest (Parquet/zstd, ADR 0011) — two source-family adapters sharing one verify: `upload_skillcorner_raw.py` (raw-JSON family: Champions League, Premier League 25/26) and `upload_skillcorner_parquet.py` (parquet-processed family: Real Madrid, Premier League 24/25), both `format_version=2` and skipping defective matches (missing a required role, or zero-byte tracking) up front via `partition_ingestible`; `verify_skillcorner_canonical_load.py` (canonical post-load HTTP verify). The legacy v0.3.0 RM loader `upload_skillcorner_realmadrid.py` / `verify_skillcorner_realmadrid_load.py` is retained (RM was migrated in place to the canonical set)
  - Shared: `_verify_http.py` (HTTP helpers the verify scripts import), `regenerate_schemas.py` (drift-tested in CI)
  - Completed one-shot migrations against live S3 state, retained as the audit trail for changes already applied (each is idempotent and safe to re-run): `backfill_skillcorner_artifacts.py` (legacy array-form artifacts → canonical object form), `migrate_skillcorner_tracking_parquet.py` (owner-tier tracking → nested Parquet, events/physical → zstd, freeze dropped — ADR 0011; stored-object-gated), `migrate_pff_to_gradientsports.py` and `migrate_gradientsports_slug.py` (the two provider-slug renames)
  - Data: `idsse_figshare_manifest.json` (committed md5 manifest for the pinned figshare release)
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

Owner-tier providers: `gradientsports` (Gradient Sports), `skillcorner` private tier
(restricted Real Madrid), `statsbomb` (commercial 360 club delivery, `provenance="original"`
— ADR 0010; plus open-data tournaments, `provenance="redistributed"` — ADR 0012). Both
StatsBomb source families are owner-tier only; `provenance` distinguishes them.

`validate_token` (in `terraform/modules/functions/src/shared.py`) returns a `Tier` enum (`PUBLIC` or `OWNER`); handlers filter responses by tier. Tier mismatch returns uniform `404` (not `403`) to avoid existence leaks. Duplicate-token misconfiguration classifies as `PUBLIC` (fail closed).

Rotation: bump `LAST_ROTATION` env var on all 6 Lambdas via `terraform apply -var=last_rotation=$(date -u +%Y%m%dT%H%M%SZ)` after `aws ssm put-parameter --overwrite`. No dual-validity in v1; consumers must implement 401 retry during the rotation window.

Full design: `docs/superpowers/specs/2026-05-02-private-data-tier.md`. Architectural decisions: `docs/decisions/`.

## Pre-commit quality gate

Always run the `final-review` skill before the final commit of any implementation plan or multi-file change. This is non-negotiable — the skill catches documentation drift, stale references, missing test updates, and consistency issues that are invisible during incremental work.
