# pining-for-the-data

De-identified youth soccer tracking data — open dataset and mock provider API.
Companion repo to [luxury-lakehouse](https://github.com/karsten-s-nielsen/luxury-lakehouse).

## Architecture

- `src/deidentify/` — name pools, roster generation, two-layer jersey→identity mapping
- `src/formats/` — provider format readers/writers (Metrica CSV, Respo.Vision JSON future)
- `src/publish/` — HuggingFace Hub dataset publishing
- `src/mock_api/` — Lambda handlers for mock provider REST API (Level 2)
- `src/tests/` — pytest test suite
- `name_pools/` — JSON name lists (fictional first/last names, cities)
- `rosters/` — generated de-identified roster JSONs per game
- `terraform/` — AWS infrastructure (S3 + API Gateway + Lambda)

## Conventions

- Python 3.12+, hatch build system
- Ruff for linting/formatting (line-length 120)
- Pyright for type checking (basic mode)
- pytest for testing (src/tests/)
- Pre-commit hooks: ruff, yaml checks, secret scanning
- No tracking data CSVs in the repo — too large for git
- MIT license (code), CC-BY-4.0 (data on HF Hub)

## De-identification

- Home team: Wakanda FC
- 4 featured names: Fezzik Took, Tormund Tully, Westley Montoya, T'Challa Stark
- Remaining players: randomly generated from name pools (GOT, LOTR, BB/BCS, Princess Bride, EEAAO, Ghibli, Elf)
- Opponent teams: 20 pre-generated fictional names (Asgard Athletic, Krypton City, etc.)
- Two-layer mapping: stable synthetic identity (Layer 1) → per-game jersey mapping (Layer 2)

## CLI Entry Points

- `pining-generate-roster` — generate a synthetic roster for a game
- `pining-ingest` — read raw provider CSV, apply de-identification, output clean Parquet
- `pining-publish` — push Parquet to HuggingFace Hub
