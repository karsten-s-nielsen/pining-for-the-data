# Documentation Audit Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 22 findings from the documentation audit report (`PINING-DOC-AUDIT-1_12_0.md`).

**Architecture:** Seven independent tasks grouped by file proximity — each task touches a distinct set of files with no cross-task conflicts. All tasks can run in parallel. No code logic changes; only documentation, metadata, and docstring edits.

**Tech Stack:** Markdown, Python docstrings, TOML metadata. Verification via `uv run ruff check src/`, `uv run pyright src/`, `uv run pytest`.

---

## File Map

| Action | File | Task | What Changes |
|--------|------|------|-------------|
| Modify | `pyproject.toml` | 1 | Fix stale description |
| Modify | `ARCHITECTURE.md` | 1 | Remove "(Planned)" header, fix passive voice, remove manual date stamp |
| Modify | `docs/specs/2026-03-19-mock-provider-api-design.md` | 2 | Replace all Metrica references with SkillCorner |
| Modify | `Dockerfile` | 2 | Remove Metrica comment line |
| Modify | `README.md` | 3 | Badges, prerequisites, objective, passive voice, CLI coverage, domain terms, section labels, error recovery |
| Modify | `terraform/docs/setup.md` | 4 | Fix "Just" trivializing language, add troubleshooting section |
| Create | `CHANGELOG.md` | 5 | Retroactive change history from git log |
| Create | `SECURITY.md` | 5 | Vulnerability disclosure policy |
| Modify | `terraform/modules/functions/src/get_artifact.py` | 6 | Add handler docstring |
| Modify | `terraform/modules/functions/src/list_matches.py` | 6 | Add handler docstring |
| Modify | `terraform/modules/functions/src/list_providers.py` | 6 | Add handler docstring |
| Modify | `src/deidentify/roster_generator.py` | 6 | Add dataclass docstrings (Player, TeamRoster, MatchMetadata, GameRoster) + main docstring for skillcorner.py |
| Modify | `src/formats/skillcorner.py` | 6 | Add main() docstring |
| Create | `docs/api-reference.md` | 7 | Standalone API reference (Diataxis Reference quadrant) |
| Create | `docs/tutorial.md` | 7 | Guided tutorial using test fixtures (Diataxis Tutorial quadrant) |
| Create | `docs/decisions/README.md` | 7 | Stub explaining ADR intent |

---

## Task 1: Fix Stale Metadata & Labels

**Findings:** F3, F4, F11 (ARCHITECTURE.md portion)
**Files:**
- Modify: `pyproject.toml:4`
- Modify: `ARCHITECTURE.md:3-4,11,113`

- [ ] **Step 1: Fix pyproject.toml description**

In `pyproject.toml`, replace the `description` field:

```toml
# OLD
description = "De-identified youth soccer tracking data — open dataset and mock provider API"

# NEW
description = "Open soccer tracking data — redistribution, validation, and mock provider API"
```

- [ ] **Step 2: Fix ARCHITECTURE.md stale header**

In `ARCHITECTURE.md`, replace:

```markdown
## 4. Infrastructure (Planned)
```

with:

```markdown
## 4. Infrastructure
```

- [ ] **Step 3: Remove manual date stamp from ARCHITECTURE.md**

Replace:

```markdown
> **Status**: SkillCorner V3 format + Mock Provider API implemented.
> **Last Updated**: 2026-03-20
> **Repository**: [`karstenskyt/pining-for-the-data`](https://github.com/karstenskyt/pining-for-the-data)
```

with:

```markdown
> **Status**: SkillCorner V3 format + Mock Provider API implemented.
> **Repository**: [`karstenskyt/pining-for-the-data`](https://github.com/karstenskyt/pining-for-the-data)
```

- [ ] **Step 4: Fix passive voice in ARCHITECTURE.md**

Replace:

```markdown
a de-identification engine is included for future use with private/commercial data.
```

with:

```markdown
the project includes a de-identification engine for future use with private/commercial data.
```

- [ ] **Step 5: Verify**

Run: `uv run pytest -q`
Expected: All 64 tests pass (metadata changes don't affect code).

---

## Task 2: Metrica Cleanup

**Findings:** F5, F6
**Files:**
- Modify: `docs/specs/2026-03-19-mock-provider-api-design.md`
- Modify: `Dockerfile:11`

- [ ] **Step 1: Update provider list in spec**

In `docs/specs/2026-03-19-mock-provider-api-design.md`, replace line 25:

```markdown
`{provider}`: `metrica`, `respovision` (extensible)
```

with:

```markdown
`{provider}`: `skillcorner`, `respovision` (extensible)
```

- [ ] **Step 2: Replace Metrica Artifacts section with SkillCorner**

Replace the entire section 2.2 (lines 30-37):

```markdown
### 2.2 Metrica Artifacts (initial)

| Artifact | File | Content |
|----------|------|---------|
| `tracking` | `tracking.txt` | EPTS raw tracking data (colon-delimited, 0-1 normalized) |
| `metadata` | `metadata.xml` | FIFA EPTS metadata XML (players, teams, half boundaries) |
| `events` | `events.xml` | Tactical patterns / event annotations |
| `roster` | `roster.json` | De-identified roster (pining-for-the-data generated, not provider artifact) |
```

with:

```markdown
### 2.2 SkillCorner Artifacts (implemented)

| Artifact | File | Content |
|----------|------|---------|
| `match` | `match.json` | Match metadata (teams, players, competition, pitch dimensions, periods) |
| `tracking` | `tracking.jsonl` | Tracking data at 10fps (one JSON object per frame — ball + player positions) |
```

- [ ] **Step 3: Update discovery response example**

Replace the provider list example (line 54):

```json
"providers": ["metrica", "respovision"]
```

with:

```json
"providers": ["skillcorner", "respovision"]
```

Replace the match listing example (lines 57-69):

```json
// GET /v1/metrica/matches
{
  "provider": "metrica",
  "matches": [
    {
      "id": "game_03",
      "date": "2026-01-03",
      "home": "Wakanda FC",
      "away": "Shire Town",
      "artifacts": ["tracking", "metadata", "events", "roster"]
    }
  ]
}
```

with:

```json
// GET /v1/skillcorner/matches
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

- [ ] **Step 4: Update S3 layout example**

Replace the S3 layout block (lines 83-101) to show `skillcorner/` instead of `metrica/`:

```
pining-for-the-data-{account_id}/
├── providers.json                    # ["skillcorner", "respovision"]
├── skillcorner/
│   ├── matches.json                  # discovery index
│   ├── game_03/
│   │   ├── match.json
│   │   └── tracking.jsonl
│   └── game_04/
│       └── ...
└── respovision/
    ├── matches.json
    └── game_03/
        ├── tracking.json
        ├── summary.json
        └── roster.json
```

- [ ] **Step 5: Update CLI example in spec**

Replace lines 193-199:

```bash
# Upload all Metrica artifacts for a game
pining-upload game_03/ --provider metrica --game-id game_03

# Upload Respo.Vision artifacts (future)
pining-upload game_03/ --provider respovision --game-id game_03
```

with:

```bash
# Upload SkillCorner artifacts for a game
pining-upload game_03/ --provider skillcorner --game-id game_03

# Upload Respo.Vision artifacts (future)
pining-upload game_03/ --provider respovision --game-id game_03
```

- [ ] **Step 6: Update bootstrap guide reference in spec**

Replace line 219 (inside the bootstrap guide section):

```markdown
8. Upload data: `pining-upload output/game_03/ --provider metrica --game-id game_03`
```

with:

```markdown
8. Upload data: `pining-upload output/game_03/ --provider skillcorner --game-id game_03`
```

- [ ] **Step 7: Remove Metrica comment from Dockerfile**

Remove line 11 from `Dockerfile`:

```dockerfile
# - Metrica CSV de-identification (CPU-only, runs via uv directly)
```

- [ ] **Step 8: Clean stale pycache files**

Run:

```bash
rm -f src/formats/__pycache__/metrica.*.pyc
rm -f src/tests/__pycache__/test_metrica_format.*.pyc
```

---

## Task 3: README.md Overhaul

**Findings:** F1, F2, F7, F11 (README portions), F14, F15, F16
**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add CI and license badges below the title**

Insert after line 1 (`# Pining For The Data!`), before the Monty Python quote:

```markdown
[![CI](https://github.com/karstenskyt/pining-for-the-data/actions/workflows/python-ci.yml/badge.svg)](https://github.com/karstenskyt/pining-for-the-data/actions/workflows/python-ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
```

- [ ] **Step 2: Fix passive voice in "About The Project"**

Replace line 27:

```markdown
A de-identification engine is included for future use with private/commercial tracking data from providers like [Respo.Vision](https://respo.vision/).
```

with:

```markdown
The project includes a de-identification engine for future use with private/commercial tracking data from providers like [Respo.Vision](https://respo.vision/).
```

- [ ] **Step 3: Rename "Distribution" section for stronger information scent**

Replace:

```markdown
## Distribution
```

with:

```markdown
## How to Access the Data
```

- [ ] **Step 4: Add prerequisites and objective to Quick Start**

Replace lines 66-78:

```markdown
## Quick Start

### Installation

```bash
# Clone and install
git clone https://github.com/karstenskyt/pining-for-the-data.git
cd pining-for-the-data
uv sync --extra dev

# Run tests
uv run pytest
```
```

with:

```markdown
## Quick Start

After these steps you will have the project installed with all CLI tools available and the test suite passing.

### Prerequisites

- **Python 3.12+** &mdash; [download](https://www.python.org/downloads/)
- **uv** (Python package manager) &mdash; [install](https://docs.astral.sh/uv/getting-started/installation/)
- **git**

### Installation

```bash
# Clone and install
git clone https://github.com/karstenskyt/pining-for-the-data.git
cd pining-for-the-data
uv sync --extra dev

# Run tests — all 64 should pass
uv run pytest
```

> **Troubleshooting:** If `uv` is not found, install it first: `curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS/Linux) or `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` (Windows). Then restart your terminal.
```

- [ ] **Step 5: Add domain-term parentheticals**

In line 27 (the "About" section), replace:

```markdown
in SkillCorner V3 format (match JSON + tracking JSONL at 10fps),
```

with:

```markdown
in SkillCorner V3 format (match metadata as JSON + frame-by-frame tracking as [JSONL](https://jsonlines.org/) at 10 frames per second),
```

In line 39 (Key Features, Format Handlers bullet), replace:

```markdown
- **Format Handlers** &mdash; Validates and processes provider-specific tracking data (SkillCorner V3 JSON/JSONL). Respo.Vision 3D pose scaffolded for future use.
```

with:

```markdown
- **Format Handlers** &mdash; Validates and processes provider-specific tracking data (SkillCorner V3 JSON/JSONL). [JSONL](https://jsonlines.org/) = one JSON object per line, one line per frame. Respo.Vision 3D pose scaffolded for future use.
```

- [ ] **Step 6: Fix passive voice in de-identification section**

Replace line 106:

```markdown
SkillCorner open data is redistributed as-is — no de-identification is applied. The de-identification engine below is retained for future use with private/commercial tracking data.
```

with:

```markdown
SkillCorner open data ships as-is — no de-identification applied. The de-identification engine below exists for future use with private/commercial tracking data.
```

Replace line 122:

```markdown
Remaining players get randomly generated names from fictional name pools. Opponent teams are assigned from a pre-defined list of 20 fictional clubs (Asgard Athletic, Krypton City, Shire Town, etc.).
```

with:

```markdown
Remaining players get randomly generated names from fictional name pools. The tool assigns opponent teams from a pre-defined list of 20 fictional clubs (Asgard Athletic, Krypton City, Shire Town, etc.).
```

- [ ] **Step 7: Surface pining-upload in Quick Start**

After the "Publish to HuggingFace" subsection (after line 102), add:

```markdown
### Upload to Mock API

```bash
uv run pining-upload path/to/game_03/ \
  --provider skillcorner \
  --game-id game_03 \
  --bucket your-bucket-name \
  --date 2026-01-03 \
  --home "Auckland FC" \
  --away "Wellington Phoenix FC"
```

Requires AWS credentials and a deployed mock API instance. See [Setup Guide](terraform/docs/setup.md).
```

- [ ] **Step 8: Add Parquet explanation to Tech Stack**

Replace:

```markdown
- **pandas + pyarrow** for data processing and Parquet output
```

with:

```markdown
- **pandas + pyarrow** for data processing and [Parquet](https://parquet.apache.org/) output (columnar format, compressed, native to HuggingFace datasets)
```

- [ ] **Step 9: Rename "Related" section**

Replace:

```markdown
## Related
```

with:

```markdown
## See Also
```

- [ ] **Step 10: Verify**

Run: `uv run pytest -q`
Expected: All 64 tests pass (README changes don't affect code).

---

## Task 4: terraform/docs/setup.md Improvements

**Findings:** F12, F13
**Files:**
- Modify: `terraform/docs/setup.md`

- [ ] **Step 1: Fix trivializing "Just" in "Adding a New Provider"**

Replace lines 171-172:

```markdown
No infrastructure changes needed. Just upload files with a new provider prefix:
```

with:

```markdown
No infrastructure changes needed. Upload files with a new provider prefix:
```

- [ ] **Step 2: Fix trivializing "Just" in "Adding New Artifact Types"**

Replace lines 184-185:

```markdown
Just upload files with the desired name. The `get_artifact` handler resolves
```

with:

```markdown
Upload files with the desired name. The `get_artifact` handler resolves
```

- [ ] **Step 3: Add troubleshooting section**

Insert before the `## Teardown` section (before line 218):

```markdown
## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `terraform init` fails with "Failed to get existing workspaces" | Backend S3 bucket doesn't exist yet | Run Step 1 (Bootstrap) first — the state module creates the bucket |
| `terraform apply` fails with "Access Denied" | IAM permissions insufficient | Your AWS credentials need `s3:*`, `apigateway:*`, `lambda:*`, `iam:*`, `kms:*`, `dynamodb:*`, `logs:*` permissions. Use an admin role for initial setup |
| `pining-upload` fails with "NoSuchBucket" | Bucket name mismatch | Check `terraform output bucket_name` and pass the exact value to `--bucket` |
| `curl` returns `{"message":"Unauthorized"}` | Wrong or missing token | Verify `Authorization: Bearer <token>` header matches `api_token` in `terraform.tfvars` |
| `curl` returns `{"message":"Forbidden"}` | API Gateway stage mismatch | Ensure the URL ends with `/v1/...` — the stage name is part of the path |
| `curl` returns `{"error":"Artifact not found"}` | File not uploaded or wrong artifact name | Check S3: `aws s3 ls s3://{bucket}/{provider}/{game_id}/` — artifact name must match the filename prefix (e.g., `tracking` matches `tracking.jsonl`) |
| `terraform destroy` fails with "BucketNotEmpty" | S3 bucket still has objects | Empty it first: `aws s3 rm s3://{bucket} --recursive` |

---
```

---

## Task 5: New Repo Files

**Findings:** F8, F17
**Files:**
- Create: `CHANGELOG.md`
- Create: `SECURITY.md`

- [ ] **Step 1: Create CHANGELOG.md**

Create `CHANGELOG.md` at the repo root with retroactive history from git log:

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed
- Documentation improvements from audit (see PINING-DOC-AUDIT-1_12_0.md)

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

[Unreleased]: https://github.com/karstenskyt/pining-for-the-data/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/karstenskyt/pining-for-the-data/releases/tag/v0.1.0
```

- [ ] **Step 2: Create SECURITY.md**

Create `SECURITY.md` at the repo root:

```markdown
# Security Policy

## Scope

This project serves open, non-sensitive data (SkillCorner tracking data under MIT license). The mock provider API uses a static, publicly documented bearer token by design — it exercises authentication code paths, not real access control.

## Reporting a Vulnerability

If you discover a security issue in this project's code or infrastructure:

1. **Do not** open a public GitHub issue.
2. Email the maintainer directly (see GitHub profile for contact information).
3. Include a description of the vulnerability, steps to reproduce, and potential impact.

You should receive an acknowledgment within 72 hours. Fixes for confirmed vulnerabilities will be released as soon as practical.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x | Yes |
```

---

## Task 6: Source Code Docstrings

**Findings:** Audit backlog items — Lambda handlers (3), dataclasses (4), CLI entry point (1)
**Files:**
- Modify: `terraform/modules/functions/src/get_artifact.py`
- Modify: `terraform/modules/functions/src/list_matches.py`
- Modify: `terraform/modules/functions/src/list_providers.py`
- Modify: `src/deidentify/roster_generator.py`
- Modify: `src/formats/skillcorner.py`

- [ ] **Step 1: Add docstring to get_artifact handler**

In `terraform/modules/functions/src/get_artifact.py`, add a docstring to the `handler` function:

```python
def handler(event: dict, context: object) -> dict:
    """Resolve an artifact by name and return a presigned S3 URL via 302 redirect.

    Scans ``{provider}/{match_id}/{artifact}.*`` in S3 and redirects to the
    first file whose stem matches the requested artifact name.
    """
```

- [ ] **Step 2: Add docstring to list_matches handler**

In `terraform/modules/functions/src/list_matches.py`:

```python
def handler(event: dict, context: object) -> dict:
    """Return the matches index for a provider by reading ``{provider}/matches.json`` from S3."""
```

- [ ] **Step 3: Add docstring to list_providers handler**

In `terraform/modules/functions/src/list_providers.py`:

```python
def handler(event: dict, context: object) -> dict:
    """Return the provider index by reading ``providers.json`` from S3."""
```

- [ ] **Step 4: Add docstrings to roster_generator dataclasses**

In `src/deidentify/roster_generator.py`, add class-level docstrings:

```python
@dataclass
class Player:
    """A single player in a de-identified roster."""

@dataclass
class TeamRoster:
    """One team's roster for a game — team name and list of players."""

@dataclass
class MatchMetadata:
    """Competition metadata attached to a game roster (gender, age group, level)."""

@dataclass
class GameRoster:
    """Complete de-identified roster for a game — home team, away team, and metadata."""
```

- [ ] **Step 5: Add docstring to skillcorner main()**

In `src/formats/skillcorner.py`:

```python
def main() -> None:
    """CLI entry point for ``pining-ingest`` — validate a SkillCorner V3 match JSON and tracking JSONL pair."""
```

- [ ] **Step 6: Verify**

Run:

```bash
uv run ruff check src/ terraform/modules/functions/src/
uv run pyright src/
uv run pytest -q
```

Expected: All checks pass, all 64 tests pass.

---

## Task 7: New Documentation

**Findings:** F9, F10, F18
**Files:**
- Create: `docs/api-reference.md`
- Create: `docs/tutorial.md`
- Create: `docs/decisions/README.md`
- Modify: `README.md` (add links to new docs in Architecture section)

- [ ] **Step 1: Create API reference**

Create `docs/api-reference.md`:

```markdown
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
| `401` | `{"message": "Unauthorized"}` | Missing or invalid bearer token |
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
| `401` | `{"message": "Unauthorized"}` | Missing or invalid bearer token |
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
```

- [ ] **Step 2: Create tutorial**

Create `docs/tutorial.md`:

```markdown
# Tutorial: Exploring SkillCorner Tracking Data

**Objective:** By the end of this tutorial you will understand the structure of SkillCorner V3 tracking data, validate a game using the CLI, and know how to access the full 10-match dataset.

**Prerequisites:**
- Python 3.12+ installed
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- This repository cloned and dependencies installed (`uv sync --extra dev`)

**Time:** ~10 minutes

---

## 1. What You're Looking At

This project redistributes [SkillCorner open tracking data](https://github.com/SkillCorner/opendata) — 10 A-League Men matches from the 2024/2025 season. Each game consists of two files:

| File | Format | Contents |
|------|--------|----------|
| `match.json` | JSON | Match metadata — teams, players, pitch dimensions, periods, competition |
| `tracking.jsonl` | JSONL (one JSON object per line) | Frame-by-frame player and ball positions at 10 frames per second |

The repository includes sample fixtures you can explore without downloading the full dataset.

---

## 2. Inspect a Match Metadata File

Open the sample match file:

```bash
cat src/tests/fixtures/sample_match.json | python -m json.tool | head -50
```

Key fields to notice:

| Field | What it tells you |
|-------|-------------------|
| `home_team` / `away_team` | Team names, IDs, and short codes |
| `players[]` | Every player on the pitch — name, jersey number, position, team, and a `trackable_object` ID that links to tracking data |
| `match_periods[]` | Start and end frames for each half, with duration |
| `pitch_length` / `pitch_width` | Pitch dimensions in meters (typically 105 x 68) |
| `ball.trackable_object` | The ball's ID in tracking frames |

Each player's `trackable_object` field is the key that connects match metadata to tracking frames. In the sample, player Hiroshi Tanaka (jersey #1, GK) has `trackable_object: 101` — you'll see `player_id: 101` in the tracking data.

---

## 3. Inspect Tracking Frames

Open the sample tracking file:

```bash
head -2 src/tests/fixtures/sample_tracking.jsonl | python -m json.tool
```

Each line is one frame. Key fields:

| Field | What it tells you |
|-------|-------------------|
| `frame` | Frame number (sequential integer) |
| `timestamp` | Time within the match period (e.g., `"00:00:00.10"` = 0.1 seconds) |
| `period` | Match period (1 = first half, 2 = second half) |
| `ball_data` | Ball position: `x`, `y` in meters from pitch center, `z` for height, `is_detected` flag |
| `player_data[]` | Array of player positions: `player_id` (matches `trackable_object` from match metadata), `x`, `y`, `is_detected` |
| `possession` | Which player/team currently has the ball |

**Coordinate system:** `x` and `y` are in meters relative to the pitch center (0, 0). For a standard 105 x 68m pitch, `x` ranges from approximately -52.5 to 52.5 and `y` from -34 to 34.

Notice that some players have `"is_detected": false` — this means the tracking system lost sight of them in that frame (common with camera-based tracking). Your analytics code should handle missing detections.

---

## 4. Validate a Game

The `pining-ingest` CLI validates that a match JSON and tracking JSONL pair are structurally correct:

```bash
uv run pining-ingest \
  src/tests/fixtures/sample_match.json \
  src/tests/fixtures/sample_tracking.jsonl
```

Expected output:

```
Match 9999999: 6 players, 3 frames — OK
```

The validator checks that both files parse correctly, extracts player and frame counts, and optionally copies validated files to an output directory (`--output-dir`).

---

## 5. Access the Full Dataset

### From HuggingFace Hub (easiest)

The full 10-match dataset is published as Parquet on HuggingFace:

```python
from datasets import load_dataset

ds = load_dataset("luxury-lakehouse/pining-for-the-data")
```

### From the Mock API

If you need to test provider-style ingestion (bearer token auth, HTTP download):

```bash
TOKEN="test-token-pining-for-the-data"
API="https://your-api-url/v1"

# List available games
curl -s -H "Authorization: Bearer $TOKEN" "$API/skillcorner/matches" | python -m json.tool

# Download a tracking file
curl -s -L -H "Authorization: Bearer $TOKEN" \
  "$API/skillcorner/matches/game_03/tracking" -o tracking.jsonl
```

See the [Setup Guide](../terraform/docs/setup.md) to deploy your own API instance.

---

## Next Steps

- Browse the [API Reference](api-reference.md) for endpoint details
- Read [ARCHITECTURE.md](../ARCHITECTURE.md) for the full system design
- Explore the [C4 architecture diagrams](c4/architecture.html) in your browser
```

- [ ] **Step 3: Create ADR directory stub**

Create `docs/decisions/README.md`:

```markdown
# Architecture Decision Records

This directory is reserved for [Architecture Decision Records](https://adr.github.io/) (ADRs) documenting significant technical decisions in the project.

No ADRs have been written yet. When a non-obvious architectural choice is made, document the context, decision, and consequences here using the format:

```
NNNN-title-of-decision.md
```
```

- [ ] **Step 4: Add links to new docs in README.md**

In the Architecture section of `README.md`, replace lines 124-128:

```markdown
## Architecture

Open [`docs/c4/architecture.html`](docs/c4/architecture.html) in a browser to explore the C4 architecture diagrams (System Context, Container, Dynamic).

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full written architecture documentation.
```

with:

```markdown
## Architecture

Open [`docs/c4/architecture.html`](docs/c4/architecture.html) in a browser to explore the C4 architecture diagrams (System Context, Container, Dynamic).

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full written architecture documentation.

Additional documentation:
- [**Tutorial**](docs/tutorial.md) &mdash; guided walkthrough of SkillCorner tracking data structure
- [**API Reference**](docs/api-reference.md) &mdash; mock provider API endpoints, authentication, and data layout
```

- [ ] **Step 5: Verify**

Run: `uv run pytest -q`
Expected: All 64 tests pass.
