# Architecture — pining-for-the-data

> **Status**: SkillCorner V3 format + Mock Provider API implemented.
> **Last Updated**: 2026-03-20
> **Repository**: [`karstenskyt/pining-for-the-data`](https://github.com/karstenskyt/pining-for-the-data)

---

## 1. Purpose

Tooling and infrastructure to redistribute, validate, and publish soccer tracking data as an open dataset. SkillCorner open data (MIT license) is redistributed as-is; a de-identification engine is included for future use with private/commercial data. Companion to luxury-lakehouse.

---

## 2. Data Flow

```
SkillCorner Open Data (MIT) ──> match.json + tracking.jsonl (10fps)
                                        │
                                        ▼
                           ┌─────────────────────┐
                           │  pining-for-the-data │
                           │                     │
                           │  1. Validate format  │
                           │  2. Redistribute     │
                           └──────┬──────┬───────┘
                                  │      │
                     ┌────────────┘      └────────────┐
                     ▼                                ▼
           ┌──────────────────┐             ┌──────────────────┐
           │  HuggingFace Hub │             │   AWS Mock API   │
           │  (Level 1)       │             │   (Level 2)      │
           │                  │             │                  │
           │  load_dataset()  │             │  GET /v1/matches │
           │  Zero friction   │             │  Bearer token    │
           └──────────────────┘             └──────────────────┘
                     │                                │
                     └──────────┬─────────────────────┘
                                ▼
                     ┌──────────────────────┐
                     │   luxury-lakehouse    │
                     │                       │
                     │  src/ingestion/       │
                     │    skillcorner.py      │
                     │    provider_framework/ │
                     │                       │
                     │  Same adapter code    │
                     │  works against mock   │
                     │  and real provider    │
                     └──────────────────────┘
```

**Two modes:**
- **As-is redistribution** (SkillCorner open data): validate and publish, no transformation
- **De-identification** (future private data): full synthetic identity pipeline via RosterGenerator + TwoLayerMapping

---

## 3. Module Architecture

### 3.1 De-identification (`src/deidentify/`)

Generates synthetic rosters and maps jersey numbers to fictional identities.

| Module | Responsibility |
|--------|---------------|
| `name_pools.py` | Load and sample from JSON name pools (GOT, LOTR, BB/BCS, Princess Bride, etc.) |
| `roster_generator.py` | Generate full game rosters — 4 featured names + random fill. CLI entry point. |
| `mapping.py` | Two-layer jersey-to-identity mapping. Resolves home/away by jersey number. |

**Two-layer mapping design:**

```
Layer 1 (private, maintained by data owner):
    Player A  →  "T'Challa Stark"     (stable across all games)
    Player B  →  "Westley Montoya"    (stable across all games)

Layer 2 (per-game, in roster JSON):
    Game 3:  #41 → T'Challa Stark,  #30 → Westley Montoya
    Game 4:  #30 → T'Challa Stark,  #41 → Westley Montoya  ← numbers swapped
```

Layer 1 ensures analytical continuity. Layer 2 handles jersey number changes.

### 3.2 Format Handlers (`src/formats/`)

Read and validate provider-specific tracking data.

| Module | Status | Provider | Format |
|--------|--------|----------|--------|
| `skillcorner.py` | Implemented | SkillCorner | V3 match JSON + tracking JSONL at 10fps |
| `respovision.py` | Scaffolded | Respo.Vision | JSON, 3D pose, 50+ keypoints |
| `convert.py` | Scaffolded | Cross-format | Respo.Vision 3D -> SkillCorner-equivalent 2D |

### 3.3 Publishing (`src/publish/`)

| Module | Responsibility |
|--------|---------------|
| `hf_push.py` | Push Parquet to HuggingFace Hub with MIT dataset card. CLI entry point. |

### 3.4 Mock API (`src/mock_api/`)

Upload CLI (`upload.py`) for S3 data management. Lambda handlers live in `terraform/modules/functions/src/`.

| Endpoint | Handler | Purpose |
|----------|---------|---------|
| `GET /v1/providers` | `list_providers` | List supported tracking data providers |
| `GET /v1/{provider}/matches` | `list_matches` | List available games + artifacts for a provider |
| `GET /v1/{provider}/matches/{id}/{artifact}` | `get_artifact` | Serve artifact via presigned S3 URL (302 redirect) |

---

## 4. Infrastructure (Planned)

```
AWS (effectively $0/month at expected volume)
├── S3 bucket
│   ├── private prefix  →  source-of-truth JSON/JSONL (versioned)
│   └── public prefix   →  served via presigned URLs
├── API Gateway          →  REST endpoints mimicking provider protocol
└── Lambda               →  auth check + S3 presigned URL generation
```

Terraform modules in `terraform/`. See [setup guide](terraform/docs/setup.md).

---

## 5. Relationship to luxury-lakehouse

```
luxury-lakehouse (analytics platform)         pining-for-the-data (open data)
├── src/ingestion/skillcorner.py ──────────→  Mock API serves same JSON/JSONL format
├── src/ingestion/provider_framework/  ────→  Adapters work against mock + real
└── fct_tracking_frames                       Data flows in identically
```

The mock API is designed so that `luxury-lakehouse`'s existing SkillCorner ingestion code works against it unmodified. Same bearer token auth, same JSON/JSONL schema, same download protocol.

---

## 6. Technology Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Build system | hatch | Consistent with luxury-lakehouse |
| Dependency management | uv | Fast, reproducible, lockfile-based |
| Python version | 3.12+ | No Databricks constraint (luxury-lakehouse is pinned to 3.10) |
| Data format | Parquet via pyarrow | Columnar, compressed, HF-native |
| License (code) | MIT | Simple permissive — no business logic to protect |
| License (data) | MIT | Redistributed from SkillCorner open data (MIT license) |
| Repo name | pining-for-the-data | Dead Parrot sketch — "pining for the fjords" Danish Easter egg |
