# Pining For The Data!

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

**pining-for-the-data** redistributes [SkillCorner open data](https://github.com/SkillCorner/opendata) (MIT license) in SkillCorner V3 format (match JSON + tracking JSONL at 10fps), with CLI tooling to validate, publish, and serve it through a mock provider API. A de-identification engine is included for future use with private/commercial tracking data from providers like [Respo.Vision](https://respo.vision/).

The end result is clean, highly structured, and open-source soccer tracking data published directly to [HuggingFace](https://huggingface.co/luxury-lakehouse) and served through a mock REST API. It is the companion dataset for testing and scaling analytics platforms like luxury-lakehouse.

> **Note:** SkillCorner open data is redistributed as-is under its MIT license — no de-identification is applied. See [`NOTICE`](NOTICE) for attribution. The de-identification system is reserved for future private data sources.

The data isn't dead. It's just resting.

## Key Features

- **De-identification Engine** &mdash; Generates synthetic rosters and manages two-layer jersey mappings. Four hand-picked character names, the rest randomly generated from fictional universes (GOT, LOTR, Breaking Bad, Princess Bride, and more).
- **Format Handlers** &mdash; Validates and processes provider-specific tracking data (SkillCorner V3 JSON/JSONL). Respo.Vision 3D pose scaffolded for future use.
- **Automated Publication** &mdash; Pushes tracking data and dataset cards directly to the HuggingFace Hub.
- **Mock Provider API** &mdash; AWS-backed REST API mimicking real provider download protocols, so anyone can test ingestion adapters without a commercial account.

## Distribution

| Level | What | Where | Friction |
|-------|------|-------|----------|
| **Level 1** | Static JSON/JSONL files | [HuggingFace Hub](https://huggingface.co/luxury-lakehouse) | `load_dataset()` &mdash; zero |
| **Level 2** | Mock REST API (SkillCorner protocol) | AWS (S3 + API Gateway + Lambda) | Bearer token auth &mdash; same code path as real provider |

## Mock Provider API

REST API mimicking commercial tracking data providers. Same bearer token auth, same endpoint shape, same response format.

| Method | Path | Response |
|--------|------|----------|
| GET | `/v1/providers` | JSON list of supported providers |
| GET | `/v1/{provider}/matches` | JSON list of games + available artifacts |
| GET | `/v1/{provider}/matches/{id}/{artifact}` | 302 redirect to presigned S3 URL |

```bash
TOKEN="test-token-pining-for-the-data"
curl -H "Authorization: Bearer $TOKEN" "$API_URL/v1/skillcorner/matches" | python -m json.tool
```

Deploy your own instance in ~15 minutes: [**Setup Guide**](terraform/docs/setup.md)

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

## De-identification (for future private data)

SkillCorner open data is redistributed as-is — no de-identification is applied. The de-identification engine below is retained for future use with private/commercial tracking data.

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

Remaining players get randomly generated names from fictional name pools. Opponent teams are assigned from a pre-defined list of 20 fictional clubs (Asgard Athletic, Krypton City, Shire Town, etc.).

## Architecture

Open [`docs/c4/architecture.html`](docs/c4/architecture.html) in a browser to explore the C4 architecture diagrams (System Context, Container, Dynamic).

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full written architecture documentation.

## Project Structure

```
pining-for-the-data/
├── src/
│   ├── deidentify/          # Roster generation, name pools, jersey mapping
│   ├── formats/             # Provider format readers/writers (SkillCorner, Respo.Vision)
│   ├── publish/             # HuggingFace Hub dataset publishing
│   ├── mock_api/            # Upload CLI for mock provider API
│   └── tests/               # pytest test suite (64 tests)
├── name_pools/              # JSON name lists (fictional first/last names, cities)
├── rosters/                 # Generated de-identified rosters per game
├── terraform/               # AWS infrastructure (S3 + API Gateway + Lambda)
├── assets/                  # Repo logo and images
├── docs/c4/                 # C4 architecture diagrams
├── pyproject.toml           # hatch build, ruff, pyright, pytest, CLI entry points
├── uv.lock                  # Locked dependencies
├── Dockerfile               # GPU-enabled container (future)
└── ARCHITECTURE.md          # Architecture documentation
```

## Tech Stack

- **Python 3.12+** with [uv](https://github.com/astral-sh/uv) for dependency management
- **pandas + pyarrow** for data processing and Parquet output
- **huggingface_hub** for dataset publishing (optional dependency)
- **Terraform** for AWS mock API infrastructure
- **ruff** for linting/formatting, **pyright** for type checking, **pytest** for testing

## License

Code: [MIT](LICENSE)
Redistributed SkillCorner data: [MIT](NOTICE)

## Related

- luxury-lakehouse &mdash; the main analytics platform that ingests this data
- [SkillCorner open data](https://github.com/SkillCorner/opendata) &mdash; source tracking data (MIT license)
