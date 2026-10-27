# Match Competition/Season Fields and Artifact Content Identity — Implementation Plan

> **For agentic workers:** execute inline with superpowers:executing-plans. The owner's rules forbid subagents, worktrees and per-task commits. All work stays on branch `feat/match-competition-season-artifact-meta`. There is exactly **one** commit, at Task 15, after the owner's explicit approval. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every `{provider}/matches.json` entry carries the source's competition and season values and the sha256/size of every stored artifact. `updated_at` changes only when a record really changes.

**Architecture:** The pure readers in `src/formats/` return each match's id and a `CompetitionSeason`, using one set of value rules. `upload_game` validates everything before its first S3 write. It hashes the staged files and keeps `updated_at` when the record is unchanged. A one-shot backfill applies the same readers and hashing to the stored objects. A cross-provider verify script checks the live API.

**Tech Stack:** Python 3.12, pydantic v2, boto3, pytest, ruff, pyright (basic), hatch, uv.

**Spec:** `docs/superpowers/specs/2026-10-26-match-competition-season-and-artifact-identity-design.md` (approved by reviewers A and B in round 2 and by the owner). Read it with this plan. Section references (§) below point to the spec.

## Global Constraints

- Field names: `competition_id`, `competition_name`, `season_id`, `season_name`, `artifact_meta` (D1, D6).
- Id fields match `^[a-zA-Z0-9][a-zA-Z0-9_-]*$`, max 128 characters (`_PATH_PARAM_RE` in `src/canonical/models.py`).
- `ArtifactMeta.sha256` matches `^[0-9a-f]{64}$`; `ArtifactMeta.size` is an `int >= 0`.
- Value rules (§5.3): an `int` id becomes its decimal string; a non-empty `str` is kept verbatim (no stripping); `None` or `""` means absent; `float`, `bool`, `list` and `dict` raise `ValueError`. A name accepts only a non-empty `str`, `None` or `""`.
- Gradient Sports: `season_name` = metadata `[0].season` verbatim; `season_id` is never set (D3).
- Never copy a name into an id or an id into a name (D2).
- `updated_at` changes only when the record changes, `updated_at` excluded, for match entries and player records (D5, D7, D8).
- Hash the bytes stored in S3. Never use ETags or S3 checksums (§5.4).
- The gzip header in staging has `mtime=0` and no file name (§5.8).
- The schema `$id` stays `urn:pining-for-the-data:schema:matches:v1`.
- Test fixtures are wholly invented: no real provider id, name or (id → entity) tuple. Do not use the competition values in `src/tests/fixtures/sample_match.json` for new assertions.
- No operator-local paths in committed files. Shell examples use `$PINING_BUCKET` and `$PINING_FOR_THE_DATA_TOKEN`.
- Run tools with `uv run --no-sync` (Windows; plain `uv run` can fail on reinstall).
- No commit, push or PR without the owner's explicit yes (Task 15). No live S3 write without the owner's explicit go (Task 14).

## Review Focus

These five input classes are not exercised by the spec's §6 list. Each one has its own test in the task that owns the code.

1. Non-ASCII and punctuation in names (`"1. FC Ünïon"`, `"Women's Cup"`). They must round-trip verbatim through upload, index JSON and the `list_matches` handler. → Task 13, `test_unicode_names_round_trip_verbatim`.
2. Ids that become strings but fail the regex: a negative `int` (`-5` → `"-5"`) or a string with a space (`"A B"`). The upload must refuse them before any S3 write, with an error that names the field. → Task 4, `test_negative_int_id_refused_before_any_write`.
3. A backfill entry whose stored artifact object is missing (`NoSuchKey`). The run stops after pass 1: the error is reported, no provider is written, and the exit status is `1` (owner ruling, spec §5.9). → Task 10, `test_missing_object_stops_run_before_any_write`.
4. Hashing across chunk boundaries, and the empty file. The streamed hash equals `hashlib.sha256(whole)`. An empty file gives `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` with size 0. → Task 3, `test_hash_stream_chunk_boundaries_and_empty`.
5. Dot-files in a staging directory (`.DS_Store`). They appear in neither `artifacts` nor `artifact_meta`, and they are not uploaded. → Task 4, `test_dotfiles_excluded_from_artifacts_and_meta`.

---

### Task 1: Value rules and `CompetitionSeason`

**Files:**
- Create: `src/formats/competition_season.py`
- Test: `src/tests/test_competition_season.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class CompetitionSeason: competition_id: str | None = None; competition_name: str | None = None; season_id: str | None = None; season_name: str | None = None`
  - `CompetitionSeason.as_kwargs(self) -> dict[str, str | None]` (keys exactly the four field names)
  - `native_id(value: object, field: str) -> str | None`
  - `native_name(value: object, field: str) -> str | None`
  - `skillcorner_v3_competition_season(meta: Mapping[str, Any]) -> CompetitionSeason` (reads the §5.2 SkillCorner paths; a missing `competition_edition`, `competition` or `season` gives absent fields)

- [ ] **Step 1: Write the failing tests** in `test_competition_season.py`:
  - `test_native_id_int_becomes_decimal_string`: `native_id(61, "competition_id") == "61"`.
  - `test_native_id_str_verbatim`: `native_id("SYN-COM-01", "x") == "SYN-COM-01"`; `native_id(" A1", "x") == " A1"` (no stripping; the model refuses it later).
  - `test_native_id_absent_values`: `native_id(None, "x") is None`; `native_id("", "x") is None`.
  - `test_native_id_refuses_other_types`: `pytest.raises(ValueError, match="competition_id")` for each of `49.0`, `True`, `False`, `[1]`, `{"a": 1}`.
  - `test_native_name_rules`: `"Synthetic League"` verbatim; `None` and `""` give `None`; `5`, `5.0`, `True` and `["x"]` raise `ValueError`.
  - `test_skillcorner_v3_reads_only_its_own_paths`: invented meta `{"competition_edition": {"id": 4001, "competition": {"id": 301, "name": "Synthetic Premier"}, "season": {"id": 701, "name": "2099/2100"}}}` → `CompetitionSeason("301", "Synthetic Premier", "701", "2099/2100")`.
  - `test_roles_never_cross`: meta with `competition.name` present and `competition.id` absent → `competition_id is None` and `competition_name == "Synthetic Premier"`; the same check for the season pair.
  - `test_as_kwargs_keys`: `set(CompetitionSeason().as_kwargs()) == {"competition_id", "competition_name", "season_id", "season_name"}`.
- [ ] **Step 2:** Run `uv run --no-sync pytest src/tests/test_competition_season.py -v`. Expected: FAIL (`ModuleNotFoundError: formats.competition_season`).
- [ ] **Step 3:** Implement the Interfaces above. Check `bool` before `int`, because `bool` is a subclass of `int`. Every error message names the field.
- [ ] **Step 4:** Run the same command. Expected: PASS.

### Task 2: `MatchEntry` fields, `ArtifactMeta`, schema

**Files:**
- Modify: `src/canonical/models.py` (`MatchEntry`, new `ArtifactMeta`)
- Modify: `schemas/matches.schema.json` (regenerated, not hand-edited)
- Test: `src/tests/test_match_entry_competition_and_artifact_meta.py`

**Interfaces:**
- Produces: `class ArtifactMeta(BaseModel): sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$"); size: int = Field(..., ge=0)`. `MatchEntry` gains `competition_id: str | None` (path-param pattern, `max_length=128`), `competition_name: str | None` (`min_length=1`), `season_id: str | None` (as `competition_id`), `season_name: str | None` (`min_length=1`), and `artifact_meta: dict[str, ArtifactMeta] | None`. A new `model_validator(mode="after")` raises when `artifact_meta is not None` and `set(artifact_meta) != set(artifacts)`.

- [ ] **Step 1: Write the failing tests:**
  - `test_all_new_fields_optional`: an entry with only `id`, `artifacts`, `visibility`, `updated_at` validates, and `model_dump(exclude_none=True)` has none of the five new keys.
  - `test_valid_new_fields_accepted`: invented values `"301"`, `"Synthetic Premier"`, `"701"`, `"2099/2100"` plus `artifact_meta={"tracking": {"sha256": "0"*64, "size": 0}}` with `artifacts={"tracking": "tracking.parquet"}`.
  - `test_id_regex_enforced`: `competition_id` and `season_id` values `"-5"`, `"A B"`, `"_x"` and `"a"*129` each raise `ValidationError`.
  - `test_empty_name_rejected`: `competition_name=""` raises `ValidationError` (absent is the only "no value").
  - `test_artifact_meta_key_set_must_equal_artifacts`: one key missing raises, and one extra key raises.
  - `test_artifact_meta_bad_values`: `sha256` with 63 characters, with uppercase `"A"*64`, or with `"g"*64`, and `size=-1`, each raise.
- [ ] **Step 2:** Run `uv run --no-sync pytest src/tests/test_match_entry_competition_and_artifact_meta.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement the model changes. Then run `uv run --no-sync python scripts/regenerate_schemas.py`.
- [ ] **Step 4:** Run `uv run --no-sync pytest src/tests/test_match_entry_competition_and_artifact_meta.py src/tests/test_schemas.py src/tests/test_match_entry_format_version.py -v`. Expected: PASS (the drift test agrees with the regenerated schema).

### Task 3: Hashing and record-change helpers

**Files:**
- Create: `src/mock_api/artifact_meta.py`, `src/mock_api/index_records.py`
- Test: `src/tests/test_artifact_meta.py`, `src/tests/test_index_records.py`

**Interfaces:**
- Produces (`artifact_meta.py`):
  - `CHUNK_SIZE: int = 8 * 1024 * 1024`
  - `class ArtifactDigest(NamedTuple): sha256: str; size: int`, plus `ArtifactDigest.as_dict(self) -> dict[str, str | int]`
  - `hash_stream(stream: BinaryIO, chunk_size: int = CHUNK_SIZE) -> ArtifactDigest`
  - `hash_file(path: Path) -> ArtifactDigest`
  - `artifact_name(filename: str) -> str` (`filename.split(".", 1)[0]`, the rule `upload.py` uses today)
  - `staged_files(game_dir: Path) -> list[Path]` (sorted; regular files only; names starting with `.` excluded)
  - `staged_artifact_meta(game_dir: Path) -> dict[str, dict[str, str | int]]`
- Produces (`index_records.py`):
  - `same_record(new: Mapping[str, Any], stored: Mapping[str, Any] | None) -> bool` (compares with `updated_at` removed from both; `stored is None` → `False`)
  - `carry_updated_at(new: dict[str, Any], stored: Mapping[str, Any] | None) -> dict[str, Any]` (returns a copy; when `same_record`, it takes `stored["updated_at"]`; otherwise `new` is unchanged)

- [ ] **Step 1: Write the failing tests:**
  - `test_hash_stream_chunk_boundaries_and_empty` (Review Focus 4): for payload sizes 0, 1, 7, 8, 9 and 1000 with `chunk_size=8`, `hash_stream(io.BytesIO(p), 8) == (hashlib.sha256(p).hexdigest(), len(p))`. The empty payload gives `e3b0c442…b855` with size 0.
  - `test_hash_file_matches_bytes`: writes bytes to `tmp_path` and compares with `hashlib`.
  - `test_staged_files_skips_dotfiles_and_dirs`: `tmp_path` holds `a.json`, `.DS_Store` and `sub/`; the result is `[tmp_path / "a.json"]`.
  - `test_staged_artifact_meta_keys_use_artifact_name`: `tracking.jsonl.bz2` gives key `tracking`.
  - `test_same_record_ignores_updated_at_only`: entries that differ only in `updated_at` → `True`. A changed `season_name`, a changed `artifact_meta.tracking.sha256`, or a key present on one side only → `False`.
  - `test_carry_updated_at`: same record → the stored timestamp; changed record → the new timestamp; `stored=None` → the new timestamp.
- [ ] **Step 2:** Run `uv run --no-sync pytest src/tests/test_artifact_meta.py src/tests/test_index_records.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement. `hash_stream` reads with `stream.read(chunk_size)` until it gets `b""`.
- [ ] **Step 4:** Run the same command. Expected: PASS.

### Task 4: `upload_game` — new fields, validation first, `updated_at` contract

**Files:**
- Modify: `src/mock_api/upload.py` (`upload_game`, `_build_match_entry`, `_update_matches_json`, `main`)
- Test: `src/tests/test_upload.py` (add class `TestCompetitionAndArtifactMeta`)

**Interfaces:**
- Consumes: Task 1 `native_id`, `native_name`; Task 2 `MatchEntry`; Task 3 `staged_files`, `artifact_name`, `staged_artifact_meta`, `carry_updated_at`.
- Produces: `upload_game(..., format_version: int | None = None, competition_id: str | None = None, competition_name: str | None = None, season_id: str | None = None, season_name: str | None = None) -> list[str]`. The existing parameters keep their names and order. CLI flags: `--competition-id`, `--competition-name`, `--season-id`, `--season-name`.

The new order inside `upload_game` (§5.7):
1. Validate the params, list `staged_files`, build the `artifacts` map and `staged_artifact_meta`, and build and validate the entry. A refusal raises here, before `boto3.client` is used for any write.
2. Run the tier-mixing check.
3. Upload the objects.
4. `_update_matches_json` reads the index, applies `carry_updated_at(entry, stored)` and writes.

- [ ] **Step 1: Write the failing tests** (mocked boto3, the same style as `TestUploadGame`):
  - Rewrite the existing `test_reupload_refreshes_updated_at_even_if_unchanged` (`test_upload.py:244`). Its name encodes the old contract, which D7 reverses. Rename it `test_reupload_adding_artifact_meta_refreshes_updated_at`. Its docstring states that the stored entry predates `artifact_meta`, so the re-upload adds `artifact_meta` and that is a change. Keep its assertion (`updated_at != "2025-01-01T00:00:00Z"`) and add `"artifact_meta" in` the written entry.
  - `test_flags_reach_stored_entry`: the four values appear in the `matches.json` body passed to `put_object`.
  - `test_artifact_meta_equals_uploaded_bytes`: for each staged file, the index `artifact_meta[name]` equals the `hashlib` digest and size of that file.
  - `test_noop_reupload_keeps_updated_at_and_hashes`: the stored index already holds the identical entry with `updated_at="2001-01-01T00:00:00Z"`. After the re-upload, the written entry has that same `updated_at` and the same `artifact_meta`.
  - `test_content_change_updates_hash_and_updated_at`: one staged byte differs from the stored hash → a new `sha256` and `updated_at != "2001-01-01T00:00:00Z"`.
  - `test_field_change_updates_updated_at`: only `season_name` differs → a new `updated_at`.
  - `test_refused_value_writes_nothing`: `competition_id="49.0"` passes as a string but fails the regex; and `upload_game(..., competition_id=" A")` likewise. `pytest.raises(ValidationError)`, and `mock_s3.upload_file.call_count == 0` and `mock_s3.put_object.call_count == 0`.
  - `test_negative_int_id_refused_before_any_write` (Review Focus 2): `native_id(-5, …)` gives `"-5"`, and `upload_game(..., season_id="-5")` raises with `season_id` in the message. No `upload_file` and no `put_object` call happens.
  - `test_dropped_stored_field_counts_as_change`: the stored entry carries `"legacy_note": "x"`; the re-upload writes an entry without it and with a new `updated_at`.
  - `test_dotfiles_excluded_from_artifacts_and_meta` (Review Focus 5): `.DS_Store` in the staging dir → not uploaded and not a key in `artifacts` or `artifact_meta`.
  - `test_cli_flags_parse`: `main()` with `sys.argv` carrying the four flags calls `upload_game` with them (patch `mock_api.upload.upload_game`).
- [ ] **Step 2:** Run `uv run --no-sync pytest src/tests/test_upload.py -v`. Expected: the new tests FAIL; the existing tests PASS.
- [ ] **Step 3:** Implement the order above. `_build_match_entry` takes the four values (through `native_id` / `native_name`) and `artifact_meta`. Keep the docstring and comments accurate: the "BEFORE any S3 index write" comment becomes "before any S3 write".
- [ ] **Step 4:** Run `uv run --no-sync pytest src/tests/test_upload.py -v`. Expected: all PASS. Name the rewritten test in the Task 14 report.

### Task 5: `upload_players` — `updated_at` contract

**Files:**
- Modify: `src/mock_api/upload_players.py` (module docstring line 14 and the merge loop)
- Test: `src/tests/test_upload_players.py`

**Interfaces:**
- Consumes: Task 3 `carry_updated_at`.

- [ ] **Step 1: Write the failing tests:** `test_noop_reupload_keeps_player_updated_at` (the stored record equals the incoming record except `updated_at` → the stored `updated_at` is kept); `test_changed_player_gets_new_updated_at` (`height` changes); `test_new_player_gets_new_updated_at`.
- [ ] **Step 2:** Run `uv run --no-sync pytest src/tests/test_upload_players.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3:** In the merge loop, set `by_id[id] = carry_updated_at(new, prior)`. Update the docstring to state the D8 contract.
- [ ] **Step 4:** Run the same command. Expected: PASS. No existing test in `test_upload_players.py` asserts a refresh of an unchanged record (checked at `81e00f7`: `test_idempotent_reupload_replaces_existing` changes `nickname` and does not assert `updated_at`), so no existing test is rewritten.

### Task 6: Readers return match id and `CompetitionSeason`

**Files:**
- Create: `src/formats/gradientsports.py`, `src/formats/provider_metadata.py`, `src/tests/fixtures/gradientsports_metadata_synthetic.json`, `src/tests/fixtures/skillcorner_v3_meta_competition_synthetic.json`
- Modify: `src/formats/idsse.py`, `src/formats/skillcorner_bundle.py`, `src/formats/skillcorner_opendata.py`, `src/formats/statsbomb.py`
- Test: `src/tests/test_gradientsports_format.py`, `src/tests/test_provider_metadata.py`, plus additions to `test_idsse_format.py`, `test_skillcorner_bundle_format.py`, `test_skillcorner_opendata.py`, `test_statsbomb_format.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - Every `MatchInfo` dataclass (`idsse`, `skillcorner_bundle`, `statsbomb`, the new `gradientsports`) and `OpenDataMatchInfo` gains `competition: CompetitionSeason = CompetitionSeason()` as the last field. Existing positional construction stays valid.
  - `idsse.parse_match_information(root: ET.Element, source: str = "<bytes>") -> MatchInfo` and `idsse.read_match_information_bytes(data: bytes) -> MatchInfo`. `read_match_information(path)` delegates to them. The four values come from `General@CompetitionId`, `@CompetitionName`, `@SeasonId` and `@Season`.
  - `gradientsports.MatchInfo(match_id: str, date: str, home: str, away: str, competition: CompetitionSeason)` and `gradientsports.match_info(metadata_obj: list | dict) -> MatchInfo`. It unwraps a one-element list. `match_id` = `[0].id`; `date` = `date.split("T", 1)[0]`; `home`/`away` = `homeTeam.name`/`awayTeam.name` (each default `""`, exactly as the loader does today); the competition comes from `competition.id`, `competition.name` and `season` → `season_name`. It never sets `season_id`.
  - `provider_metadata.MetadataIdentity(match_id: str, competition: CompetitionSeason)` (frozen dataclass)
  - `provider_metadata.metadata_artifact_key(provider: str, entry: Mapping[str, Any]) -> str` (`"metadata"`, or for public `skillcorner` the single key ending in `_match`; zero or more than one such key → `ValueError`)
  - `provider_metadata.read_metadata_identity(provider: str, visibility: str, body: bytes) -> MetadataIdentity`. Dispatch: `gradientsports` → `gradientsports.match_info`; `idsse` → `read_match_information_bytes`; `skillcorner` public → `skillcorner_opendata.match_info`; `skillcorner` private → `skillcorner_bundle.match_info`; `statsbomb` → `statsbomb.match_info`. An unknown provider → `ValueError`.

- [ ] **Step 1: Write the invented fixtures.** Gradient Sports: a one-element list with `id: "990001"`, `date: "2099-07-01T18:00:00"`, `homeTeam.name: "Synthetic Rovers"`, `awayTeam.name: "Invented Athletic"`, `competition: {"id": "77", "name": "Synthetic Cup"}`, `season: "2099"`. SkillCorner: a copy of the shape of `skillcorner_bundle_meta_synthetic.json` (invented values) plus the `competition_edition` from Task 1's test.
- [ ] **Step 2: Write the failing tests:**
  - `test_gradient_match_info_values`: `match_id == "990001"`, `competition == CompetitionSeason("77", "Synthetic Cup", None, "2099")`.
  - `test_gradient_season_is_name_only`: `competition.season_id is None` and `competition.season_name == fixture[0]["season"]`.
  - `test_gradient_date_home_away_parity`: for the fixture, `date`, `home` and `away` equal the expressions the old loader used (`metadata_obj.get("date", "").split("T", 1)[0]`, `(metadata_obj.get("homeTeam") or {}).get("name", "")`, and the same for `awayTeam`), computed inline in the test.
  - IDSSE: `test_idsse_competition_from_general` uses `idsse_matchinformation_synthetic.xml` (`CompetitionId="DFL-COM-000099"` etc. are already invented), and `test_idsse_bytes_and_path_agree`.
  - SkillCorner: `test_bundle_match_info_competition` and `test_opendata_match_info_competition` on the new fixture.
  - StatsBomb: `test_statsbomb_match_info_competition` on the conftest metadata (`competition_id` 555555 → `"555555"`, `season_name "2026"`), and `test_statsbomb_float_competition_id_refused` (`49.0` → `ValueError`).
  - `test_missing_source_value_gives_absent_field`: for each family, one fixture variant without the season name → `season_name is None`.
  - `test_metadata_artifact_key`: `{"metadata": …}` → `"metadata"`; public SkillCorner `{"5_match": …, "5_tracking_extrapolated": …}` → `"5_match"`; zero `_match` keys raises; two `_match` keys raises.
  - `test_read_metadata_identity_dispatch`: for each provider/tier, the fixture bytes give the expected `match_id` (int ids as decimal strings) and competition.
- [ ] **Step 3:** Run `uv run --no-sync pytest src/tests/test_gradientsports_format.py src/tests/test_provider_metadata.py src/tests/test_idsse_format.py src/tests/test_skillcorner_bundle_format.py src/tests/test_skillcorner_opendata.py src/tests/test_statsbomb_format.py -v`. Expected: the new tests FAIL.
- [ ] **Step 4:** Implement. SkillCorner modules call `skillcorner_v3_competition_season(meta)`. StatsBomb reads `competition.competition_id`, `competition.competition_name`, `season.season_id` and `season.season_name`.
- [ ] **Step 5:** Run the same command. Expected: PASS, including every pre-existing test in those files.

### Task 7: Reproducible gzip staging

**Files:**
- Modify: `src/mock_api/staging.py:16-19`, `scripts/upload_skillcorner_realmadrid.py:45-48`
- Test: `src/tests/test_staging.py`, `src/tests/test_upload_skillcorner_realmadrid.py`

- [ ] **Step 1: Write the failing tests:** `test_gzip_header_fixed` for each `_gzip_file`: the output bytes 4–7 (MTIME) are `b"\x00\x00\x00\x00"`, and the FLG byte (index 3) has the FNAME bit (`0x08`) clear. `test_gzip_twice_identical`: two calls on the same source give equal bytes.
- [ ] **Step 2:** Run `uv run --no-sync pytest src/tests/test_staging.py src/tests/test_upload_skillcorner_realmadrid.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3:** Replace `gzip.open(dest, "wb")` with `gzip.GzipFile(filename="", mode="wb", fileobj=<dest opened "wb">, mtime=0)` in both functions. Keep the streaming with `shutil.copyfileobj(..., length=1 << 20)`.
- [ ] **Step 4:** Run the same command. Expected: PASS.

### Task 8: Loaders pass the four values; stage-twice tests

**Files:**
- Modify: `scripts/upload_gradient_wc2022.py`, `scripts/upload_idsse_bundesliga.py`, `scripts/upload_skillcorner_opendata.py`, `scripts/upload_skillcorner_raw.py`, `scripts/upload_skillcorner_parquet.py`, `scripts/upload_skillcorner_realmadrid.py`, `scripts/upload_statsbomb_club.py`, `scripts/upload_statsbomb_open.py`, `src/tests/conftest.py`
- Test: `src/tests/test_loader_competition_and_staging.py` (new; one test pair per loader)

**Interfaces:**
- Consumes: Task 6 readers, `CompetitionSeason.as_kwargs()`, Task 3 `staged_artifact_meta`.
- Produces:
  - `conftest.py` fixture `upload_recorder` → `(fake_upload_game, calls)`. `fake_upload_game(**kw)` appends `(staged_artifact_meta(kw["game_dir"]), kw)` and returns `sorted(artifact names)`.
  - `upload_gradient_wc2022._upload_one_match(source_dir: Path, match_id: str, bucket: str) -> None` keeps its signature. It uses `gradientsports.match_info` and raises `ValueError` when `info.match_id != match_id` (the file stem).
  - Extracted per-match functions, so that the tests do not need HF or network access:
    - `upload_skillcorner_raw.upload_match(fetch: Callable[[str], bytes], match_id: str, info: MatchInfo, physical_rows: list[dict], bucket: str) -> None`
    - `upload_skillcorner_parquet.upload_match(fetch: Callable[[str], bytes], match_id: str, info: MatchInfo, physical_bytes: bytes | None, bucket: str) -> None`
    - `upload_skillcorner_opendata.upload_match(fetch: Callable[[str], bytes], match_id: str, info: OpenDataMatchInfo, bucket: str) -> None`
    Each `main()` calls its function in the place of today's inline block, with no other change in behaviour.
  - Every `upload_game(...)` call in the 8 loaders adds `**info.competition.as_kwargs()`.

- [ ] **Step 1: Write the failing tests.** For each loader, two tests with all I/O faked (reuse the patterns of the existing loader tests; invented sources only):
  - `test_<loader>_passes_competition_values`: the recorded `kw` holds the four values that the fixture's reader returns (for Gradient Sports, `season_id` is absent from or `None` in `kw`, and `season_name == "2099"`).
  - `test_<loader>_stages_reproducibly`: run the loader's entry point twice on the same invented source; `calls[0][0] == calls[1][0]`.
  - Entry points: Gradient Sports `_upload_one_match`; IDSSE `upload_all` (patch `fetch_file_listing`, `load_manifest`, `verify_listing`, `download_verified`); SkillCorner raw, parquet and opendata `upload_match`; RM `upload_all` (patch `boto3.client`, `upload_players`); StatsBomb club `upload_bundle` (conftest StatsBomb bundle fixtures; patch `upload_players`); StatsBomb open `_stage_and_upload_open_match`.
  - `test_gradient_stem_mismatch_fails`: the metadata `[0].id` differs from the file stem → `ValueError`, and `upload_game` is not called.
  - Network guard for every `main()` test below: patch `urllib.request.urlopen` with `monkeypatch` to raise `AssertionError("network access")`, so a missed seam fails the test instead of reaching the network.
  - `test_<loader>_main_calls_upload_match_per_match`, for each of SkillCorner raw and parquet. Put a fake `huggingface_hub` module (`HfApi`, `hf_hub_download`) in `sys.modules` with `monkeypatch.setitem`. It serves an invented manifest, inventory and per-match files. Patch `boto3.client`, the module's `upload_match` (a recorder) and the players path. `main` imports these inside its body, so patch them where they are defined: `mock_api.upload_players.upload_players`, `upload_skillcorner_realmadrid.public_player_ids` and `upload_skillcorner_realmadrid.derive_players`. Patch `sys.stdout` with `monkeypatch`, so the UTF-8 rewrap in `main` is undone after the test. Run `main()` with `--apply`. Assert one `upload_match` call per ingestible match id, in manifest order, with the `match_id`, the `info` that the reader returns for that match, and the per-match extra argument (`physical_rows` or `physical_bytes`) that today's inline block passes.
  - `test_opendata_main_calls_upload_match_per_new_match`. The opendata loader uses no `huggingface_hub`. Patch these module attributes with `monkeypatch`: `http_get` (serves an invented `matches.json` index and the invented per-match `_match` files), `live_match_ids` (returns a set that holds one of the invented ids), `private_player_ids` (returns an empty set), `fetch_resolved` (a stub that fails the test if it is called), and `upload_match` (a recorder). Also patch `boto3.client`. `main` imports the players path inside its body, so patch it where it is defined: `mock_api.upload_players.upload_players` (`main` imports it at `:146`) and `upload_skillcorner_realmadrid.derive_players` (`:143`). Patch `sys.stdout` as above. Run `main()` with `--apply`. Assert one `upload_match` call per id in `select_new_matches(discover_match_ids(index), live)`, in that order, and no call for the live id. Each call is `(fetch, match_id, info, bucket)`: `fetch` is the patched `fetch_resolved` object, `info` equals `match_info` of that match's invented file, and there is no extra per-match argument.
  - `test_<loader>_main_dry_run_calls_no_upload_match`, for raw, parquet and opendata: the same setup without `--apply` → zero `upload_match` calls.
- [ ] **Step 2:** Run `uv run --no-sync pytest src/tests/test_loader_competition_and_staging.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement the Interfaces above.
- [ ] **Step 4:** Run `uv run --no-sync pytest src/tests/test_loader_competition_and_staging.py src/tests/test_upload_*.py -v`. Expected: PASS. If a Parquet stage-twice test fails, make that writer deterministic. Do not weaken the test.

### Task 9: Other writers — LFS repair and Parquet-migration guard

**Files:**
- Modify: `scripts/backfill_skillcorner_opendata_lfs.py`, `scripts/migrate_skillcorner_tracking_parquet.py`
- Test: `src/tests/test_backfill_skillcorner_opendata_lfs.py`, `src/tests/test_migrate_skillcorner_tracking.py`

**Interfaces:**
- Consumes: Task 3 `hash_stream`, `ArtifactDigest`; Task 2 `MatchEntry`.
- Produces:
  - `backfill_skillcorner_opendata_lfs.refresh_entries(index: dict, repaired: dict[str, bytes], digest_of: Callable[[str], ArtifactDigest], now: str) -> dict`. It is pure. `repaired` maps a match id to the new tracking bytes. `digest_of(s3_key)` hashes a stored object; it is called only for entries without `artifact_meta`. It returns the new index, with each changed entry validated by `MatchEntry`.
  - The `--apply` path then writes `matches.json` once with `IfMatch=<ETag read before the repair>`.
  - `main() -> int` (today `-> None`), and the module ends with `sys.exit(main())` (today `main()`). The no-repair path and the dry-run path return `0`. A completed `--apply` returns `0`.
  - On a `412` for that write (the objects are already replaced), the script makes no further write. It prints every replaced object key with its new `sha256`, states that the `artifact_meta` of those entries is stale until the §5.9 backfill runs again, and exits `1`. A run of the §5.9 backfill recomputes `artifact_meta` from the stored bytes, because it has no hash cache.
  - In `migrate_skillcorner_tracking_parquet.migrate_match`, the conversion branch raises `ValueError` naming the match id when `entry.get("artifact_meta") is not None`. The `format_version == 2` sweep is unchanged.

- [ ] **Step 1: Write the failing tests:**
  - `test_refresh_sets_hash_of_new_bytes_and_updated_at`: an entry with `artifact_meta` gets `artifact_meta["<id>_tracking_extrapolated"] == hashlib digest of the repaired bytes` and `updated_at == now`. Its other keys are unchanged.
  - `test_refresh_untouched_entry_unchanged`.
  - `test_refresh_entry_without_meta_gets_complete_meta`: `digest_of` is called once for every other artifact key; the result has every key and passes `MatchEntry.model_validate`.
  - `test_apply_writes_index_with_ifmatch` (FakeS3 records `IfMatch`).
  - `test_main_exit_codes`: no pointer found → `main()` returns `0`; a dry-run that finds a pointer → `0` and no write. Patch `sys.stdout` with `monkeypatch`, so the UTF-8 rewrap in `main` is undone after the test.
  - `test_apply_412_after_replace_reports_and_exits_1`: the index ETag changes after the read. The replaced object is in the FakeS3, `matches.json` is unchanged, the output names the replaced key and its new `sha256`, and `main` returns `1`.
  - `test_conversion_refuses_entry_with_artifact_meta`; `test_format_version_2_sweep_still_runs_with_artifact_meta`.
- [ ] **Step 2:** Run `uv run --no-sync pytest src/tests/test_backfill_skillcorner_opendata_lfs.py src/tests/test_migrate_skillcorner_tracking.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the same command. Expected: PASS.

### Task 10: Backfill script

**Files:**
- Create: `scripts/backfill_match_competition_and_artifact_meta.py`
- Modify: `src/formats/provider_metadata.py` (adds `raw_id_values`)
- Test: `src/tests/test_backfill_match_competition_and_artifact_meta.py`, `src/tests/test_provider_metadata.py` (`test_raw_id_values_keep_source_types`: an `int` stays `int`, and a `float` stays `float`)

**Interfaces:**
- Consumes: Task 6 `metadata_artifact_key`, `read_metadata_identity`; Task 3 `hash_stream`, `carry_updated_at`; Task 2 `MatchEntry`.
- Produces:
  - `@dataclass class ProviderReport: provider: str; changed: int = 0; unchanged: int = 0; written: bool = False; error: str | None = None; id_types: Counter[str] = field(default_factory=Counter)`
  - `script_sha256() -> str` (the sha256 of `Path(__file__).read_bytes()`)
  - `object_key(provider: str, entry: Mapping[str, Any], filename: str) -> str` (`{provider}/_private/{id}/{filename}` when private, otherwise `{provider}/{id}/{filename}`)
  - `backfill_entry(s3, bucket: str, provider: str, entry: dict, now: str, id_types: Counter[str]) -> dict`. It raises `ValueError` when the metadata match id differs from `entry["id"]`. It records `type(raw).__name__` for each raw id value read. It copies every other field unchanged. It applies `carry_updated_at`.
  - `@dataclass class PreparedProvider: report: ProviderReport; etag: str | None = None; index: dict[str, Any] | None = None`
  - `prepare_provider(s3, bucket: str, provider: str, now: str) -> PreparedProvider` (pass 1). It reads `matches.json` and its ETag and runs `backfill_entry` on every entry. It never writes. Any exception for any entry (a float id, an id mismatch, a missing object, a `ClientError`) sets `report.error` and leaves `index` as `None`.
  - `write_provider(s3, bucket: str, prepared: PreparedProvider) -> None` (pass 2). One `put_object` of `prepared.index` with `IfMatch=prepared.etag`; on success `report.written = True`. A `ClientError` with code `PreconditionFailed` sets `report.error = "conflict (412)"`. Any other exception sets `report.error = "write failed: <exception type>: <message>"` (spec §5.9 "Other write errors"). `write_provider` never raises.
  - `main() -> int`. Arguments: `--bucket` (default `$PINING_BUCKET`), `--provider` (repeatable; default: every provider in `providers.json`), `--apply`. One `now = utc_now_iso()` per run. It prints `script_sha256()` first. It runs pass 1 for every selected provider. If any report has an `error`, it prints every report and the line `run stopped after pass 1: no provider written`, and returns `1`. Otherwise, with `--apply`, it runs pass 2 for every provider in order, also after an error on an earlier provider. It prints one report per provider, each with its `written` value, and returns `1` if any report has an `error`, else `0`.
  - Owner ruling (2026-10-26, plan review A MCS-PLAN-01 / B B-PLAN-02; spec §5.9): any per-entry error in any provider stops the run before any write. In pass 2, a write error on one provider (a `412` or any other error) stops only that provider; the others are still written. The second part is a separate owner ruling (2026-10-26, plan review round 3, A MCS-PLAN-08).
- Raw id types: `read_metadata_identity` returns normalised strings. So `backfill_entry` reads the raw id values for the `id_types` report through a helper `raw_id_values(provider: str, visibility: str, body: bytes) -> dict[str, object]` added to `provider_metadata.py` (Task 6 file; add its test here).

- [ ] **Step 1: Write the failing tests** with an in-memory S3 double. Extend the `FakeS3` pattern from `test_migrate_skillcorner_tracking.py` with `ETag`, `IfMatch` (a mismatch raises `botocore.exceptions.ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")`) and `NoSuchKey`. Seed two providers with invented entries and objects.
  - `test_sets_four_fields_and_artifact_meta_from_stored_objects`
  - `test_hashes_equal_stored_bytes`
  - `test_updated_at_only_on_changed_entries`: a pre-completed entry keeps its `updated_at`; the others get `now`.
  - `test_second_run_changes_nothing`: the report has `changed == 0`, and the stored index is byte-identical after the second `--apply`.
  - `test_other_fields_copied_unchanged`: `date`, `home`, `away`, `artifacts`, `visibility`, `provenance`, `source`, `format_version` and an extra field `"x_note"` all survive.
  - `test_match_id_mismatch_fails_loudly` (one per provider, including `gradientsports`): `report.error` names the entry id and the metadata match id. Under `--apply`, zero `matches.json` `put_object` calls for any provider, and `main` returns `1`.
  - `test_dry_run_writes_nothing_and_reports_types`: no `put_object`; `id_types` counts `int` and `str`.
  - `test_float_id_stops_run_before_any_write`: provider A is clean; provider B is StatsBomb with metadata `competition_id: 49.0`. Under `--apply`: B's `report.error` is set, zero `put_object` calls for A and for B, the output has `run stopped after pass 1: no provider written`, and `main` returns `1`.
  - `test_put_uses_ifmatch`.
  - `test_412_on_one_provider_others_written`: provider A's ETag changes before its put → A has `error="conflict (412)"` and is not written; provider B is written; `main` returns 1.
  - `test_pass2_other_write_error_reported_others_written`: provider A's `matches.json` `put_object` raises `ClientError` with code `AccessDenied`. A's `report.error` starts with `write failed` and `written` is `False`; provider B is written; the output shows both reports with their `written` values; `main` returns `1`.
  - `test_missing_object_stops_run_before_any_write` (Review Focus 3): provider A is clean; one stored object of provider B is missing. Under `--apply`: zero `put_object` calls for A and for B, and `main` returns `1`.
  - `test_gradientsports_season_name_only`: every backfilled `gradientsports` entry has `season_name == metadata[0]["season"]` and no `season_id`.
  - `test_parity_with_upload_game`: for one invented fixture per provider and tier, run `upload_game` (mocked S3 capturing the index) and `backfill_entry` on the same bytes. The four fields and `artifact_meta` are equal.
- [ ] **Step 2:** Run `uv run --no-sync pytest src/tests/test_backfill_match_competition_and_artifact_meta.py src/tests/test_provider_metadata.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3:** Implement. Stream every object body through `hash_stream`. Never call `.read()` on a whole body.
- [ ] **Step 4:** Run the same command. Expected: PASS.

### Task 11: Live verify script

**Files:**
- Create: `scripts/verify_match_index_fields.py`
- Test: `src/tests/test_verify_match_index_fields.py`

**Interfaces:**
- Consumes: Task 6 `metadata_artifact_key`, `read_metadata_identity`; Task 3 `hash_stream`, `ArtifactDigest`; `scripts/_verify_http.py` `get_json`, `NoFollow`, `parse_content_range_total`.
- Produces:
  - `check_entry(entry: Mapping[str, Any], identity: MetadataIdentity) -> list[str]` (problem strings; empty means pass). It checks the match id, the four fields (an absent field must equal `None` in the identity), and that the `artifact_meta` keys equal the `artifacts` keys.
  - `compare_digest(name: str, expected: Mapping[str, Any], actual: ArtifactDigest) -> list[str]` (problem strings that name the artifact; empty means pass). `expected` is the entry's `artifact_meta[name]`. It reports a `sha256` difference and a `size` difference separately. `main` calls it for every artifact under `--full-hash`, with `actual` from `hash_stream` on the presigned-URL body.
  - `size_from_range(status: int, content_range: str | None, fetch_full: Callable[[], bytes]) -> int`. On `206`, it returns `parse_content_range_total`. On `416`, it calls `fetch_full()` and returns `0` only when the body is empty. Any other result raises `ValueError`.
  - `class RateLimiter(max_per_second: float = 8.0)` with `wait(self) -> None` (an injectable clock for tests).
  - `with_retry(call: Callable[[], T], *, attempts: int = 5, sleep: Callable[[float], None] = time.sleep) -> T`. It retries an `HTTPError` with code 429 or 5xx using exponential backoff (1, 2, 4, 8 s).
  - `main() -> int`. Arguments: `--api` (required), `--owner-token` (default `$PINING_FOR_THE_DATA_TOKEN`), `--provider` (repeatable; default: every provider from `/v1/providers`), `--full-hash`. It prints every problem and returns `1` if any.

- [ ] **Step 1: Write the failing tests:** `test_check_entry_passes_on_equal_values`; `test_check_entry_reports_each_mismatch` (one case per field, the match id, and the key set); `test_absent_field_must_be_absent_in_identity`; `test_compare_digest_equal_passes`; `test_compare_digest_sha_mismatch_reported` (the problem names the artifact and both hashes); `test_compare_digest_size_mismatch_reported`; `test_size_from_range_206`; `test_size_from_range_416_empty_body_is_zero`; `test_size_from_range_416_nonempty_body_fails`; `test_rate_limiter_spacing` (fake clock; 3 calls at 8/s take ≥ 0.25 s of fake time); `test_with_retry_429_then_ok`; `test_with_retry_gives_up_after_attempts`.
- [ ] **Step 2:** Run `uv run --no-sync pytest src/tests/test_verify_match_index_fields.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement. Artifact fetches use the `NoFollow` 302 pattern of `verify_statsbomb_load.py`, then a header-free GET of the presigned URL.
- [ ] **Step 4:** Run the same command. Expected: PASS.

### Task 12: Docs, ADR 0013, CHANGELOG

**Files:**
- Modify: `docs/api-reference.md` ("List Matches" and "List Players"), `CHANGELOG.md` (`[Unreleased]`), `docs/decisions/README.md` (index row), `docs/context/formats-and-tiers.md`, `docs/context/scripts-and-migrations.md`
- Create: `docs/decisions/0013-entry-change-contract-and-artifact-content-identity.md`
- Test: `src/tests/test_agents_md_budget.py` (existing gate)

- [ ] **Step 1:** api-reference "List Matches": add the five new fields to the response example (invented values). Add a field table that covers every entry field. Replace line 113's `updated_at` sentence with the §5.5 replacement text verbatim. Add the D4 sentence verbatim: "season_id absent means the source gives no season id; season_name is the source value verbatim." "List Players": add the player `updated_at` contract (§5.5).
- [ ] **Step 2:** Write ADR 0013 in the README format. Its content is as §5.12 states, including the citation of `docs/superpowers/specs/2026-05-02-private-data-tier.md:159` and `:641`. Add its row to the README index.
- [ ] **Step 3:** CHANGELOG `[Unreleased]`: the new fields; the `updated_at` contract (matches and players); "clients should compare artifact sha256 before downloading"; the one-time hash change of StatsBomb `.json.gz` objects on their next re-upload; the new backfill and verify scripts.
- [ ] **Step 4:** `formats-and-tiers.md`: add the Gradient Sports metadata reader (`formats/gradientsports.py`). `scripts-and-migrations.md`: add the verify script to the shared scripts. Replace "idempotent and safe to re-run" with the §5.8a treatment of each completed migration. Leave a placeholder sentence for the backfill audit entry; Task 14 fills in the script sha256. Check `AGENTS.md:9` and its `scripts/` line: change them only if they name modules (expected: no change).
- [ ] **Step 5:** Run `uv run --no-sync pytest src/tests/test_agents_md_budget.py -v`. Expected: PASS.

### Task 13: Contract tests through the handler

**Files:**
- Test: `src/tests/test_match_index_contract.py` (new)

**Interfaces:**
- Consumes: Task 4 `upload_game`; the `list_matches` handler (`terraform/modules/functions/src/list_matches.py`, imported as `test_lambda_handlers.py` does, with the same `shared` token stubs).
- Type checking: `test_match_index_contract.py` must pass `pyright` clean. It does not join the `[tool.pyright] ignore` list in `pyproject.toml`. Set the `shared` stubs (`_s3_client`, `_get_owner_token`) with `monkeypatch.setattr`, not by direct assignment. `extraPaths` already resolves the handler imports.

- [ ] **Step 1: Write the tests:**
  - `test_list_equals_metadata[<provider>-<tier>]`, parametrised over the five provider/tier pairs. Upload an invented fixture through `upload_game` into a dict-backed S3 double. Serve the captured `matches.json` through `list_matches.handler` with the owner token. Assert that the listed four fields equal `read_metadata_identity(...)` of the stored metadata bytes.
  - `test_unicode_names_round_trip_verbatim` (Review Focus 1): `competition_name="1. FC Ünïon"` and `season_name="Women's Cup 2099"` come back byte-identical from the handler's JSON body.
- [ ] **Step 2:** Run `uv run --no-sync pytest src/tests/test_match_index_contract.py -v`. Expected: PASS. A failure means a defect in Tasks 4 or 6: fix it there.

### Task 14: Quality gate, backfill gates, verify, final review

- [ ] **Step 1: Shift left.** Run `uv run --no-sync ruff check src scripts`, `uv run --no-sync ruff format --check src scripts`, `uv run --no-sync pyright` and `uv run --no-sync pytest`. This is wider than CI (`pyright src/`, `ruff check src/`), because the new backfill and verify scripts live in `scripts/`. Baseline, recorded at `81e00f7` on 2026-10-26: `ruff check` clean; `ruff format --check` clean (84 files); `pyright` 0 errors and 3 warnings, all `reportMissingImports` for `huggingface_hub` (an optional dependency not installed in the dev environment) in `scripts/upload_skillcorner_parquet.py`, `scripts/upload_skillcorner_raw.py` and `src/publish/hf_push.py`. Expected: ruff clean; `pyright` 0 errors and no warning other than those 3; pytest all PASS. Report any failure in full.
- [ ] **Step 2: Gate — owner.** Ask the owner to launch the A/B implementation reviews of the branch. Fix or answer every finding. Take any A/B disagreement to the owner.
- [ ] **Step 3: Gate — owner go for the dry-run.** Run `uv run --no-sync python scripts/backfill_match_competition_and_artifact_meta.py --bucket "$PINING_BUCKET"` with `AWS_PROFILE=devops-agent`. Expected: the script sha256; per provider, `changed` = all entries (`gradientsports` 64, `idsse` 7, `skillcorner` 909, `statsbomb` 327); no error; every id type is `int` or `str`. Show the full report to the owner.
- [ ] **Step 4: Gate — owner go for the apply.** Run the same command with `--apply`. Expected: four providers written, exit 0. Record the script sha256. **The load freeze starts now:** no loader and no §5.8a writer runs against the live bucket until Step 9 passes.
- [ ] **Step 5:** Run `uv run --no-sync python scripts/verify_match_index_fields.py --api "$API_URL" --full-hash`. `$API_URL` is the Terraform output `api_url`, as in api-reference.md. Expected: zero problems, exit 0. Report the result in full.
- [ ] **Step 6:** Run the `final-review` skill. Fill in the `scripts-and-migrations.md` audit entry with the recorded script sha256. If the backfill script changed after Step 4, run it again as a dry-run. Expected: `changed == 0` for every provider. Record the new sha256.
- [ ] **Step 7:** Run Step 1 again. Expected: clean.
- [ ] **Step 8:** Check that `sha256sum scripts/backfill_match_competition_and_artifact_meta.py` equals the recorded sha256.

### Task 15: The one commit (owner gate), then the post-merge verify

- [ ] **Step 1: Gate — owner approval.** Show `git status`, `git diff --stat` and the full diff. The request names three actions: the commit (message below), the push of `feat/match-competition-season-artifact-meta`, and the PR. Each happens only after an explicit yes that covers it.

```
feat: competition/season fields and artifact content identity on match entries

<body: the four fields, artifact_meta, the updated_at contract, the backfill (applied; script sha256), ADR 0013>

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

- [ ] **Step 2:** After the merge, run `scripts/verify_match_index_fields.py` (without `--full-hash`). Expected: zero problems. Report the result to the owner. The load freeze ends when it passes.
- [ ] **Step 3:** Tell the owner the spec §9 consumer notes are ready to relay to the lakehouse session.

---

## Self-review record

- **Spec coverage:** §5.1 → T2. §5.2 and the match-id table → T6. §5.3 → T1, T6. §5.4 → T3, T4, T10. §5.5 → T3, T4, T5, T12. §5.6 → T6. §5.7 → T4, T5. §5.8 (loaders, staging) → T7, T8. §5.8a → T9, T12. §5.9 → T10. §5.10 → T13. §5.11 → T11. §5.12 → T12. §6 → T1–T13. §7 → T14, T15.
- **Interpretations made in this plan:** (a) withdrawn: the owner ruled on the backfill error behaviour (see the disposition below); (b) `upload_game` takes the four values as `str | None` and re-applies the §5.3 rules, so the CLI and the loaders share one path (T4); (c) three SkillCorner loaders get extracted `upload_match` functions, so their per-match path can be tested without HF or network access (T8).

## Plan review round 1 — disposition

Reviewers A and B both returned REQUEST CHANGES with no BLOCKING finding. They did not disagree on any item.

| Finding | Disposition | Where |
|---|---|---|
| A MCS-PLAN-01, B B-PLAN-02 (per-entry backfill error under `--apply`) | Owner ruling, 2026-10-26: two passes; any per-entry error in pass 1 stops the run before any write; a `412` in pass 2 stops only that provider. Spec §5.9 and §6 changed to match. | T10, Review Focus 3, spec §5.9, §6, §12 |
| A MCS-PLAN-02, B B-PLAN-01 (owner approval of the spec not recorded) | Fixed: the spec status line records the owner's approval on 2026-10-26 | spec header |
| A MCS-PLAN-03, B B-PLAN-06 (`--full-hash` comparison test) | Fixed: `compare_digest` and three named tests, including a `sha256` and a `size` mismatch | T11, spec §6 |
| A MCS-PLAN-04, B B-PLAN-03 (`scripts/` lint and type baseline) | Fixed: baseline recorded at `81e00f7` (ruff clean; pyright 0 errors, 3 `huggingface_hub` import warnings); expected result stated against it | T14 Step 1 |
| A MCS-PLAN-05 (new contract test and the pyright ignore list) | Fixed: the new test must type-check clean and does not join the ignore list (the stricter option, so no exemption is widened) | T13 |
| B B-PLAN-04 (old-contract re-upload test exists) | Fixed: the rewrite is named in T4 Step 1. `test_upload_players.py` has no old-contract test (checked). | T4 Step 1, T5 Step 4 |
| B B-PLAN-05 (LFS `412` after the objects are replaced) | Fixed: report and exit `1`; the backfill recomputes `artifact_meta` (author's call); test added | T9, spec §5.8a, §6 |
| B B-PLAN-07 (rewired SkillCorner `main()` functions untested) | Fixed: two `main()` tests per loader (apply and dry-run) | T8 Step 1 |

## Plan review round 2 — disposition

Reviewer A: REQUEST CHANGES (one new SHOULD FIX; round-1 items resolved). Reviewer B: APPROVE (three CONSIDER items open). They did not disagree on any item: A's MCS-PLAN-06 and B's partial B-PLAN-07 are the same defect.

| Finding | Disposition | Where |
|---|---|---|
| A MCS-PLAN-06, B B-PLAN-07 partial (opendata `main()` test used the wrong seams) | Fixed: the opendata test names its own seams (`http_get`, `live_match_ids`, `private_player_ids`, `fetch_resolved`, `upload_match`) and its order (`select_new_matches` over `discover_match_ids`). A `urlopen` guard in every `main()` test fails on any network access. | T8 Step 1 |
| B B-PLAN-08 (pass-2 write errors other than `412`) | Fixed: handled as a `412` (author's call in round 2; replaced by an owner ruling in round 3); `write_provider` never raises; every report shows `written`; test added. Spec §5.9 and §6 changed to match. | T10, spec §5.9, §6, §12 |
| B B-PLAN-09 (LFS `main()` returns `None`, so a `412` exits `0`) | Fixed: `main() -> int` and `sys.exit(main())` declared; exit-code test added | T9 |

## Plan review round 3 — disposition

Reviewer A: REQUEST CHANGES (one SHOULD FIX that needs an owner ruling, one CONSIDER). Reviewer B: APPROVE (nothing open). A and B disagreed on one point: B read the pass-2 "report and continue" rule as consistent with the round-1 ruling, and A read it as an extension that needs the owner. The owner ruled on it.

| Finding | Disposition | Where |
|---|---|---|
| A MCS-PLAN-08 (pass-2 write errors other than `412`; the plan contradicted itself) | Owner ruling, 2026-10-26: continue, as for a `412`. The recorded ruling in T10 now covers both passes; spec §5.9 and §12 cite the ruling, not an author's call. | T10, spec §5.9, §12 |
| A MCS-PLAN-07 (wrong patch target for `upload_players`) | Fixed: every `main()` test patches the players path where `main` imports it from: `mock_api.upload_players.upload_players` and `upload_skillcorner_realmadrid.derive_players` / `public_player_ids`. | T8 Step 1 |
