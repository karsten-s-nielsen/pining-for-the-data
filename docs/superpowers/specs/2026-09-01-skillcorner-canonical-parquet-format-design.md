# Canonical Owner-Tier SkillCorner Format — Design

**Date:** 2026-09-01
**Status:** Proposed (awaiting review)
**Related ADRs:** amends the owner-tier format stance recorded in ADR 0009 + the 2026-06-29 RM design spec; relates to 0008 (role-aligned artifact keys), 0010 (faithful-feed mimicry), 0005 (single-bucket multi-tier prefix)

---

## 1. Scope & drivers

Converge the **owner-tier** SkillCorner artifacts onto a single canonical, columnar
**Parquet/zstd** format, and build one ingest path that normalises every SkillCorner
source layout into it.

**Drivers (chosen):**
- **Smaller footprint** — a match is ~140 MB, ~97 % of which is uncompressed tracking JSON.
- **One canonical format** — kill the divergence between source layouts and land all
  owner-tier SkillCorner data in one shape.

**In scope for this cycle:**
- Define the canonical artifact set (this doc + ADR 0011).
- Build + test the ingest adapter for **all four** SkillCorner source shapes.
- **Write to S3 now:** migrate the existing Real Madrid owner-tier matches; ingest
  English Premier League 2025/26 (owner tier). PL 25/26 is chosen deliberately — current
  season, and it exercises the full raw-JSON path. It is partial (270 of ~380 delivered), but
  the ingest is idempotent and simply appends the remainder when it lands, so partialness is
  not a blocker.
- **Build but do not yet ingest:** Premier League 2024/25 and the Champions League 2025/26
  dataset — held **by owner decision** pending the missing-data conversation. This hold is a
  scoping choice, *not* a completeness rule: PL 24/25 is in fact complete but is grouped into
  the hold with CL (whose delivery has real gaps).

**Explicit non-goals:**
- **Public redistributed data is untouched.** The public SkillCorner tier (legacy
  id-prefixed CSV/JSONL) keeps its native format — changing it would break existing
  consumers (Hyrum's Law, per ADR 0009) and turn a "pure redistribution" into an
  "adaptation" (licence-cleanliness, per the IDSSE precedent).
- **Freeze-frames are dropped**, not re-formatted (see §3.3).
- No query-engine / analytics redesign; this is a storage-format change.

---

## 2. Background — current state

### 2.1 Two source families

SkillCorner course deliveries arrive in one of two layouts, both carrying the same
underlying data model:

| Layer | Parquet family (RM, PL 24/25) | Raw-JSON family (CL 25/26, PL 25/26) |
|-------|-------------------------------|--------------------------------------|
| Metadata | `meta/<id>.json` | `matches/<id>.json` (same schema) |
| Tracking | `tracking/<id>.json` | `tracking/<id>.json` (**identical** frame schema) |
| Events | `dynamic/<id>.parquet` (294 cols) | `dynamic_events/<id>.json` — actually **CSV** (310 cols) |
| Physical | `physical/<id>.parquet` (per-match) | one combined `physical/physical.json` (`{results:[…]}`) |
| Freeze | `freeze/<id>.parquet` (RM only) | *absent* |

Notes established during investigation:
- The `dynamic_events/*.json` files are **CSV mislabelled `.json`** — readers must sniff
  content, not trust the extension.
- The newer (25/26) events/physical schemas are strict **column supersets** of the older
  ones (events 294 ⊂ 310; physical 36 ⊂ 41) — normal schema evolution, nullable columns.
- **PL 24/25** is parquet-family but **has no `freeze/` and no `physical/`** layer, plus
  top-level `players.parquet`/`teams.parquet` and a reduced 3-column `matches.parquet`.
- The metadata schema is identical across families (verified field-for-field).

### 2.2 Current owner-tier storage (Real Madrid)

Each `skillcorner/_private/<match_id>/` currently holds:
`events.parquet` (snappy), `freeze_frames.parquet`, `metadata.json`,
`physical.parquet` (snappy), `tracking.json.gz`.

### 2.3 Footprint (prototype measurement, one representative match — see §2.5)

| Tracking encoding | Size |
|-------------------|------|
| raw JSON | 146.8 MB |
| gzip (current) | 8.75 MB |
| JSON + zstd-19 | 4.96 MB (only competitive at max level, slow) |
| **tracking Parquet (nested) + zstd** | **4.63 MB** |

Tracking dominates match size; Parquet/zstd both shrinks it ~32× vs raw and is columnar.
Events recompress snappy→zstd is ~20 % (1.43 → 1.15 MB); physical is negligible.

### 2.4 Freeze-frames are redundant with tracking

An investigation prototype found every freeze frame to be a tracking frame with **identical
positions** at those frames (shared player-rows: Δ = 0.0 m, `is_detected` 100 % agreement).
`freeze_frames.parquet`'s full column set is `{time, frame, period, player_id, is_detected,
is_ball, x, y, visible_area}` — every column maps to a tracking field: `time`→`timestamp`;
`frame`/`period`/`player_id`/`is_detected` are the same fields; `is_ball` distinguishes the
ball row; `x`/`y` are the positions (byte-compared, identical); `visible_area` is the same
broadcast-visible-region struct as tracking's `image_corners_projection`. Freeze carries **no
`z`**, so it is a strict subset of tracking (which has ball `z`) — an event-synchronised
*slice*, regenerable from tracking + events, hence dropped. SkillCorner already ships full
continuous tracking (all players extrapolated per frame with a per-player `is_detected` flag)
— unlike StatsBomb 360, where freeze frames are the *only* spatial data (so still required
there). *Caveat: positions were byte-compared; the `visible_area`↔`image_corners_projection`
correspondence is structural (same field), not separately byte-verified.*

### 2.5 Provenance of measurements

The footprint figures (§2.3), the layout benchmark (§4.3), the freeze-position comparison
(§2.4), and the "round-trips" claim were produced by **investigation-phase prototype scripts
(throwaway, not committed to the tree)**. They are directional evidence for the design, not
yet CI-enforced facts. Implementation makes them reproducible: the tracking round-trip (§4.2)
becomes a CI-tested invariant, and a small committed benchmark reproduces the size figures.
External counts (match totals, superset relations) derive from the source dataset manifests.
A reviewer wanting to re-run the prototypes before implementation can ask — the scripts are
committable as a throwaway `scripts/` benchmark.

---

## 3. Canonical artifact set

### 3.1 The set

Per owner-tier match, up to four artifacts (`physical` is optional — see §5.2):

| Role key (ADR 0008) | File | Notes |
|---------------------|------|-------|
| `metadata` | `metadata.json` | JSON kept — nested/hierarchical, human-readable, small; not tabular |
| `tracking` | `tracking.parquet` | nested columnar, **zstd** (was `tracking.json.gz`) |
| `events` | `events.parquet` | **zstd** (role/values unchanged) |
| `physical` | `physical.parquet` | **zstd** (role/values unchanged) |
| ~~`freeze_frames`~~ | — | **removed** |

The `tracking` **role key is unchanged**; only its wire format moves from gzip-JSON to
Parquet. ADR 0008 makes the wire format out-of-band of the role key, so this is within the
established contract. `upload_game`'s existing extension-stripping already derives the
correct keys (`tracking.parquet` → `tracking`, etc.).

### 3.2 `format_version` marker

During the migration window, both un-migrated (`tracking.json.gz`) and canonical
(`tracking.parquet`) matches share the role key `tracking`, so a consumer cannot tell them
apart by key. Each `matches.json` entry therefore gains an optional integer
**`format_version`**: canonical entries carry `format_version: 2`; an absent value means the
legacy layout (implicitly 1). Consumers dispatch their tracking deserializer on this field
rather than sniffing bytes.

The marker lives in `matches.json` (our index, which we own) — **not** in `metadata.json`,
which is the provider-faithful body and must not be polluted.

### 3.3 Scope of the format

Owner-tier only. Public redistributed matches keep their native legacy layout (§1 non-goals).

---

## 4. Tracking Parquet schema (nested, lossless)

### 4.1 Layout

**One row per frame**, mirroring the source JSON structure 1:1:

| Column | Type | Source |
|--------|------|--------|
| `frame` | int32 | `frame` |
| `timestamp` | string (nullable) | `timestamp` — kept as the raw `"HH:MM:SS.ms"` string |
| `period` | int8 (nullable) | `period` |
| `ball_data` | struct{`x`,`y`,`z`: float64; `is_detected`: bool} (nullable fields) | `ball_data` |
| `possession` | struct{`player_id`: int64; `group`: string} (nullable) | `possession` |
| `image_corners_projection` | struct of 8 float64 (nullable): `x_top_left`,`y_top_left`,`x_bottom_left`,`y_bottom_left`,`x_bottom_right`,`y_bottom_right`,`x_top_right`,`y_top_right` | `image_corners_projection` |
| `player_data` | list<struct{`x`,`y`: float64; `player_id`: int64 (nullable); `is_detected`: bool}> | `player_data` |

Compression: **zstd**.

### 4.2 Round-trip equivalence (the migration's sole pre-delete guard)

The gate requires `parquet_to_tracking(...)` to reproduce the source frames under a
**defined equivalence relation** — *not* byte identity (the functions exchange Python frame
lists, not bytes, so "byte-exact" is a category error). Two frame lists are equivalent iff,
after **normalizing absent optional keys to explicit nulls**, every scalar matches under a
**per-column-type** comparison (keyed to the §4.1 schema). Both halves matter:
- *Absent-vs-null normalization* prevents false-aborts: the source JSON may omit an optional
  key that Parquet's fixed schema round-trips as an explicit null; without normalization the
  gate would abort real, healthy matches (data-safe, but it stalls the migration).
- *Per-column-type comparison* is keyed to the §4.1 schema — **not** "value + type on every
  scalar", which would contradict the float64 coordinate columns:
  - **integer columns** (`frame`, `period`, `player_id`): value **and** type must match — a
    stray float here is a real coercion bug and must abort.
  - **float coordinate columns** (`x`, `y`, `z`): numeric equality after widening both sides
    to float. A source integer literal (e.g. `0`) stored as `float64 0.0` is lossless
    (integers are exact in float64) and must **pass**, not abort.
  - `bool` (`is_detected`) and `string` (`timestamp`): value equality.

Under this relation the layout is lossless: `image_corners_projection` is 8 scalars
(4 corners × x,y), fully preserved; `player_data` order/structure survive natively (no
ordinal bookkeeping); nullable `player_id` (detected-but-unidentified players) round-trips;
and **every frame is preserved**, including empty/pre-kickoff frames (`player_data: []`,
null `ball_data`) — frame cadence drives frame-rate derivation downstream, so a dropped
empty frame would shift the cadence median. The relation is enforced as a CI-tested property
(§8) and at runtime (§7.1).

### 4.3 Rejected alternatives (prototype measurement — see §2.5)

Fully-lossless comparison, representative match, zstd:

| Layout | Size | Round-trip |
|--------|------|------------|
| **Nested (chosen)** | **4.63 MB** | exact by construction (§4.2 relation) |
| Two-table (frames + positions) | 4.81 MB | exact (needs ordinal) |
| Flat (one row per frame×entity, +ord) | 4.88 MB | exact (needs ordinal) |

Nested is the smallest and the most source-faithful; it never repeats the wide per-frame
block, preserves array order/structure natively, and matches how the primary consumer (a
programmatic re-parser) already explodes player arrays. Flat's only edge — single-file
`SELECT` — barely helps a consumer that needs all fields to reconstruct frames anyway.

---

## 5. Events & physical

### 5.1 Events

- **Parquet feeders (RM, PL 24/25):** recompress snappy → zstd; schema/values unchanged.
- **CSV feeders (CL 25/26, PL 25/26):** `read_csv` → Parquet. To keep the canonical events
  schema **consistent and deterministic across matches/seasons**, the shared columns are
  conformed to a **pinned, committed reference events schema** (a column→Arrow-dtype map held
  in the repo, seeded once from an authored SkillCorner events Parquet); the newer columns are
  appended with fixed nullable types. A pinned schema — not one derived at runtime from
  whichever match happens to be processed — is what makes the conversion reproducible; a
  runtime-derived reference would be non-deterministic and defeat the consistency goal. Values
  are unchanged; consumers still own Delta schema-evolution for the extra columns. A future
  season's new columns are added by a deliberate, reviewed update to the pinned schema.

### 5.2 Physical

- **Parquet feeders:** recompress snappy → zstd.
- **Combined-JSON feeders:** `physical.json` (`{results: […]}`) is split by `match_id` into
  per-match `physical.parquet`. Superset columns tolerated (nullable). A match with no
  physical rows simply has no `physical` artifact (physical is optional).

---

## 6. Components (functional core / imperative shell)

Pure transforms in `formats/` (functional core); all S3/HF I/O and orchestration in
`scripts/` (imperative shell). This is pure-core / imperative-shell separation for
testability — labelled precisely: it is *not* ports-and-adapters/hexagonal, since there is no
dependency-inversion port between the two.

### 6.1 `formats/skillcorner_canonical.py` (new, pure, I/O-free)

- `tracking_to_parquet(frames: list[dict]) -> bytes` — nested layout (§4).
- `parquet_to_tracking(parquet_bytes) -> list[dict]` — inverse, for the round-trip gate.
- `events_to_parquet(...)` — CSV→Parquet (dtype-conformed) or Parquet→zstd recompress.
- `physical_to_per_match_parquet(...)` — combined-JSON split, or Parquet→zstd recompress.
- `recompress_parquet_zstd(parquet_bytes) -> bytes`.

### 6.2 Source readers (pure discovery + role-mapping)

- **Parquet-family reader** — extend `formats/skillcorner_bundle.py` to cover RM **and**
  PL 24/25. The required-artifact rule relaxes via a **new** `missing_required` check
  (`metadata` + `tracking` + `events` required; `physical` optional); `freeze` is not required
  and not emitted in the canonical output. `ARTIFACT_SPECS`/`source_files`/`is_complete` are
  left **unchanged** — they describe the *source* bundle (RM source really does contain freeze),
  so existing reader tests and the historical uploader are untouched. Tolerate PL 24/25's
  `players.parquet`/`teams.parquet` and reduced `matches.parquet`. This reader is built + tested
  this cycle per the "all four shapes" directive, but is **not exercised by a live ingest** now:
  RM migrates in place (§7.1) and PL 24/25 ingest is held (§1).
- **Raw-JSON-family reader** — new `formats/skillcorner_raw.py` for CL 25/26 **and**
  PL 25/26. Match discovery uses the `available_dynamic_event_match_ids` manifest (skips
  frameless/incomplete matches), not directory globs. Reuses `match_info` /
  `players_from_meta` (metadata schema is identical across families).

### 6.3 `scripts/` adapters

Orchestration + S3/HF I/O, dry-run-first, idempotent (mirrors the StatsBomb multi-match
ingest recipe). RM is an **in-place S3 transform** (bytes already on S3, byte-identical to
source — no multi-GB re-download); the other datasets ingest from their source.

### 6.4 Conscious boundary reversal

The 2026-06-29 RM design spec established "format normalization: none — the tracking body is
never parsed, staged as-is." This design deliberately reverses that for tracking: the body is
now parsed and columnarised. Peak
memory for the one-shot transform is a few hundred MB per match (JSON + Arrow table +
buffer) — acceptable for a batch job. Recorded in ADR 0011.

---

## 7. Migration & ingest flows

Both entry points drive the shared transforms; both dry-run-first and idempotent.

### 7.1 RM in-place S3 migration (existing owner-tier matches)

The invariant: **no destructive operation (overwrite or delete) happens until the
corresponding new object has been re-fetched from S3 and verified** — an in-memory
transform check is necessary but not sufficient, because a truncated/corrupt PUT would pass
it. Per `_private/<id>/`:

1. Get `tracking.json.gz` → gunzip → `json.loads` → frames.
2. `tracking_to_parquet(frames)` → bytes.
3. **Transform gate (in-memory):** `parquet_to_tracking(bytes)` is equivalent to `frames`
   under the equivalence relation in §4.2 — abort this match if not (nothing changed).
4. Recompress `events.parquet` + `physical.parquet` → zstd bytes; assert each recompressed
   table equals the original table (value equality) in memory.
5. **Upload:** `tracking.parquet` to its final key (additive — the `.json.gz` is a different
   key, untouched); the recompressed `events`/`physical` to **temporary staging keys** (the
   snappy originals stay at their final keys).
6. **Stored-object gate (from S3):** re-fetch the stored `tracking.parquet` and the staged
   `events`/`physical`; verify tracking round-trips (§4.2) and the events/physical tables
   equal the originals. **Abort before any mutation** if the stored objects fail — this is
   the gate that guards the irreversible steps, not step 3.
7. **Swap events/physical:** server-side copy staging → final key (overwrite), then delete
   the staging keys.
8. **Update `matches.json`:** `tracking` → `tracking.parquet`, drop `freeze_frames`, set
   `format_version: 2`. (Index now references only verified, existing files.)
9. **Delete old** `tracking.json.gz` + `freeze_frames.parquet`.

Delete/overwrite are always last and always gated on a verified *stored* object. Re-run: an
already-canonical entry with a verifying stored `tracking.parquet` is a no-op **except that it
also deletes any lingering legacy `tracking.json.gz`/`freeze_frames.parquet`** — self-healing
the crash-between-index-update-and-delete window the reviewer flagged; orphaned staging keys
from a mid-run failure are likewise cleaned/re-verified on the next run. (The
events/physical originals are additionally byte-identical to the HF source, a backstop — but
the stage→verify→swap flow means we never rely on it.)

### 7.2 PL 25/26 raw-JSON ingest (owner tier, `visibility=private`)

1. Download the combined `physical.json` once; index rows by `match_id`.
2. Per manifest match: download `matches/<id>.json`, `tracking/<id>.json`,
   `dynamic_events/<id>.json` (CSV); guard-skip any empty/missing body.
3. Transform → `metadata.json` (as-is), `tracking.parquet`, `events.parquet`
   (CSV→Parquet, dtype-conformed), `physical.parquet` (this match's rows) →
   **round-trip-verify tracking** → stage → `upload_game(..., visibility="private",
   format_version=2)`.
4. Derive + upload owner-tier players from the metadatas (dedup; skip already-public ids),
   as in the RM path.

The 270 delivered matches are ingested now; re-running after the remaining matches land
simply appends them (idempotent).

### 7.3 Index / model change

`MatchEntry` (`canonical/models.py`) gains an optional `format_version`; `upload_game`
(`mock_api/upload.py`) accepts and records it. Regenerate `schemas/matches.schema.json` via
`scripts/regenerate_schemas.py`; the generic schema-drift test needs no edit. `MatchEntry`
already sets `extra="allow"`, so the field is schema-safe. Legacy/public entries omit it.

---

## 8. Correctness & testing (TDD)

### 8.1 Correctness gates

1. **Round-trip + stored-object gate** — tracking round-trip under the §4.2 equivalence
   relation, enforced in unit tests and at runtime. The runtime gate verifies the *stored*
   S3 object (re-fetched), not just the in-memory transform, before any overwrite/delete; a
   failing match is skipped untouched. Events/physical recompress is likewise verified from
   S3 before the swap.
2. **Migration ordering** — write-new → verify → repoint index → delete-old; idempotent.
3. **No public-tier touch** — the RM migration operates only on `_private/` keys; an explicit
   guard/test asserts it never reads or writes a public key.
4. **Missing-artifact tolerance** — events/physical optional; freeze already-absent on re-run.
5. **Chesterton's Fence** — before removing freeze, grep the *pining* repo (scripts, tests,
   notebooks, docs) for any `freeze_frames` consumer and surface anything found. (The
   luxury-lakehouse consumer path has been confirmed not to fetch it.)

### 8.2 Tests

- **Pure unit (`formats/`):** tracking round-trip on a **wholly-synthetic** frames fixture
  (empty frames, null `player_id`, **absent optional keys** → normalized-null PASS,
  **integer coordinates** → int-source-equals-float64 PASS, ball with/without z, null
  pre-kickoff fields, multi-period), plus a genuine-corruption case that must ABORT;
  events CSV→Parquet dtype-conform (shared cols match reference dtypes, extra cols nullable,
  values preserved); events/physical recompress equality; combined-physical split-by-match;
  raw-JSON reader discovery-via-manifest + role mapping; parquet-family reader with PL 24/25
  quirks; `MatchEntry.format_version` + schema-drift test.
- **Adapter (`scripts/`, mocked S3):** RM one-match migration happy path + idempotent re-run
  no-op + **a corrupt/truncated *stored* object blocks the delete/swap** + events/physical
  recompress verified-from-S3 before overwrite; PL 25/26 one-match ingest; `_private/`-
  confinement guard.
- **Fixtures are wholly invented** — synthetic match/player ids, coordinates, and event
  values, verified per field; never renamed real SkillCorner rows (provider-internal ids and
  id→entity tuples are licensed).
- **Post-run ops verify** (mirror `verify_skillcorner_realmadrid_load.py`): against the live
  API — each migrated RM match serves parseable `tracking.parquet`/`events`/`physical`,
  `freeze` now 404s, `format_version` present, count unchanged; PL 25/26 serves its matches
  plus players; sample-from-live-response (no committed licensed ids).

---

## 9. Security, licence & faithfulness

- **Owner-tier gating unchanged** — these matches remain `visibility=private`, served only to
  the owner token; the `_private/` prefix tier separation (ADR 0002) and single-bucket
  multi-tier prefix isolation (ADR 0005) are unchanged.
- **Licence** — restricted; redistribution not permitted; recorded in each entry's
  `source.licence` and `provenance="original"`, as today.
- **Faithful archive** — values are preserved, not reshaped to any consumer's narrow schema
  (`possession` and `image_corners_projection` are retained even though the current consumer
  ignores them). This continues the ADR 0010 lineage: faithful, not a byte-for-byte
  passthrough.
- **Public redistribution untouched** — the MIT-redistributed public tier keeps its native
  format.

---

## 10. ADR 0011

A new ADR records the decision: the canonical owner-tier SkillCorner artifact set
(`metadata.json`, `tracking.parquet` nested/zstd, `events.parquet` zstd, `physical.parquet`
zstd); `freeze_frames` no longer produced; role keys stable (ADR 0008); `format_version`
marker. Consequences: **amends the owner-tier format stance** ("format normalization: none /
tracking never parsed") recorded in the 2026-06-29 RM design spec, alongside ADR 0009's
tier/format decisions; public tier untouched; faithful-archive stance retained; `freeze_frames` stays a
valid vocabulary key (StatsBomb still uses it) — only SkillCorner owner-tier stops emitting
it; the consumer retools its tracking reader and schema-evolves events. Also update
`docs/decisions/README.md` (+0011), the `CLAUDE.md` architecture line, and provider docs.

---

## 11. Rollout / ops

- Target environment: **dev** only (the current mock-API deployment).
- Sequence: land the code + tests (green locally: ruff + pyright + pytest) → **explicit human
  approval gate** → run the RM migration (dry-run first, inspect, then apply) → run the
  PL 25/26 ingest (dry-run first, then apply) → post-run ops verify → update docs/ADR.
- **No commit or S3 mutation happens without explicit approval** for that specific step.
- Token rotation: not applicable (no auth surface changes).

---

## 12. Decisions & open questions

**Decided:**
- Nested tracking schema (§4); zstd everywhere; freeze dropped; nested chosen over flat/two-table.
- Events CSV→Parquet conforms shared columns to a **pinned, committed** reference events
  schema (§5.1) — deterministic, not runtime-derived.
- `format_version` is a per-match integer in `matches.json`; canonical = 2, absent = legacy.
- Ingest this cycle: RM (migrate) + PL 25/26 (ingest); PL 24/25 + CL built but held pending
  the missing-data query.

**Open for review:**
- Exact `format_version` encoding (integer vs descriptive string) — bikeshed, low stakes.
- Storage location of the pinned events reference schema (a JSON under `schemas/` vs a module
  constant in `formats/`) — a placement detail, resolved in the plan.
