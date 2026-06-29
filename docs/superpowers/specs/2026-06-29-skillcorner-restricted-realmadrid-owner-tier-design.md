# Restricted SkillCorner (Real Madrid) → Owner Tier — Design Spec

**Date:** 2026-06-29
**Status:** Draft — revised after cross-session spec review (round 1)
**Scope:** Add a restricted SkillCorner tracking dataset (~99 Real Madrid matches, 2023/24 season, Soccermatics-course distribution) to the existing `skillcorner` provider as **owner-tier (`visibility="private"`)** content, served through the unchanged mock provider API. Native Parquet/JSON artifacts served as-is; no de-identification.

---

## 1. Purpose

We have access to a restricted SkillCorner dataset that we may use but **may not redistribute**. It should be reachable through the same provider-shaped API contract consumers already use, but gated so only the owner bearer token can see it. This is the exact scenario the owner tier was built for (cf. Gradient Sports WC 2022): access without redistribution.

The dataset is added under the **existing** `skillcorner` provider slug at the **private** visibility tier. An owner-token consumer browsing `/v1/skillcorner/...` sees the existing public open-data matches **and** these restricted matches merged; a public-token consumer sees only the open data. No new provider, no Lambda/Terraform change.

## 2. Licence Basis & Provenance

- **Licence:** Restricted — redistribution not permitted. Recorded verbatim in each match's `source.licence` and in the per-entry `provenance="original"` field (mirrors Gradient Sports, which is also restricted/original).
- **Tier:** **Owner only.** Served exclusively to the owner bearer token; the existing `_private/` prefix + tier-gating in `get_artifact`/`list_matches`/`list_players` enforces this. Tier mismatch returns a uniform `404` (no existence leak).
- **No de-identification.** Consistent with the Gradient Sports precedent: owner-tier restricted data is access-gated, not de-identified. The de-identification engine remains reserved for a future redistribution scenario, not this one.
- **Source attribution (owner-facing only):** `source.name = "SkillCorner"` with a note that the bytes arrived via the Soccermatics course distribution. This metadata is served only to the owner tier; it is **not** a public attribution claim.

## 3. Source Data Form

The source bundle (operator-local; never committed — see §9) is a directory tree:

```
<root>/
├── players.parquet        # global SkillCorner player catalogue (~147k rows) — reference
├── teams.parquet          # global team catalogue (~4k rows) — reference (not ingested)
├── matches.parquet        # fixture index — NOT used (see §5.2)
├── RealMadrid/
│   ├── matches.parquet     # (same caveat)
│   ├── meta/<match_id>.json         # rich per-match metadata (authoritative)
│   ├── tracking/<match_id>.json     # raw frame-by-frame positions (~143 MB each)
│   ├── dynamic/<match_id>.parquet   # 294-col event analytics (~6k events/match)
│   ├── freeze/<match_id>.parquet    # freeze-frame snapshots
│   ├── physical/<match_id>.parquet  # per-player physical metrics
│   └── velocities/<match_id>.parquet  # DERIVED from tracking — NOT ingested
└── (bundled venv/, analysis scripts, README — NOT data, ignored)
```

~99 matches. Per-match artifact formats: `tracking`/`meta` are JSON; `dynamic`/`freeze`/`physical`/`velocities` are Parquet.

### 3.1 Artifact selection

| Source | Ingested? | Rationale |
|---|---|---|
| `tracking/*.json` | ✅ (gzip-compressed) | Raw positional data — the core asset. ~143 MB JSON each; gzip ≈ 10×. |
| `dynamic/*.parquet` | ✅ | Rich event analytics. |
| `freeze/*.parquet` | ✅ | Freeze-frame snapshots. |
| `meta/*.json` | ✅ | Authoritative match metadata; also the index source (§5.2). |
| `physical/*.parquet` | ✅ | Per-player physical metrics. |
| `velocities/*.parquet` | ❌ | **Derived** from `tracking` by the bundled preprocessing script (Savitzky-Golay smoothing). Reproducible by any consumer; ~18 MB each is not worth storing. |
| `players.parquet` | ❌ | **Not needed.** The owner-tier `/players` catalogue is derived from the per-match `meta.players` lists, which are self-contained and richer (include position) — see §6. The 147k-row global parquet is never read. |
| `teams.parquet` | ❌ | No team-catalogue resource exists in the API; team info already lives inside `meta`. |
| `matches.parquet` | ❌ | Mismatched IDs (§5.2). |

### 3.2 Artifact-key vocabulary (role-aligned, per ADR 0008)

Artifact keys describe the **role**, not the wire format or source filename:

| Artifact key | Staged filename | Source | Role |
|---|---|---|---|
| `tracking` | `tracking.json.gz` | `tracking/<id>.json` | raw position/tracking data |
| `events` | `events.parquet` | `dynamic/<id>.parquet` | event analytics |
| `freeze_frames` | `freeze_frames.parquet` | `freeze/<id>.parquet` | freeze-frame snapshots |
| `metadata` | `metadata.json` | `meta/<id>.json` | match/team/player metadata |
| `physical` | `physical.parquet` | `physical/<id>.parquet` | per-player physical metrics |

**Role-alignment, not format-alignment** (ADR 0008): `tracking` here returns gzipped JSON; for IDSSE it returns XML; for Gradient Sports, JSONL. A provider-agnostic consumer selects by role key, then dispatches to a provider-specific parser. `freeze_frames` and `physical` are **new role keys** introduced here; they are registered in the **shared** role vocabulary by ADR 0009 (not as skillcorner-local keys) so a future provider with freeze-frame or physical artifacts reuses the same keys — that is the point of role-alignment. That a provider exposes a superset/subset of the shared vocabulary is expected.

**Known divergence (unchanged):** the *public* `skillcorner` matches predate ADR 0008 and use legacy id-prefixed keys (`<match_id>_match`, `tracking`). These restricted matches use the role-aligned vocabulary. Within the single `skillcorner` provider, two artifact-key conventions therefore coexist — public (legacy) and private (role-aligned). This is documented for consumers in the ADR and provider docs; we do **not** retro-rename the public keys (Hyrum's Law — existing consumers depend on them).

### 3.3 Compression

`tracking` JSON is gzip-compressed to `tracking.json.gz` before upload (broad consumer support, fast, ≈10× on this data). The gzip is performed in the **adapter** as a streamed file→file byte copy (`gzip` of the raw bytes — never `json.load` into memory; the 143 MB body is never parsed), keeping the `formats/` reader pure (§4). The legacy public `skillcorner` tracking used `.jsonl.bz2`; we deliberately diverge to gzip — these are independent artifacts and gzip is the more ubiquitous default. This makes a **third** wire convention coexist within `skillcorner` (public legacy bz2, public V3 JSONL, private gzip JSON); per ADR 0008 the wire format is out-of-band of the role key, so this is acceptable and is recorded consciously in ADR 0009. The other artifacts are Parquet (already columnar-compressed) and uploaded as-is.

## 4. Architecture & Components

No Lambda or Terraform changes — the API is already provider- and tier-generic. New code mirrors the **`formats/idsse.py` precedent specifically** for structure (a pure reader exposing constants + small parse functions, no I/O staging, paired with a `scripts/` worked adapter and a verifier). Gradient Sports is cited only for the owner-tier **decision** (`visibility="private"`, `provenance="original"`) — it is script-only and has no `formats/` reader, so it is not the structural model.

| Component | Responsibility | Depends on |
|---|---|---|
| `src/formats/skillcorner_bundle.py` | **Pure** reader for the SkillCorner multi-artifact bundle shape (as distributed by the Soccermatics course). Exposes: `discover_matches(root)` (walk `meta/`), `is_complete(match_dir, id)` (the 5 ingested artifacts present), `read_meta(path)` → `MatchInfo{match_id, date, home, away}` (date via `local_match_date`, §7), `players_from_meta(meta)` → `list[PlayerRecord-dict]` (derives participant player records from `meta.players`, §6), and the **role→source-filename MAP constant**. **No I/O staging, no gzip, no body parsing** — mirrors `formats/idsse.py`. Named after the *bundle shape*, not the licence tier (tier is an orthogonal upload/serve concern). | stdlib `json`, `datetime`, `zoneinfo`, `pathlib` |
| `scripts/upload_skillcorner_realmadrid.py` | Owner-tier worked ops adapter. Reads source root from **`$SKILLCORNER_RESTRICTED_DIR`**. For each match: stage the 5 artifacts into a **fresh temp dir** (so `upload_game`, which uploads *all* non-dot files in the dir, never picks up stray/leftover files), gzip `tracking` as a streamed file→file copy, call `upload_game(..., visibility="private", provenance="original")`. Then derive the participant-filtered catalogue (§6), **serialize it to a temp canonical JSON file**, and call `upload_players(input_file=<temp.json>, ..., visibility="private")`. Idempotent; supports `--limit N` smoke test. | `formats.skillcorner_bundle`, `mock_api.upload`, `mock_api.upload_players`, stdlib `gzip` |
| `scripts/verify_skillcorner_realmadrid_load.py` | Post-load verification against the live API with the **owner** token: asserts the restricted matches/artifacts are present and **private**-tier (public token must NOT see them — 404), a date-filtered query returns the expected window, artifact downloads return 200/302 + non-empty, and `/players` (owner) includes the derived records. Samples identifiers from the live response — no licensed IDs hard-coded. | live API, owner token |
| `src/tests/test_skillcorner_bundle_format.py` | Reader + mapping + adapter unit tests on **synthetic** fixtures (no real names/IDs): match discovery, missing-artifact detection, `meta` parse → `{match_id,date,home,away}`, participant-id extraction, gzip round-trip (adapter), player-collision skip-and-report path (§6.1, §11), adapter kwargs (`visibility="private"`, `provenance="original"`, role-aligned names, `velocities` excluded). | synthetic fixtures |
| `docs/decisions/0009-*.md` | ADR: restricted tier under an existing public provider; `freeze_frames`/`physical` registered in the shared role vocab; artifact-key + gzip convention coexistence; `velocities` exclusion; `matches.parquet` discrepancy. | — |
| `README.md` / provider docs | Note that `skillcorner` now also carries owner-tier restricted matches (no licensed specifics). | — |

### 4.1 Why a `formats/` module rather than inline script logic

Mirrors `formats/idsse.py`: keeps the fragile pieces (meta-JSON field names, artifact discovery, date parsing) behind a small unit-testable interface, isolated from the ops adapter. The large artifact bodies are never parsed — preserving "as-is" — so the module stays small and I/O-free. All staging, gzip, and S3 I/O live in the adapter (`scripts/`), keeping the hexagonal boundary honest.

## 5. Data Flow & Index Source-of-Truth

### 5.1 Flow

```
$SKILLCORNER_RESTRICTED_DIR  (operator-local source tree)
  → formats.skillcorner_bundle.discover_matches(root)  →  list of match_ids from meta/
  → for each match (optionally limited by --limit):
        is_complete()? else skip with explicit error
        meta = read_meta(meta/<id>.json) → MatchInfo{match_id, date (YYYY-MM-DD), home, away}
        stage into a FRESH temp dir under role-aligned names:
            tracking.json.gz (streamed gzip of tracking/<id>.json), events.parquet,
            freeze_frames.parquet, metadata.json, physical.parquet
        upload_game(
            game_dir=<staging>, provider="skillcorner", game_id=meta.match_id,
            bucket=..., visibility="private", provenance="original",
            date=meta.date, home=meta.home, away=meta.away,
            source_name="SkillCorner",  # source_url omitted — no public URL for restricted bytes
            source_licence="Restricted; redistribution not permitted",
        )
  → derive participant player set from all processed meta files (§6)
  → drop ids already present in the live public/private skillcorner players.json
    (skip-and-report, §6.1)
  → serialize remaining PlayerRecords → temp canonical JSON file
  → upload_players(input_file=<temp.json>, provider="skillcorner", visibility="private", ...)
```

Owner-token access uses the **unchanged** API:

```
GET /v1/skillcorner/matches            (owner token → public + restricted; public token → public only)
GET /v1/skillcorner/matches/{id}/tracking      (302 → presigned S3 URL; restricted id → 404 for public token)
GET /v1/skillcorner/players            (owner token merges public + _private/players.json)
```

### 5.2 Index source-of-truth: `meta/`, not `matches.parquet`

**Data-quality finding:** the bundle's `matches.parquet` lists 2024/25-season fixtures whose IDs do **not** match the per-match artifact filenames (2023/24 season). Joining the index on `matches.parquet` would mislabel or drop matches. The per-match `meta/<id>.json` is self-contained and authoritative (`id`, `date_time`, `home_team`, `away_team`, `players`, pitch dims, etc.), so **each `MatchEntry` is built from `meta`**, keyed by the `meta` `id`. `matches.parquet` is ignored. This discrepancy is recorded in the ADR so it is not rediscovered later.

### 5.3 game_id scheme

`game_id` = the SkillCorner match id from `meta` (e.g. a numeric string). It is regex-safe (`^[a-zA-Z0-9][a-zA-Z0-9_-]*$`), ≤128 chars. The public `skillcorner` game_id scheme is **not code-determined** — it is whatever was passed to `--game-id` at upload time (the README worked example used `game_NN`, but the V3 reader itself surfaces the numeric `match_data["id"]`), so we do **not** assume a particular public scheme. The restricted ids are distinct SkillCorner LaLiga match ids (public open data is A-League), so a collision is not expected; the adapter verifies the live public match-id set before upload, and `_check_no_tier_mixing` (keyed by game_id) is a loud backstop that blocks any accidental public↔private flip regardless.

## 6. Players Catalogue Derivation

- **Source = `meta.players`, not `players.parquet`.** Each `meta/<id>.json` carries a self-contained `players` list (the ~44-entry matchday squads for both teams), and each entry already has `id`, `first_name`, `last_name`, `short_name`, `birthday`, `gender`, and `player_role.{name, position_group}`. This is everything `players.parquet` has **plus** position, already scoped to participants — so the global parquet is never loaded and the reader stays pure-stdlib.
- **Participant set:** union (deduplicated by `id`) of all `meta.players` entries across the ingested matches — a few hundred ids.
- **Map → `PlayerRecord`** (canonical model): `id`←`str(id)`, `firstName`←`first_name`, `lastName`←`last_name`, `nickname`←`short_name`, `dob`←`birthday`, `position`←`player_role.name`, `positionGroupType`←`player_role.position_group`. `visibility="private"`, `source.name="SkillCorner"`. (`short_name` always present → satisfies the `nickname OR (firstName AND lastName)` validator even when a name field is blank.)
- **Serialize + upload:** write the mapped `PlayerRecord`s to a temp canonical JSON file, then `upload_players(input_file=<temp.json>, provider="skillcorner", visibility="private")` → `skillcorner/_private/players.json`. (`upload_players` takes a JSON *file* path, not a list.) The serve path merges public + private with private-wins on id collision (existing behaviour).

### 6.1 Cross-tier player-id collision (resolved review concern)

SkillCorner player ids are **global**, and the public open-data `skillcorner` matches come from the **same provider / same id space**. A Real Madrid participant — or an opponent international — *may* already exist in the public `skillcorner/players.json`. The upload tool is unforgiving here: `upload_players` **raises on the first** id found in the other tier and aborts the **entire** catalogue upload (`upload_players.py:96-103`) — so one overlapping id would kill the whole derivation, even though the serve path would have handled it (private-wins).

Therefore the **adapter handles collisions gracefully, before calling `upload_players`**:

1. **Empirically diff** the derived participant id set against the live public `skillcorner/players.json` (and any existing `_private/players.json`) — a concrete pre-build step, not an assumed "disjoint".
2. **Skip-and-report** colliding ids: drop them from the private catalogue and log each. A colliding player is *already public*; their **name is not the restricted asset** (the tracking is), and the restricted matches still reference them by id via the served `metadata`/`events` artifacts. Leaving them in the public tier is correct.
3. Upload only the non-colliding remainder. `upload_players`' raise-on-collision stays as an **untouched backstop** — by construction the adapter never feeds it a colliding id.

This is unit-tested (§11) so the policy is locked, not incidental.

## 7. Index Metadata & API Filter Parity

Date filtering parity is achieved by populating `date` (YYYY-MM-DD) on each `MatchEntry` from `meta.date_time`. `meta.date_time` is a tz-aware ISO 8601 timestamp; the reader returns the **local match date** by converting to `Europe/Madrid` before taking `.date()` — reusing the exact pattern of `formats/idsse.py:local_match_date` (`Europe/Berlin`). This is the semantically-correct match date and is robust to late/edge kickoffs and any future non-evening fixtures, at the cost of one `zoneinfo` line. `home`/`away` populated for display parity. `updated_at` set automatically by `upload_game`.

## 8. De-identification

**None.** Owner-tier gating is the control. Real player/team names and birthdays are served only to the owner token. This matches Gradient Sports and the project rule that redistributed/owner data is served as-is, with the de-identification engine reserved for a future redistribution use case.

## 9. Public-Repo Hygiene

This repository is **public**. The spec, ADR, tests, scripts, README, and commit messages contain **no** real match ids, player names, team names, sample data rows, or the operator-local source path. Specifically:

- Source path is referenced only as `$SKILLCORNER_RESTRICTED_DIR` (no Dropbox/drive-letter path committed).
- Test fixtures are **synthetic** (invented ids/names) reproducing the artifact *shape*, not real bytes.
- The verifier **samples identifiers from the live API response** rather than hard-coding licensed ids.
- Commit messages describe the mechanism (restricted owner-tier ingest for SkillCorner), not the licensed contents.

## 10. Error Handling

| Condition | Behaviour |
|---|---|
| `$SKILLCORNER_RESTRICTED_DIR` unset/missing | Fail fast with a clear message; no upload. |
| Match missing one of its 5 ingested artifacts | Skip that match with an explicit error (mirrors the IDSSE/Gradient missing-file guard). |
| Malformed `meta` JSON | Reader raises a clear exception; match skipped, not silently defaulted. |
| Player id collides with existing public/private `skillcorner` players | Adapter **skips that id and reports it** (the player is already public; only the tracking is restricted), then uploads the remainder (§6.1). `upload_players`' raise-on-collision remains an untouched backstop. |
| Re-run / partial prior run | Idempotent: `upload_game`/`upload_players` update in place by id; no-tier-mixing guard blocks a public↔private flip. |
| `--limit N` | Process only the first N matches (smoke test). |

## 11. Testing

- **Reader unit** (`test_skillcorner_bundle_format.py`): synthetic bundle → asserts match discovery from `meta/`, missing-artifact detection (`is_complete`), `meta` parse → `{match_id,date,home,away}` (incl. `Europe/Madrid` local-date conversion).
- **Player-derivation unit**: synthetic `meta.players` entries → asserts `players_from_meta` maps fields correctly (`id`/`firstName`/`lastName`/`nickname`/`dob`/`position`/`positionGroupType`), dedups by `id` across matches, and that records validate against `PlayerRecord`.
- **Player-collision unit (review #1/#6)**: derived ids overlapping a stubbed live public `players.json` → asserts the adapter **skips the colliding ids and reports them**, serializes only the remainder, and never passes a colliding id to `upload_players` (the all-or-nothing failure mode is provably avoided).
- **Adapter staging logic**: mocked filesystem + mocked `upload_game`/`upload_players` (no real S3) → asserts a **fresh temp dir** per match, role-aligned artifact names, gzip applied to tracking (streamed, not in-memory parse), `velocities` excluded, players serialized to a JSON file before `upload_players(input_file=…)`, and the expected kwargs (`provider="skillcorner"`, `visibility="private"`, `provenance="original"`, date/home/away, source_\*).
- **Tier-gating regression**: a private `MatchEntry` is filtered out for a public-tier request and returned for owner-tier (exercises existing handler logic with the new entries; reuses existing handler test patterns).
- **Post-load verification** (`scripts/verify_skillcorner_realmadrid_load.py`): run after the loader against the live API — owner token sees the restricted matches + artifacts + derived players; **public token does not** (404 on a restricted id; restricted matches absent from the public match list); date-filter query returns the expected window. Large `tracking` validated via the 302 presigned URL with a `Range: bytes=0-0` GET (206 + positive total), not a full body read.
- **Gate (Shift Left):** `ruff`, `pyright` (basic), `pytest` (hermetic subset) all green before completion; run the `final-review` skill before the final commit.

## 12. Out of Scope

- **HuggingFace Hub** — restricted data is never pushed to HF; the only channel is the owner-tier provider API.
- **De-identification** — none (§8).
- **`teams` catalogue / `matches.parquet`** — not ingested (§3.1, §5.2).
- **`velocities` artifact** — excluded as derived (§3.1).
- **Format normalization** — none; native Parquet/JSON (tracking gzipped) served as-is.
- **Terraform / Lambda changes** — none; the API is already provider- and tier-generic.
- **Retro-renaming the legacy public `skillcorner` artifact keys** — left as-is (Hyrum's Law).

## 13. Architecture Decision Record

A new ADR (`0009-restricted-tier-under-existing-public-provider.md`) records:

1. **Decision:** restricted SkillCorner data lives under the **existing** `skillcorner` provider at `visibility="private"`, rather than a new provider slug — one data provider, two tiers, as the system was designed.
2. **Consequence:** two artifact-key conventions and three wire conventions coexist within `skillcorner` (legacy id-prefixed public keys + bz2/JSONL vs. ADR-0008 role-aligned private keys + gzip JSON); consumers need a per-tier/per-match map. Accepted to avoid a Hyrum's-Law break on the public keys.
3. **Shared role vocab:** `freeze_frames` and `physical` are added to the **shared** ADR-0008 role vocabulary (not skillcorner-local), so future providers reuse them.
4. **Notes:** `velocities` excluded as derived; `matches.parquet` ignored due to the ID discrepancy (index built from `meta/`); cross-tier player-id collisions handled by adapter skip-and-report (§6.1).

## 14. Resolved Review Items (round 1)

| # | Item | Resolution |
|---|---|---|
| 1 (blocking) | Cross-tier player-id collision is likely + all-or-nothing | Adapter empirically diffs against live public/private players.json and **skips-and-reports** collisions; `upload_players` guard kept as backstop; unit-tested (§6.1, §11). |
| 2 | Precedent imprecise (GS is script-only) | Structure now mirrors **`formats/idsse.py`** specifically; GS cited only for the owner-tier decision (§4). |
| 3 | Module named after licence tier | Renamed `skillcorner_bundle.py` (format/bundle shape, not tier) (§4). |
| 4 | Keep `formats/` pure | Reader exposes discovery + meta-parse + role→filename MAP only; gzip/staging/S3 I/O moved to the adapter (§3.3, §4). |
| 5 | `upload_players` takes a JSON file, not a list | Adapter serializes derived records → temp canonical JSON, then `upload_players(input_file=…)` (§5.1, §6). |
| 6 | No test for collision path | Added player-collision unit test (§11). |
| 7 | `freeze_frames`/`physical` are vocab supersets | Registered in the **shared** role vocab via ADR 0009 (§3.2, §13). |
| 8 | Date: prefer local over UTC component | Reader converts to `Europe/Madrid` (IDSSE `local_match_date` pattern) (§7). |
| 9 | gzip = third convention in skillcorner | Noted consciously in ADR 0009 (§3.3, §13). |
| 10 | Public match-id scheme unverified | §5.3 no longer assumes `game_NN`; adapter verifies live public ids; `_check_no_tier_mixing` backstops. |
| 11 | Staging hygiene | Fresh temp dir per match so `upload_game` never uploads stray files (§4, §5.1). |
