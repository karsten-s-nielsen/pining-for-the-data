# Pining For The Data!

[![CI](https://github.com/karsten-s-nielsen/pining-for-the-data/actions/workflows/python-ci.yml/badge.svg)](https://github.com/karsten-s-nielsen/pining-for-the-data/actions/workflows/python-ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> **Customer:** 'Ello, I wish to register a complaint about this tracking dataset what I cloned not half an hour ago from this very repo.
>
> **Maintainer:** Oh yes, the open-source Danish Blue. What's, uh... what's wrong with it?
>
> **Customer:** I'll tell you what's wrong with it. It's completely de-identified. There are no real names in here.
>
> **Maintainer:** No, no, it's uh... it's resting.
>
> **Customer:** Look, mate, I know a synthetic roster when I see one.
>
> **Maintainer:** It's not synthetic. It's *pining*. It's pining for the data lake. Remarkable dataset, the Danish Blue, isn't it? Beautiful Parquet.

<p align="center">
  <img src="assets/pining-for-the-data.jpg" alt="pining-for-the-data — Dead Parrot sketch meets soccer analytics" width="800">
</p>

<sup>Comic by NanoBanana &mdash; inspired by Monty Python's *Dead Parrot*</sup>

---

## About The Project

Finding high-quality, open-source tracking data in soccer analytics is notoriously difficult. Datasets are either locked behind commercial licenses or plagued by privacy concerns regarding player identification.

**pining-for-the-data** redistributes [SkillCorner open data](https://github.com/SkillCorner/opendata) (MIT license) in SkillCorner V3 format (match metadata as JSON + frame-by-frame tracking as [JSONL](https://jsonlines.org/) at 10 frames per second), with CLI tooling to validate, publish, and serve it through a mock provider API. The project includes a de-identification engine for future use with private/commercial tracking data from providers like [Respo.Vision](https://respo.vision/).

The end result is clean, highly structured, and open-source soccer tracking data published directly to [HuggingFace](https://huggingface.co/luxury-lakehouse) and served through a mock REST API. It is the companion dataset for testing and scaling analytics platforms like luxury-lakehouse.

> **Note:** SkillCorner open data is redistributed as-is under its MIT license — no de-identification is applied. See [`NOTICE`](NOTICE) for attribution. The de-identification system is reserved for future private data sources.

The data isn't dead. It's just resting.

## Key Features

- **De-identification Engine** &mdash; Generates synthetic rosters and manages two-layer jersey mappings. Four hand-picked character names, the rest randomly generated from fictional universes (GOT, LOTR, Breaking Bad, Princess Bride, and more).
- **Format Handlers** &mdash; Validates and processes provider-specific tracking data (SkillCorner V3 JSON/JSONL). [JSONL](https://jsonlines.org/) = one JSON object per line, one line per frame. Respo.Vision 3D pose scaffolded for future use.
- **Automated Publication** &mdash; Pushes tracking data and dataset cards directly to the HuggingFace Hub.
- **Mock Provider API** &mdash; AWS-backed REST API mimicking real provider download protocols, so anyone can test ingestion adapters without a commercial account.

## How to Access the Data

| Level | What | Where | Friction |
|-------|------|-------|----------|
| **Level 1** | Static JSON/JSONL files | [HuggingFace Hub](https://huggingface.co/luxury-lakehouse) | `load_dataset()` &mdash; zero |
| **Level 2** | Mock REST API (SkillCorner protocol) | AWS (S3 + API Gateway + Lambda) | Bearer token auth &mdash; same code path as real provider |

## Mock Provider API

REST API mimicking commercial tracking data providers. Bearer token auth, same endpoint shape, same response format.

| Method | Path | Response |
|--------|------|----------|
| GET | `/v1/providers` | JSON list of supported providers |
| GET | `/v1/{provider}/matches` | JSON list of games + available artifacts |
| GET | `/v1/{provider}/matches/{id}/{artifact}` | 302 redirect to presigned S3 URL |
| GET | `/v1/{provider}/players` | JSON list of player reference records |
| GET | `/v1/{provider}/players/{id}` | Single player reference record |
| GET | `/v1/health` | Health check (unauthenticated) |

**Two-tier auth.** Public-tier content (the redistributed SkillCorner data) is served against the documented public token. Private-tier content (operator-loaded restricted datasets) requires a separate owner-tier token stored in AWS SSM Parameter Store. Tier semantics are uniform 404 on mismatch (no existence leaks). See [`docs/api-reference.md`](docs/api-reference.md) for the full contract; the design rationale lives in [`docs/superpowers/specs/2026-05-02-private-data-tier.md`](docs/superpowers/specs/2026-05-02-private-data-tier.md).

```bash
API_URL="https://your-api-gateway-id.execute-api.us-east-1.amazonaws.com"
TOKEN="test-token-pining-for-the-data"
curl -H "Authorization: Bearer $TOKEN" "$API_URL/v1/skillcorner/matches" | python -m json.tool
```

Deploy your own instance in ~15 minutes: [**Setup Guide**](terraform/docs/setup.md)

## Quick Start

After these steps you will have the project installed with all CLI tools available and the test suite passing.

### Prerequisites

- **Python 3.12+** &mdash; [download](https://www.python.org/downloads/)
- **uv** (Python package manager) &mdash; [install](https://docs.astral.sh/uv/getting-started/installation/)
- **git**

### Installation

```bash
# Clone and install
git clone https://github.com/karsten-s-nielsen/pining-for-the-data.git
cd pining-for-the-data
uv sync --extra dev

# Run tests — all 166 should pass
uv run pytest
```

> **Troubleshooting:** If `uv` is not found, install it first: `curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS/Linux) or `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` (Windows). Then restart your terminal.

### Generate a Roster

```bash
uv run pining-generate-roster \
  --game-id game_03 \
  --date 2026-01-03 \
  --home-jerseys 1,11,17,20,23,25,26,30,31,33,38,40,41,45 \
  --away-jerseys 2,5,8,10,14,18,22 \
  --opponent-index 16 \
  --seed 42
```

### Validate Tracking Data

```bash
uv run pining-ingest match.json tracking.jsonl
```

### Publish to HuggingFace

```bash
uv run pining-publish output/ --message "Add Game 03 tracking data"
```

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

### Upload Player Catalogue

```bash
uv run pining-upload-players players.json \
  --provider skillcorner \
  --bucket your-bucket-name \
  --visibility public
```

See [`docs/api-reference.md`](docs/api-reference.md) for the full player record schema.

## De-identification (for future private data)

SkillCorner open data ships as-is — no de-identification applied. The de-identification engine below exists for future use with private/commercial tracking data.

The home team (**Wakanda FC**) roster uses a two-layer mapping:

- **Layer 1 (private):** Player &rarr; stable synthetic identity (same player is always the same character across all games)
- **Layer 2 (per-game):** Jersey number &rarr; synthetic identity (handles number changes between games)

Four players have featured names that persist across all games:

| Jersey | Name |
|--------|------|
| #1 GK | Fezzik Took |
| #17 | Tormund Tully |
| #30 | Westley Montoya |
| #41 | T'Challa Stark |

Remaining players get randomly generated names from fictional name pools. The tool assigns opponent teams from a pre-defined list of 20 fictional clubs (Asgard Athletic, Krypton City, Shire Town, etc.).

## Architecture

Open [`docs/c4/architecture.html`](docs/c4/architecture.html) in a browser to explore the C4 architecture diagrams (System Context, Container, Component, Dynamic, Deployment).

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full written architecture documentation.

Additional documentation:
- [**Tutorial**](docs/tutorial.md) &mdash; guided walkthrough of SkillCorner tracking data structure
- [**API Reference**](docs/api-reference.md) &mdash; mock provider API endpoints, authentication, and data layout

## Project Structure

```
pining-for-the-data/
├── src/
│   ├── deidentify/          # Roster generation, name pools, jersey mapping
│   ├── formats/             # Provider format readers/writers (SkillCorner, Respo.Vision)
│   ├── publish/             # HuggingFace Hub dataset publishing
│   ├── mock_api/            # Upload CLIs (pining-upload, pining-upload-players)
│   ├── canonical/           # Canonical Pydantic models (MatchEntry, PlayerRecord); kept out of Lambda src so the runtime stays pydantic-free (ADR 0006)
│   └── tests/               # pytest test suite (166 tests)
├── name_pools/              # JSON name lists (fictional first/last names, cities)
├── rosters/                 # Generated de-identified rosters per game
├── schemas/                 # Published JSON Schemas for matches.json + players.json
├── scripts/                 # One-shot ops scripts (regenerate_schemas, upload_pff_wc2022, verify_pff_load)
├── terraform/               # AWS infrastructure (S3 + API Gateway + Lambda + SSM + KMS + CloudTrail)
├── assets/                  # Repo logo and images
├── docs/
│   ├── c4/                  # C4 architecture diagrams
│   ├── decisions/           # Architecture Decision Records
│   ├── superpowers/         # Brainstormed specs and implementation plans
│   ├── api-reference.md     # Full mock provider API contract
│   └── tutorial.md          # Walkthrough of SkillCorner tracking data structure
├── pyproject.toml           # hatch build, ruff, pyright, pytest, CLI entry points
├── uv.lock                  # Locked dependencies
├── Dockerfile               # GPU-enabled container (future)
└── ARCHITECTURE.md          # Architecture documentation
```

## Tech Stack

- **Python 3.12+** with [uv](https://github.com/astral-sh/uv) for dependency management
- **pandas + pyarrow** for data processing and [Parquet](https://parquet.apache.org/) output (columnar format, compressed, native to HuggingFace datasets)
- **huggingface_hub** for dataset publishing (optional dependency)
- **Terraform** for AWS mock API infrastructure
- **ruff** for linting/formatting, **pyright** for type checking, **pytest** for testing

## License

Code: [MIT](LICENSE)
Redistributed SkillCorner data: [MIT](NOTICE)

## See Also

- [luxury-lakehouse](https://github.com/karsten-s-nielsen/luxury-lakehouse) &mdash; the main analytics platform that ingests this data
- [SkillCorner open data](https://github.com/SkillCorner/opendata) &mdash; source tracking data (MIT license)
