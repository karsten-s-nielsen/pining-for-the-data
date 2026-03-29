# pining-for-the-data

Open soccer tracking data — redistribution, validation, and mock provider API.
Companion repo to luxury-lakehouse.

## Architecture

- `src/deidentify/` — name pools, roster generation, two-layer jersey→identity mapping
- `src/formats/` — provider format readers/writers (SkillCorner V3 JSON/JSONL, Respo.Vision JSON future)
- `src/publish/` — HuggingFace Hub dataset publishing
- `src/mock_api/` — Upload CLI for mock provider API data management
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
- `pining-upload` — upload game artifacts to S3 and update provider indexes
