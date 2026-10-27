# Match competition/season fields and artifact content identity — design

- **Date:** 2026-10-26
- **Status:** approved by reviewers A and B in round 2 (§10, §11); approved by the owner on 2026-10-26; plan-review changes in §12
- **Branch:** `feat/match-competition-season-artifact-meta`
- **Requested by:** the luxury-lakehouse v2 ingestion design (§29.1), through the owner

## 1. Intent

Lakehouse v2 groups pining matches by competition and season. It uses the groups for its module filters and for the Ops preview. Today a `matches.json` entry has no competition and no season, so a consumer must fetch one metadata artifact per match to group the list. This change puts the values on every list entry, taken from each match's own metadata artifact.

During design the owner added two related decisions:

- Every entry carries the content identity (`sha256`, `size`) of each artifact, so a client can skip a download when the bytes did not change.
- `updated_at` means "this record changed". The upload tooling stops changing it on a byte-identical re-upload. The same contract applies to `players.json` records.

## 2. Goals, non-goals, anti-goals

**Goals**

- G1. Four optional fields on `MatchEntry` and on every `{provider}/matches.json` entry: `competition_id`, `competition_name`, `season_id`, `season_name`.
- G2. One `artifact_meta` object on every entry: `{<artifact_key>: {"sha256": <hex>, "size": <bytes>}}`.
- G3. Populate G1 and G2 for every existing entry of `gradientsports`, `idsse`, `skillcorner` and `statsbomb`, on both tiers.
- G4. Every future upload sets G1 and G2.
- G5. `updated_at` changes only when a record changes, for match entries and for player records.
- G6. Docs, ADR 0013, JSON schema, CHANGELOG and tests follow the change.

**Non-goals**

- A `?competition_id=` (or any other new) filter on `GET /v1/{provider}/matches`. Not required by the request.
- The LIST token tier. It is a separate project with its own branch and spec.
- A SkillCorner `competition_edition` id field. The owner was told it exists. It is not requested.
- A Lambda handler change. `list_matches` returns entries as stored (§5.10).
- A release tag. The CHANGELOG entry goes under `[Unreleased]`. A release is a separate owner decision.

**Anti-goals** (the change fails if any of these happen, even if it works)

- A value that the metadata artifact does not give. No inference, no lookup, no default.
- A value in the wrong role: a name copied into an id field, or an id copied into a name field.
- A hash that is not the hash of the stored object bytes.
- A changed `updated_at` on a record whose fields and hashes did not change.
- A break for existing consumers. All new fields are optional. No existing field changes shape.

## 3. Constraints

- **Honesty rule (ADR 0010).** Values may never be invented. A value the source does not give stays absent.
- **Licensed data.** Provider ids and (id → real entity) tuples are licensed data. Test fixtures are wholly invented. This document and other committed docs name field paths, not real values.
- **Path-param regex.** Ids must match `^[a-zA-Z0-9][a-zA-Z0-9_-]*$` (max 128 characters), the regex in `src/canonical/models.py` and `terraform/modules/functions/src/shared.py`. Every live id passes it (checked 2026-10-26).
- **Lambda runtime.** The handlers stay pydantic-free (ADR 0006). The models stay in `src/canonical/`.
- **Lambda response size.** The synchronous Lambda response limit is 6 MB. The estimate for the largest list after this change is about 0.9 MB (`skillcorner`, 909 entries; today 0.51 MB). This leaves about 6x headroom.
- **Public repo.** No operator-local paths. Shell examples use `$PINING_BUCKET`.

## 4. Owner decisions (2026-10-26)

| # | Decision |
|---|---|
| D1 | Field names: `competition_id`, `competition_name`, `season_id`, `season_name`. Values in the provider's own vocabulary. Ids are strings. |
| D2 | Fill a field only when the source gives the value, in the role the source gives it. Never copy a name into an id or the reverse. |
| D3 | Gradient Sports: `season_name` is the metadata `season` value verbatim. `season_id` is absent. |
| D4 | api-reference.md states: "season_id absent means the source gives no season id; season_name is the source value verbatim." |
| D5 | The backfill sets a new `updated_at` on every entry it changes. Incremental clients (`updatedSince`) must see the new fields. |
| D6 | Every entry carries `artifact_meta` with the `sha256` and `size` of each stored artifact object. The backfill hashes the stored objects. Every upload sets it. |
| D7 | The upload tooling does not change `updated_at` on a byte-identical re-upload. `updated_at` changes only when an entry field or an artifact hash changes. |
| D8 | `pining-upload-players` follows the same contract: a player's `updated_at` changes only when that record's fields change. |
| D9 | api-reference.md and the CHANGELOG get the field table, the `updated_at` contract, and "clients should compare artifact sha256 before downloading". |
| D10 | A new ADR 0013 records the entry change contract (`updated_at`) and the artifact content identity (`artifact_meta`). |
| D11 | The live backfill runs before the commit, so the one commit records an applied and verified migration. |

## 5. Design

### 5.1 New `MatchEntry` fields

| Field | Type | Validation | Meaning |
|---|---|---|---|
| `competition_id` | `str \| None` | path-param regex, max 128 | The source's competition id. |
| `competition_name` | `str \| None` | non-empty | The source's competition name, verbatim. |
| `season_id` | `str \| None` | path-param regex, max 128 | The source's season id. Absent when the source gives no season id. |
| `season_name` | `str \| None` | non-empty | The source's season label, verbatim. |
| `artifact_meta` | `dict[str, ArtifactMeta] \| None` | keys equal the `artifacts` keys | Content identity of each stored artifact object. |

`ArtifactMeta` is a new model with two required fields:

- `sha256`: `str`, pattern `^[0-9a-f]{64}$`. The SHA-256 of the stored S3 object bytes, lowercase hex.
- `size`: `int`, `>= 0`. The stored object size in bytes.

`artifact_meta` is optional on the model, so that old index entries still validate. The upload tooling and the backfill always set it. A model validator rejects an `artifact_meta` whose key set differs from the `artifacts` key set.

The schema `$id` stays `urn:pining-for-the-data:schema:matches:v1`. The change only adds optional properties, and the model already has `extra="allow"`.

### 5.2 Source of each value

Each value comes from the match's own metadata artifact. The paths below were checked on all 1307 live entries on 2026-10-26. Every entry gave every listed value, and no fetch failed.

| Provider / tier | Metadata artifact key | `competition_id` | `competition_name` | `season_id` | `season_name` |
|---|---|---|---|---|---|
| `gradientsports` private | `metadata` (JSON, a one-element list) | `[0].competition.id` | `[0].competition.name` | absent (D3) | `[0].season` |
| `idsse` public | `metadata` (DFL XML) | `MatchInformation/General@CompetitionId` | `@CompetitionName` | `@SeasonId` | `@Season` |
| `skillcorner` public | `{id}_match` (SkillCorner V3 JSON) | `competition_edition.competition.id` | `competition_edition.competition.name` | `competition_edition.season.id` | `competition_edition.season.name` |
| `skillcorner` private | `metadata` (SkillCorner V3 JSON) | same paths as public | same | same | same |
| `statsbomb` private (both source families) | `metadata` (StatsBomb match JSON) | `competition.competition_id` | `competition.competition_name` | `season.season_id` | `season.season_name` |

The public `skillcorner` tier keeps its native, id-prefixed artifact keys (ADR 0011). Its metadata artifact is the single key that ends in `_match`. If zero keys or more than one key end in `_match`, the code fails loudly.

**Match id.** Each metadata artifact also gives the match id. The backfill uses it to prove that it read the right artifact (§5.9). Checked on all 1307 live entries on 2026-10-26: every entry has exactly one metadata artifact, and the metadata match id equals the entry `id` on every entry.

| Provider / tier | Match-id path | Raw type |
|---|---|---|
| `gradientsports` private | `[0].id` | `str` |
| `idsse` public | `MatchInformation/General@MatchId` | `str` |
| `skillcorner` public and private | `id` | `int` |
| `statsbomb` private | `match_id` | `int` |

An `int` match id is compared as its decimal string, by the §5.3 id rule.

### 5.3 Value rules

One shared function converts each raw value. Every extraction path uses it.

- **Id fields.** An `int` becomes its decimal string (`61` → `"61"`). A non-empty `str` is kept verbatim. `None` or an empty string means absent. Any other type (`float`, `bool`, `list`, `dict`) raises `ValueError`. A `float` is refused because `49.0` would become `"49.0"`. Refusing also catches pandas upcasts (see `statsbomb._int_or_none`). The commercial StatsBomb family reads `competitions.json` through `normalise_records` (`statsbomb.py:539`), which also accepts a pandas column-orient dump, and `build_metadata` copies `competition_id` and `season_id` without `_int_or_none` (`statsbomb.py:284,289`). So a future commercial drop can give a `float` id. The rule refuses it loudly, and the dry-run reports the raw type of every id (§5.9). The 327 stored StatsBomb metadata artifacts hold `int` ids (checked 2026-10-26).
- **Name fields.** A non-empty `str` is kept verbatim. `None` or an empty string means absent. Any other type raises `ValueError`.
- **Roles.** Each field reads only its own source path (§5.2). No fallback reads another path. A missing id never takes the name, and a missing name never takes the id.
- **Absent** means the key is not in the entry. The dump uses `exclude_none=True`, as it does today.

Treating an empty string as absent is the author's call. An empty string carries no value, so writing it would be noise and not honesty.

### 5.4 `artifact_meta`

- `sha256` is the SHA-256 of the bytes stored in S3, and `size` is their length. No object has `Content-Encoding` set (checked 2026-10-26). So the bytes a client downloads through the presigned URL equal the stored bytes, `.json.gz` files included.
- The upload tooling hashes each local file before the upload. `upload_file` stores the file bytes unchanged, so the local hash equals the stored hash.
- The backfill hashes the stored objects. It streams each body through the hash in fixed-size chunks and never holds a whole object in memory. The largest object is 419 MB.
- S3 ETags are not used. Some are multipart ETags, which are not content hashes.

### 5.5 The `updated_at` contract

**Match entries.** `updated_at` changes only when the entry record changes. The record is every field except `updated_at`. This includes the four new fields and `artifact_meta`. So a changed artifact byte changes `updated_at`, and a byte-identical re-upload does not.

**Player records.** `updated_at` changes only when the record's fields change, `updated_at` excluded.

**Comparison.** Compare the validated new record with the stored record as JSON-equivalent dicts, with `updated_at` removed from both. A field present on one side and absent on the other is a change.

**Replacement text** for the `updated_at` sentence in `docs/api-reference.md` ("List Matches", today line 113):

> `updated_at` changes only when the entry changes: any entry field, or the `sha256` of any artifact. A byte-identical re-upload does not change it. Clients can poll it with `updatedSince` for incremental refresh. Clients should compare each artifact's `artifact_meta.sha256` with their stored copy before they download it.

"List Players" gets the same contract, without the artifact sentence.

### 5.6 Extraction code

The four values come from the same pure readers that the loaders already use for `date`, `home` and `away`. The backfill reuses the same readers, so the upload path and the backfill path cannot disagree.

- **Shared:** a frozen dataclass `CompetitionSeason(competition_id, competition_name, season_id, season_name)`, all `str | None`, plus the §5.3 conversion functions. Recommended home: `src/formats/competition_season.py`.
- **`formats/skillcorner_bundle.py` and `formats/skillcorner_opendata.py`:** `match_info(meta)` also returns a `CompetitionSeason` from `competition_edition`. One shared helper reads the V3 `competition_edition` shape for both modules.
- **`formats/statsbomb.py`:** `match_info(metadata)` also returns a `CompetitionSeason` from `competition` and `season`. `statsbomb_open` uses the same `match_info`.
- **`formats/idsse.py`:** add a parse from bytes or from a parsed root, so the backfill can parse the stored XML without a temporary file. `read_match_information(path)` keeps its signature and also returns the four values from `General`. (The IDSSE reader is `read_match_information`, not `match_info`. "`match_info`" in this spec means each family's reader.)
- **New `formats/gradientsports.py`:** `match_info(metadata_obj)` holds the parsing that `scripts/upload_gradient_wc2022.py` does inline today (`date`, `home`, `away`, including the one-element-list unwrap), plus the competition values and the match id from `[0].id` (§5.2). The `date`, `home` and `away` results must stay byte-for-byte the same as today, and a test proves it. The loader keeps the file stem as `game_id` and fails loudly if `[0].id` differs from it (author's call). This module exists so that the loader and the backfill share one reader (author's call).

### 5.7 Upload tooling

**`upload_game()` and `pining-upload`** (`src/mock_api/upload.py`):

- New keyword parameters and CLI flags: `--competition-id`, `--competition-name`, `--season-id`, `--season-name`. All optional. The values pass through the §5.3 rules and the model validation.
- **Order of work.** Today `upload_game` writes the objects (`upload.py:88-96`) before it builds and validates the entry (`upload.py:103-115`). The new order is:
  1. Apply the §5.3 rules, hash each staged file (sha256, size), build the entry with `artifact_meta`, and validate it with `MatchEntry`. A refusal here stops the call before any S3 write.
  2. Run the tier-mixing check, as today.
  3. Upload the objects.
  4. Read the stored entry. If the new entry equals the stored entry under §5.5, keep the stored `updated_at`. Otherwise set a new one.
  5. Write the index.
- **Remaining window.** A failure between step 3 and step 5 (a crash or a network error) leaves new object bytes under an old `artifact_meta`. Two things detect or repair it: `scripts/verify_match_index_fields.py --full-hash` (§5.11) finds the mismatch, and the next re-upload of that match repairs it, because its new hashes differ from the stored ones.
- The artifact bytes are uploaded on every call, as today. So `pining-upload` can repair a bad stored object, and the repair also corrects `artifact_meta` and `updated_at`. (The v0.8.1 Git-LFS repair did not use `upload_game`. It used `scripts/backfill_skillcorner_opendata_lfs.py`, which writes objects directly; see §5.8a.) The index write may be skipped when the entry is unchanged (builder's call; the index bytes would be identical).
- `_update_matches_json` replaces the whole entry, as today. So every loader must pass the four values on every call, or a re-upload drops them. §5.8 covers this, and a test proves it for each loader. A stored field that the new entry does not carry is dropped, and that counts as a change. No live entry carries a field outside the model today (checked 2026-10-26).

**`upload_players()` and `pining-upload-players`** (`src/mock_api/upload_players.py`): for each incoming record that equals the stored record under §5.5, keep the stored `updated_at`. Otherwise set a new one. Update the module docstring, which today says "updated_at is set on every write".

### 5.8 Loader scripts

Every loader that calls `upload_game` passes the four values from its reader's `match_info`:

- `scripts/upload_gradient_wc2022.py` (uses the new `formats/gradientsports.py`)
- `scripts/upload_idsse_bundesliga.py`
- `scripts/upload_skillcorner_opendata.py`
- `scripts/upload_skillcorner_raw.py`
- `scripts/upload_skillcorner_parquet.py`
- `scripts/upload_skillcorner_realmadrid.py`
- `scripts/upload_statsbomb_club.py`
- `scripts/upload_statsbomb_open.py`

`artifact_meta` needs no loader change, because `upload_game` computes it.

**Reproducible staging.** D7 holds only if a loader stages the same bytes from the same source on every run. Three loaders do not do this today. They gzip through `gzip.open`, which writes the current time into the gzip header:

| Loader | Staging code |
|---|---|
| `scripts/upload_statsbomb_club.py` | `src/mock_api/staging.py:16-19` (`_gzip_file`) |
| `scripts/upload_statsbomb_open.py` | `src/mock_api/staging.py:16-19` (`_gzip_file`) |
| `scripts/upload_skillcorner_realmadrid.py` | its own `_gzip_file` (`:45-48`) |

The StatsBomb loaders do not skip live matches, so without a fix every re-run would change every hash and `updated_at`. The fix: both `_gzip_file` functions write a fixed header, with `mtime=0` and no file name (for example `gzip.GzipFile(filename="", mode="wb", fileobj=..., mtime=0)`). The other five loaders copy source bytes or write Parquet. A "stage twice, bytes equal" test for every loader proves that all eight are reproducible.

**One-time effect.** The stored StatsBomb `.json.gz` objects carry their old upload time in the header. (No legacy RM `.gz` object is live: every owner-tier SkillCorner entry is in the canonical Parquet set.) The first re-upload after this change stores the new fixed-header bytes. So the hash and `updated_at` of each such match change once. The CHANGELOG states this.

### 5.8a Other writers of artifact objects and indexes

Every writer of artifact objects, `matches.json` or `players.json` in `src/` and `scripts/` (no Terraform resource writes data objects):

| Writer | Writes | Treatment |
|---|---|---|
| `src/mock_api/upload.py` (`upload_game`, `pining-upload`) | objects, `matches.json`, `providers.json` | Sets `artifact_meta` and the `updated_at` contract (§5.7). |
| `src/mock_api/upload_players.py` | `players.json` | Player `updated_at` contract (§5.7). |
| The 8 loaders (§5.8) | through `upload_game` and `upload_players` | Pass the four values; reproducible staging. |
| `scripts/backfill_match_competition_and_artifact_meta.py` (new) | `matches.json` | §5.9. |
| `scripts/backfill_skillcorner_opendata_lfs.py` | objects only (`:105`, direct `upload_file`) | **Changed.** For every object it replaces, it also sets that entry's `artifact_meta` entry from the new bytes and sets a new `updated_at`, in one `IfMatch` write of `matches.json`. If the entry has no `artifact_meta` yet, the script hashes every stored object of that entry and writes a complete `artifact_meta`, so the key-set validator (§5.1) passes (author's call; the alternative was to refuse). On a `412` for the index write (the objects are already replaced), the script makes no further write, prints every replaced object key with its new `sha256`, and exits non-zero; the §5.9 backfill then recomputes `artifact_meta` from the stored bytes (author's call). Reason: it is the repo's byte-repair tool, and a repair that keeps the old hash is never downloaded by a client that follows the sha256 guidance. On today's live data it finds no pointer and writes nothing. |
| `scripts/migrate_skillcorner_tracking_parquet.py` | objects and `matches.json` | **Guarded.** Its conversion branch (entries without `format_version: 2`) refuses an entry that carries `artifact_meta`. Such an entry can only come from a new legacy-layout upload; the canonical loaders are the path for it. Its `format_version: 2` self-heal sweep deletes only two fixed legacy object names, `tracking.json.gz` and `freeze_frames.parquet` (`:79-84`), which are not in a canonical entry's `artifacts`. So it does not touch `artifact_meta` and stays unchanged. On today's live data every owner-tier entry has `format_version: 2`, so the script converts nothing. |
| `scripts/backfill_skillcorner_artifacts.py` | `matches.json` | No change. It touches only entries whose `artifacts` is a list. No live entry is in that form, and such an entry cannot carry a valid `artifact_meta`. |
| `scripts/migrate_pff_to_gradientsports.py`, `scripts/migrate_gradientsports_slug.py` | `copy_object`; `matches.json`, `players.json` and `providers.json` rewrites | No change. `copy_object` keeps the bytes, so the hashes stay valid. The index rewrites change only the top-level `provider` field (and the slug in `providers.json`) and copy every entry whole. Their source prefixes are empty today, so a re-run copies nothing. |

`docs/context/scripts-and-migrations.md` today calls the completed migrations "idempotent and safe to re-run". It gets the treatment above for each one.

### 5.9 Backfill script

New one-shot script `scripts/backfill_match_competition_and_artifact_meta.py`. It follows `scripts/backfill_skillcorner_artifacts.py` (ETag `IfMatch` write, idempotent).

- **Input:** `--bucket` (default `$PINING_BUCKET`), `--provider` (repeatable; default: all providers in `providers.json`), `--apply` (without it the run is a dry-run that writes nothing).
- **For each entry:**
  1. Find the metadata artifact key (§5.2) and read the stored object.
  2. Run the provider's `match_info` on it. Fail loudly if the metadata match id (§5.2 match-id table) differs from the entry `id`, because that proves a wrong artifact. This includes `gradientsports` (`[0].id`).
  3. Set the four fields by the §5.3 rules.
  4. Stream-hash every stored artifact object and set `artifact_meta`.
  5. Validate the entry with `MatchEntry`.
  6. If the entry changed under §5.5, set `updated_at` to the run's single timestamp. Otherwise keep it.
- **Other fields** (`date`, `home`, `away`, `artifacts`, `visibility`, `provenance`, `source`, `format_version`, and any extra fields) are copied unchanged.
- **Two passes** (owner ruling, 2026-10-26, plan review; §12):
  - Pass 1 reads each selected provider's `matches.json` and its ETag, and builds every new entry (steps 1–6 above). It writes nothing.
  - Any error for any entry in any provider stops the run after pass 1. Examples: a refused value such as a `float` id, a match-id mismatch, a missing stored object, a read error. No provider is written, and the exit status is non-zero.
  - Pass 2 runs only when pass 1 found no error and `--apply` is set.
- **Write (pass 2):** one `put_object` per provider `matches.json`, with `IfMatch` set to the ETag read in pass 1.
- **Conflict (`412 PreconditionFailed`, pass 2):** the script writes nothing for that provider and reports it. It continues with the other providers. It exits non-zero at the end if any provider failed. A re-run is safe.
- **Other write errors (pass 2):** any other error on one provider's write is handled the same way as a `412`. The script reports it for that provider, continues with the other providers, and exits non-zero (owner ruling, 2026-10-26, plan review round 3; §12). Every provider report shows whether that provider was written.
- **No hash cache** (author's call). Every run hashes the stored objects again, so a cached value can never stand in for the stored bytes. The cost is time: a full run reads 13.68 GB in 4824 objects. The load freeze in §7 makes a conflict unlikely.
- **Report:** per provider, the count of entries changed and unchanged, every failure, and the raw type of every id value read (§5.3). The report starts with the sha256 of the script file itself (§7). A dry-run prints the same report.
- **Second run:** with nothing changed, the second run changes no entry and no `updated_at`.

The live `--apply` run writes to the production bucket. It needs the owner's explicit go (§7).

### 5.10 API (no handler change)

`list_matches` returns `{**matches, "matches": match_list}` as stored, after the tier filter and the query filters. So the new fields reach clients with no handler change. `apply_filters` reads only `updated_at` and `date`. No Lambda redeploy is needed.

### 5.11 Live verification

New script `scripts/verify_match_index_fields.py`. It reads through the HTTP API with the owner token (`$PINING_FOR_THE_DATA_TOKEN`), so it checks what consumers see.

For every entry of every provider:

- The four fields equal the values that the provider's `match_info` gives for the metadata artifact (fetched through the API).
- `artifact_meta` keys equal `artifacts` keys.
- Each `size` equals the object size from a one-byte Range GET (`Content-Range` total). S3 answers a Range GET on a zero-byte object with `416 InvalidRange`. On a `416`, the script fetches the object without a Range header and accepts size 0 only if the body is empty. Any other result is a failure. No live object is zero bytes (checked 2026-10-26).
- The metadata match id equals the entry `id` (§5.2).
- With `--full-hash`: download every artifact and check `sha256`. This reads 13.68 GB, so it is a flag and not the default.

API Gateway throttles at 10 requests per second sustained (api-reference.md, "Rate Limits"). A full run makes about 6100 API calls (1307 metadata fetches and 4824 size checks). The script keeps its own rate below that limit and retries `429` and `5xx` with backoff.

One cross-provider script instead of a change to each `verify_*_load.py` is the author's call. It can run after any load.

### 5.12 Documentation

- `docs/api-reference.md`, "List Matches": a response example with the new fields, a field table for every entry field, the D4 sentence, and the §5.5 text in place of today's line 113. "List Players": the §5.5 player contract.
- `docs/decisions/0013-entry-change-contract-and-artifact-content-identity.md` (D10), in the format of `docs/decisions/README.md`, plus a row in its index. Context: consumers need to know when an entry changed and whether an artifact's bytes changed. The old contract ("set on every write") came from `docs/superpowers/specs/2026-05-02-private-data-tier.md:159` and `:641`, which deferred per-artifact SHA-256 "until a consumer asks". Lakehouse v2 is that consumer, so the deferral's own condition is met. Decision: §5.4, §5.5 and the reproducible staging of §5.8. Alternatives to record: `updated_at` on every write (the old contract); S3 ETags as content identity (multipart ETags are not content hashes); S3 additional checksums (a multipart upload gives a composite SHA-256, not the hash of the object bytes). See Also: this spec, ADR 0010 (honesty rule).
- `schemas/matches.schema.json`: regenerate with `scripts/regenerate_schemas.py`. The drift test covers it.
- `CHANGELOG.md` `[Unreleased]`: the new fields, the `updated_at` contract change, the sha256 guidance, and the one-time hash change of StatsBomb `.json.gz` objects on their next re-upload (§5.8).
- `docs/context/scripts-and-migrations.md`: add the backfill script to the completed-migrations audit trail after the live run, with the sha256 of the script file used for `--apply` (§7). Add the verify script to the shared scripts. Replace "idempotent and safe to re-run" with the §5.8a treatment of each completed migration.
- `docs/context/formats-and-tiers.md`: add the Gradient Sports metadata reader (`formats/gradientsports.py`) to the format-family list.
- `AGENTS.md`: its `src/formats/` and `scripts/` bullets (`AGENTS.md:9` and the `scripts/` line) describe roles, not single modules, so no change is expected. The builder checks this, and `src/tests/test_agents_md_budget.py` gates any change.
- The `final-review` skill runs before the commit (AGENTS.md) to find other places that describe the entry shape (for example `docs/tutorial.md`).

## 6. Tests

All fixtures are invented. No real provider id, name or (id → entity) tuple.

**Model (`MatchEntry`, `ArtifactMeta`)**

- Each of the four fields is accepted when absent and when valid.
- An id that fails the path-param regex is rejected.
- `artifact_meta` with a key set different from `artifacts` is rejected.
- A bad `sha256` (wrong length, uppercase, non-hex) or a negative `size` is rejected.
- An entry without the new fields still validates (backward compatibility).

**Value rules (§5.3)**

- `int` id → decimal string. `str` id verbatim.
- `float`, `bool`, `list` and `dict` ids raise.
- `None` and `""` → absent.
- A missing id never takes the name value, and a missing name never takes the id value.

**Readers (one test group per family)**

- Each `match_info` returns the four values from an invented fixture in that family's real shape.
- Gradient Sports: `season_name` equals the metadata `season` verbatim, and `season_id` is absent.
- Gradient Sports: the new `formats/gradientsports.match_info` gives the same `date`, `home` and `away` as the old inline loader code for the same fixture.
- Gradient Sports: `match_info` returns the match id from `[0].id`. The loader fails loudly when `[0].id` differs from the file stem.
- Each reader returns the match id from its §5.2 match-id path.
- A fixture with a missing source value gives an absent field.
- Public SkillCorner: zero keys or more than one key ending in `_match` fails loudly.

**Upload CLI (`upload_game`)**

- A round trip: the CLI flags reach the stored entry.
- `artifact_meta` hashes and sizes equal the uploaded bytes.
- A no-op re-upload leaves `updated_at` and the hashes unchanged.
- A content change (one artifact byte) changes the hash and `updated_at`.
- A field change (for example `season_name`) changes `updated_at`.
- A refused value (for example a `float` id, or an id that fails the regex) leaves the stored objects and the index unchanged: no S3 write happens.
- A stored field that the new entry does not carry is dropped, and the drop counts as a change.

**Loaders and staging**

- Each of the 8 loaders passes the four values from its reader to `upload_game`.
- Stage twice, bytes equal: for each of the 8 loaders, staging the same invented source twice gives byte-identical staged files.
- Both `_gzip_file` functions write a header with `mtime` 0 and no file name.

**Other writers (§5.8a)**

- `backfill_skillcorner_opendata_lfs.py`: a repaired object updates that entry's `artifact_meta` (hash of the new bytes) and `updated_at`, through an `IfMatch` write. An entry whose objects it does not replace is unchanged. A repaired entry that had no `artifact_meta` gets a complete one (every artifact key, each hash equal to the stored bytes) and passes `MatchEntry` validation. A `412` on the index write leaves `matches.json` unchanged, reports the replaced keys with their new `sha256`, and exits non-zero.
- `migrate_skillcorner_tracking_parquet.py`: it refuses to convert an entry that carries `artifact_meta`.

**`upload_players`**

- A no-op re-upload of a record leaves its `updated_at` unchanged.
- A field change on a record changes its `updated_at`.
- A new record gets a new `updated_at`.

**Backfill**

- It sets the four fields and `artifact_meta` from the stored objects.
- `sha256` and `size` equal the stored bytes.
- It sets `updated_at` only on changed entries. A second run changes nothing.
- It copies every other field unchanged.
- A metadata match id that differs from the entry id fails loudly, for every provider including `gradientsports`.
- A dry-run writes nothing. Its report lists the raw type of every id value, and a `float` id stops the run.
- Under `--apply`, a per-entry error in one provider (a `float` id, a match-id mismatch, a missing stored object) stops the run before any write: no provider is written, and the exit status is non-zero.
- The write uses `IfMatch`.
- A `412` on one provider: that provider is reported and not written, the other providers are still processed and written, and the exit status is non-zero.
- Any other write error in pass 2 on one provider: the same result as a `412`, and every report shows whether its provider was written.
- Every `gradientsports` entry has `season_name` equal to the metadata `season` and no `season_id`.
- Parity: for one invented fixture per provider and tier, the entry that `upload_game` builds and the entry that the backfill builds have equal four fields and equal `artifact_meta`.

**List equals metadata (the request's contract check)**

- For each provider and tier, an invented end-to-end case: upload, then call the `list_matches` handler (with a stubbed S3 client). The listed values equal the values in the stored metadata artifact. Going through the handler also proves the §5.10 claim that no handler change is needed.
- The verify script's comparison logic has unit tests on invented data, including the `416` case for a zero-byte object and a `--full-hash` `sha256` or `size` mismatch.

**Schema**

- `test_schemas.py` drift test passes after the regeneration.

**Quality gates (shift left):** `ruff`, `pyright` and the full `pytest` suite are green before the commit request.

## 7. Operations sequence and gates

1. Build and test on the feature branch.
2. A/B implementation reviews. Fix or answer every finding.
3. **Gate (owner go):** run the backfill as a dry-run. Show the owner the per-provider report.
4. **Gate (owner go):** run the backfill with `--apply` against the live bucket. Record the sha256 of the script file from the report.
   **Load freeze** starts here. No loader and no other writer (§5.8a) runs against the live bucket until step 8. Reason: the old `upload_game` on `main` replaces whole entries, so it would drop the four fields and `artifact_meta` and set a new `updated_at`.
5. Run `scripts/verify_match_index_fields.py`, once with `--full-hash`. Report the results in full.
6. Run `final-review`. Update `docs/context/scripts-and-migrations.md` with the applied migration and the script sha256. If the backfill script changes after step 4, run the changed script as a dry-run. It must report zero changed entries. Then record the new sha256. The committed script must have the recorded sha256.
7. **Gate (owner approval):** show the diff and the file list. The request names the commit, the push and the PR. Each happens only after an explicit yes that covers it. One commit only.
8. After the merge: run `scripts/verify_match_index_fields.py` again (without `--full-hash`) and report the result. The load freeze ends when it passes.

The backfill runs before the commit (D11), so the commit records an applied, verified migration.

## 8. Left to the builder

- Module and function names, except where §5 names them.
- The hash chunk size and the backfill parallelism. The S3 client must be thread-safe in the pattern chosen.
- Whether `upload_game` skips the index write for an unchanged entry.
- The exact wording of the api-reference field table, provided it covers every field and contains the D4 sentence and the §5.5 text.

## 9. Notes for the consumer (lakehouse v2)

- IDSSE metadata gives a season id (`SeasonId`). Gradient Sports is the provider without one.
- `artifact_meta.sha256` lets a client skip a download when the bytes did not change.
- A byte-identical re-upload no longer changes `updated_at`.

## 10. Review round 1 — disposition

| Finding | Disposition | Where |
|---|---|---|
| A MCS-SPEC-01 (non-reproducible gzip staging) | Fixed: fixed gzip header in both `_gzip_file` functions, stage-twice test for all 8 loaders, one-time effect stated | §5.8, §5.12, §6 |
| A MCS-SPEC-02 (objects written before validation) | Fixed: new order, validation before any S3 write, remaining window and its detection named, refusal test | §5.7, §6 |
| A MCS-SPEC-03 (other writers; v0.8.1 path) | Fixed: every writer listed with its treatment; v0.8.1 path corrected | §5.7, §5.8a, §5.12, §6 |
| A MCS-SPEC-04, B B-SPEC-01 (Gradient match-id guard) | Fixed: match-id path per provider, checked live on 1307 entries; Gradient `[0].id`; tests | §5.2, §5.6, §5.9, §6 |
| B B-SPEC-02 (backfill failure paths) | Fixed: 412 behaviour defined and tested; hash cache removed | §5.9, §6 |
| A MCS-SPEC-05 (StatsBomb type claim) | Fixed: claim corrected; dry-run reports raw id types | §5.3, §5.9, §6 |
| A MCS-SPEC-06 (`_match` key count) | Fixed | §5.2, §6 |
| A MCS-SPEC-07 (IfMatch conflict test) | Fixed (with B-SPEC-02) | §6 |
| A MCS-SPEC-08, B B-SPEC-03 (old-code loads after apply) | Fixed: load freeze from apply until a post-merge verify passes | §7 |
| A MCS-SPEC-09 (approval scope) | Fixed: the request names commit, push and PR | §7 |
| A MCS-SPEC-10 (script provenance) | Fixed: script sha256 recorded and checked | §5.9, §5.12, §7 |
| B B-SPEC-04 (416 on zero-byte objects) | Fixed: defined handling; no live zero-byte object | §5.11, §6 |
| B B-SPEC-05 (cite the old contract) | Fixed in the ADR 0013 content | §5.12 |
| B B-SPEC-06 (inventories) | Fixed: `formats-and-tiers.md` named; `AGENTS.md` checked | §5.12 |
| B B-SPEC-07 (parity test) | Fixed | §6 |
| B B-SPEC-08 (extra fields) | Fixed: stated; no live entry has extra fields | §5.7, §6 |
| B B-SPEC-09 (IDSSE reader name) | Fixed | §5.6 |
| B B-SPEC-10 (what "list" means) | Fixed: the `list_matches` handler | §6 |
| A "could not verify" D6–D8 | The owner made D5–D11 in the design session on 2026-10-26 | §4 |

## 11. Review round 2 — disposition

Both reviewers approved round 2.

| Finding | Disposition | Where |
|---|---|---|
| A and B: `upload.py:105-117` imprecise | Fixed: `upload.py:103-115` | §5.7 |
| A and B: self-heal sweep wording imprecise | Fixed: names the two fixed legacy object names (`:79-84`) | §5.8a |
| B B-SPEC-11 (LFS repair on an entry without `artifact_meta`) | Fixed: the script hashes every stored object of that entry and writes a complete `artifact_meta`; test added | §5.8a, §6 |

## 12. Plan review — spec changes

The plan review (rounds 1 and 2) found five items that change or complete this spec. The other plan-review findings changed only the plan.

| Finding | Disposition | Where |
|---|---|---|
| A MCS-PLAN-01, B B-PLAN-02 (per-entry error under `--apply`) | Owner ruling, 2026-10-26: two passes. Any per-entry error in pass 1 stops the run before any write. A `412` in pass 2 stops only that provider. | §5.9, §6 |
| A MCS-PLAN-02, B B-PLAN-01 (owner approval not recorded) | Fixed: the status line records the owner's approval on 2026-10-26 | header |
| A MCS-PLAN-03, B B-PLAN-06 (`--full-hash` comparison test) | Fixed: a test for a `sha256` and a `size` mismatch | §6 |
| B B-PLAN-05 (LFS `412` after the objects are replaced) | Fixed: report and exit status defined; the backfill recomputes `artifact_meta` (author's call) | §5.8a, §6 |
| B B-PLAN-08, round 2; A MCS-PLAN-08, round 3 (pass-2 write errors other than `412`) | Owner ruling, 2026-10-26: handled as a `412`; the other providers are still written; every report shows `written` | §5.9, §6 |
