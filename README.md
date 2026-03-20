# pining-for-the-data

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
  <img src="assets/pining-for-the-data.png" alt="pining-for-the-data — Dead Parrot sketch meets soccer analytics" width="800">
</p>

<sup>Comic by NanoBanana &mdash; inspired by Monty Python's *Dead Parrot*</sup>

---

## About The Project

Finding high-quality, open-source tracking data in soccer analytics is notoriously difficult. Datasets are either locked behind commercial licenses or plagued by privacy concerns regarding player identification.

**pining-for-the-data** is a CLI tooling pipeline designed to solve this. It ingests raw tracking feeds from providers like [Metrica Sports](https://metrica-sports.com/) and (soon) [Respo.Vision](https://respo.vision/), applies strict de-identification protocols, and maps player jersey numbers to persistent, fictional identities.

The end result is clean, highly structured, and open-source youth soccer tracking data from real club matches, recorded on [Veo3](https://www.veo.co/) and published directly to [HuggingFace](https://huggingface.co/luxury-lakehouse) in Parquet format. It is the companion dataset for testing and scaling analytics platforms like [luxury-lakehouse](https://github.com/karsten-s-nielsen/luxury-lakehouse).

The data isn't dead. It's just resting.

## Key Features

- **De-identification Engine** &mdash; Generates synthetic rosters and manages two-layer jersey mappings. Four hand-picked character names, the rest randomly generated from fictional universes (GOT, LOTR, Breaking Bad, Princess Bride, and more).
- **Format Handlers** &mdash; Converts raw, provider-specific tracking data (CSV/JSON) into standardized outputs using pandas. Metrica Sports implemented; Respo.Vision 3D pose scaffolded.
- **Automated Publication** &mdash; Pushes the de-identified tracking data and dataset cards directly to the HuggingFace Hub (CC-BY-4.0).
- **Mock Provider API** &mdash; AWS-backed REST API mimicking real provider download protocols, so anyone can test ingestion adapters without a commercial account (planned).

## Distribution

| Level | What | Where | Friction |
|-------|------|-------|----------|
| **Level 1** | Static Parquet files | [HuggingFace Hub](https://huggingface.co/luxury-lakehouse) | `load_dataset()` &mdash; zero |
| **Level 2** | Mock REST API (Metrica protocol) | AWS (S3 + API Gateway + Lambda) | Bearer token auth &mdash; same code path as real provider |

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
  --date 2026-03-15 \
  --home-jerseys 1,11,17,20,23,25,26,30,31,33,38,40,41,45 \
  --away-jerseys 2,5,8,10,14,18,22 \
  --opponent-index 16 \
  --seed 42
```

### De-identify Tracking Data

```bash
uv run pining-ingest tracking_data.csv \
  --roster rosters/game_03.json \
  --output-dir output/ \
  --format both
```

### Publish to HuggingFace

```bash
uv run pining-publish output/ --message "Add Game 03 tracking data"
```

## De-identification

All player identities are synthetic. The home team (**Wakanda FC**) roster uses a two-layer mapping:

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
│   ├── formats/             # Provider format readers/writers (Metrica, Respo.Vision)
│   ├── publish/             # HuggingFace Hub dataset publishing
│   ├── mock_api/            # Lambda handlers for mock provider API (future)
│   └── tests/               # pytest test suite (44 tests)
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
- **Terraform** for AWS mock API infrastructure (future)
- **ruff** for linting/formatting, **pyright** for type checking, **pytest** for testing

## License

Code: [MIT](LICENSE)
Data (on HuggingFace Hub): [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)

## Related

- [luxury-lakehouse](https://github.com/karsten-s-nielsen/luxury-lakehouse) &mdash; the main analytics platform that ingests this data
- [Metrica Sports open data](https://github.com/metrica-sports/sample-data) &mdash; the format standard this project follows
