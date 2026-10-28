# SkillCorner Open Data re-sync — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task (inline execution, one gated commit, as in cycles A
> and B). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pin the public SkillCorner opendata redistribution to one upstream commit,
stamp that commit on every public entry, bring the ten drifted entries' stored bytes up
to the pin with a conditional-write (upsert-only) path, and verify the live store against
the pin from the stored bytes.

**Architecture:** A new `source.revision` field on the shared `_SourceMeta` model; a
`source_revision` pass-through in `upload_game`; a pinned loader; a new re-sync script
`scripts/resync_skillcorner_opendata.py` built on the `backfill_skillcorner_opendata_lfs.py`
pattern (pure core + injected S3/fetch, `If-Match` writes, 412 abort-per-match); a new
verify `scripts/verify_skillcorner_opendata_resync.py` that hashes every stored artifact
against the pin. One gated commit.

**Tech Stack:** Python 3.12, boto3, urllib, pydantic (upload side only), pytest, ruff,
pyright. S3 conditional writes (`PutObject` `If-Match`).

**Spec:** `docs/superpowers/specs/2026-10-27-skillcorner-opendata-resync-design.md`
(approved; A/B rounds 1-3).

## Global Constraints

- Pin: set `UPSTREAM_COMMIT` to `4340d274572876239c154c90bc507a9b3250a656` (SkillCorner/opendata,
  2026-09-14). Replace `master` in the loader's raw host, media host, and index fetch.
- `source.revision` is a 40-hex (`^[0-9a-f]{40}$`), optional, on `_SourceMeta` (shared by
  `MatchEntry` and `PlayerRecord`). Regenerate BOTH `schemas/matches.schema.json` and
  `schemas/players.schema.json` with `scripts/regenerate_schemas.py`; `test_schemas.py`
  gates it.
- `provenance` stays `"redistributed"` verbatim — do not change the string (Hyrum).
- Re-sync writes only `dynamic_events`/`phases_of_play` (and only when sha256 differs);
  a differing `match` or `tracking_extrapolated`, or an entry with no `source` object, is
  reported-and-stopped (no write). Never delete. `If-Match` (ETag) on every PUT; a 412
  aborts that one match and the loop continues. Rollback uses VersionIds, not ETags.
- The entry keeps every live field; only `artifact_meta`, `source.revision`, `updated_at`
  may change. `updated_at` follows ADR 0013 (`carry_updated_at`).
- Verify proves all 80 artifacts of all 20 entries equal the pin FROM STORED BYTES
  (tracking downloaded + hashed, the `--full-hash` bar) and that `artifact_meta` equals
  the stored bytes.
- The re-sync's drift check reads `tracking_extrapolated` as its Git-LFS pointer and compares the
  pointer `oid` (= content sha256) and size to the stored `artifact_meta`; the ~90 MB blob is never
  downloaded or re-PUT by the re-sync (ADR 0015). Only the verify downloads the stored tracking bytes.
- Test fixtures are wholly invented (no licensed id→entity tuple). Live public opendata
  ids appear only where existing scripts already use them.
- One commit per cycle, on `feat/skillcorner-opendata-resync`, after the owner's explicit
  approval of that commit. The live `--apply` write and the release each need a separate
  owner go.
- New scripts using `urllib.request` get an `S310` `per-file-ignores` entry in
  `pyproject.toml`, with the same rationale comment as the sibling scripts.
- Target version: 0.10.0 → **0.11.0**. Gate paths: lint and format `src/` AND both new
  `scripts/*.py` (CI's `ruff check src/` does not cover `scripts/`); the full `uv run
  pytest` suite with a recorded baseline count; `pyright src/`; `pip-audit`;
  `detect-secrets` baseline.

## Review Focus

- A re-sync run that writes a `dynamic_events`/`phases_of_play` whose fresh sha256 equals
  the stored one (no redundant PUT) — Task 3 `test_resync_skips_put_when_sha_matches`.
- A 412 on the second of two artifact PUTs for one match (partial write) — Task 3
  `test_resync_412_mid_match_aborts_and_reports`.
- An entry whose `artifact_meta` lacks a re-syncable key (fallback re-PUT) — Task 3
  `test_resync_missing_artifact_meta_fallback_puts`.
- A stored artifact whose bytes differ from `artifact_meta` (a prior partial write) —
  Task 4 `test_verify_fails_when_stored_bytes_differ_from_artifact_meta`.
- A `source`-less entry reaching the stamp step — Task 3
  `test_resync_entry_without_source_reports_and_stops`.

---

### Task 1: `source.revision` on the model + regenerated schemas

**Files:**
- Modify: `src/canonical/models.py` (the `_SourceMeta` class)
- Modify: `schemas/matches.schema.json`, `schemas/players.schema.json` (regenerated)
- Test: `src/tests/test_match_entry_source_revision.py` (new, matching the
  `test_match_entry_*.py` convention), `src/tests/test_schemas.py` (`test_models.py` does
  not exist — do not create it)

**Interfaces:**
- Produces: `_SourceMeta.revision: str | None` (pattern `^[0-9a-f]{40}$`). Consumed by
  Task 2 (`upload_game`) and Task 3/4.

- [ ] **Step 1: Write the failing tests**

```python
def test_source_revision_accepts_40_hex():
    m = MatchEntry.model_validate({..., "source": {"name": "X", "revision": "a"*40}})
    assert m.source.revision == "a"*40

def test_source_revision_rejects_non_hex():
    with pytest.raises(ValidationError):
        MatchEntry.model_validate({..., "source": {"name": "X", "revision": "zz"}})

def test_source_revision_optional():
    m = MatchEntry.model_validate({..., "source": {"name": "X"}})
    assert m.source.revision is None

def test_source_revision_in_schema():  # in test_schemas.py: drift check after regen
    assert "revision" in matches_schema["$defs"]["_SourceMeta"]["properties"]
    assert "revision" in players_schema["$defs"]["_SourceMeta"]["properties"]
```

- [ ] **Step 2: Run the tests; verify they fail.**
      Expected: the three model tests FAIL (`revision` is not a field);
      `test_source_revision_in_schema` FAILS (schema not yet regenerated).
- [ ] **Step 3: Add the field** to `_SourceMeta`:

```python
revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$",
    description="Upstream VCS commit the redistributed bytes were fetched from (git SHA-1).")
```

- [ ] **Step 4: Regenerate schemas** — `uv run python scripts/regenerate_schemas.py`.
      Both `schemas/*.schema.json` now carry `revision`.
- [ ] **Step 5: Run the tests + `test_schemas.py`; verify they pass.**
- [ ] **Step 6: Run `ruff check src/` and `ruff format --check src/`.**

### Task 2: `upload_game(source_revision=...)` + pin the loader

**Files:**
- Modify: `src/mock_api/upload.py` (`upload_game`, `_build_match_entry`, `main` argparse)
- Modify: `scripts/upload_skillcorner_opendata.py` (`UPSTREAM_COMMIT`, hosts, index fetch,
  `upload_match` passes `source_revision`)
- Test: `src/tests/test_upload*.py`, `src/tests/test_upload_skillcorner_opendata.py`

**Interfaces:**
- Consumes: `_SourceMeta.revision` (Task 1).
- Produces: `upload_game(..., source_revision: str | None = None)` — writes
  `source.revision` only when `source_name` is set (rides the existing `if source_name:`
  gate at `upload.py:177`). `UPSTREAM_COMMIT` constant in the loader. Consumed by Task 3.

- [ ] **Step 1: Write the failing tests**

```python
def test_upload_game_writes_source_revision(tmp_path, fake_s3):
    # staged game + source_name set + source_revision="a"*40 -> entry.source.revision == "a"*40
def test_upload_game_no_revision_without_source_name(...):
    # source_name=None, source_revision set -> no source object, no revision
def test_loader_is_pinned():
    import upload_skillcorner_opendata as m
    assert m.UPSTREAM_COMMIT == "4340d274572876239c154c90bc507a9b3250a656"  # pragma: allowlist secret
    assert "/master/" not in m.RAW_BASE and m.UPSTREAM_COMMIT in m.RAW_BASE
    assert "/master/" not in m.MEDIA_BASE and m.UPSTREAM_COMMIT in m.MEDIA_BASE
```

- [ ] **Step 2: Run the tests; verify they fail.**
- [ ] **Step 3: Implement.** Add `source_revision: str | None = None` to `upload_game`
      and thread it into `_build_match_entry`'s `source` dict beside `name`/`url`/`licence`
      (inside the existing `if source_name:` block). Add `--source-revision` to `main`.
      In the loader, set `UPSTREAM_COMMIT`, build `RAW_BASE`/`MEDIA_BASE`/index fetch from
      it, and pass `source_revision=UPSTREAM_COMMIT` in `upload_match`'s `upload_game` call.
- [ ] **Step 4: Run the tests; verify they pass.**
- [ ] **Step 5: `ruff check` + `ruff format --check` on `src/` and the script.**

### Task 3: the re-sync script

**Files:**
- Create: `scripts/resync_skillcorner_opendata.py`
- Modify: `pyproject.toml` (`per-file-ignores`: `scripts/resync_skillcorner_opendata.py = ["S310"]`)
- Test: `src/tests/test_resync_skillcorner_opendata.py`

**Interfaces:**
- Consumes: `fetch_resolved`, `opendata_files` (loader); `hash_stream`, `ArtifactDigest`,
  `artifact_name` (`mock_api.artifact_meta`); `carry_updated_at`, `utc_now_iso`;
  `MatchEntry`; `UPSTREAM_COMMIT` (Task 2).
- Produces: pure `plan_match(entry, staged_digests)` → a decision object
  (`{to_put: [...], stop_reason: str | None}`); `refresh_entry(entry, new_meta, revision, now)`;
  `preflight_media_sha(fetch)` and `preflight_if_match(s3, bucket, index_key)`.
  `main()` with `--bucket`, `--apply`, optional `--ids` (default: all public opendata
  entries in `skillcorner/matches.json` — all twenty are stamped; only those with a
  differing CSV are re-PUT).

**Index read/write model (per-match, ETag re-read each iteration).** To keep spec §6.3/§7
"a 412 aborts that one match", the index is written **per match**, not batched: each
iteration does a fresh read-modify-write — GET `matches.json` (capturing its current ETag),
modify only that match's entry, then `put_object` with `IfMatch=<that fresh ETag>`. The
ETag is therefore re-read every iteration, so a prior match's own write never makes the
next match `412` (the bug a once-read ETag would cause). A `412` here means a *concurrent
foreign* change and aborts only that match; the loop continues. A batched single index
write was rejected because its `412` would leave all twenty entries unwritten, widening the
abort scope the spec defines. The artifact objects keep their own per-object `If-Match`.

Pattern peer: `scripts/backfill_skillcorner_opendata_lfs.py` for the `If-Match` write, the
412 handling, the injected `digest_of`, and the Range-GET probe — but that script writes
the index once; this one writes per match, re-reading the ETag each time.

- [ ] **Step 1: Write the failing tests** (pure core, injected fakes — a `FakeS3`
      recording PUTs and raising `ClientError` `PreconditionFailed`/`NoSuchKey` on command,
      a fake fetch returning staged bytes). Name one per Review-Focus / spec §6.8:

```
test_resync_skips_put_when_sha_matches
test_resync_puts_when_sha_differs            # If-Match == stored ETag, one PUT
test_resync_412_aborts_only_that_match
test_resync_412_mid_match_aborts_and_reports # partial write reported, loop continues
test_resync_unexpected_match_or_tracking_drift_reports_and_stops
test_resync_missing_artifact_meta_fallback_puts
test_resync_vanished_file_reports_and_keeps  # upstream 404 -> keep, no delete, no stop
test_resync_entry_without_source_reports_and_stops
test_resync_carries_entry_fields             # only artifact_meta/source.revision/updated_at change
test_resync_two_matches_refresh_etag         # 2 matches -> each index write re-reads ETag; match 2 does NOT 412
test_preflight_media_sha_asserts_206         # a non-206 pinned media response raises
test_preflight_if_match_requires_412         # fake S3 returns 200 -> raise; raises 412 -> pass
```

- [ ] **Step 2: Run the tests; verify they fail.**
- [ ] **Step 3: Implement the pure core.** `plan_match`: compare each staged digest to the
      stored `artifact_meta`; mark `dynamic_events`/`phases_of_play` for PUT when the sha
      differs or the key is absent; set `stop_reason` when `match`/`tracking_extrapolated`
      differs, when the entry has no `source`/`source.name`, or on an upstream 404.
      `refresh_entry(entry, new_meta, revision, now)`: deep-copy the live entry, replace
      only the changed `artifact_meta` values, set `source["revision"] = revision` (the
      caller passes `UPSTREAM_COMMIT`), set `updated_at = now`, apply
      `carry_updated_at`, then `MatchEntry.model_validate`; assert no field other than
      `artifact_meta`/`source.revision`/`updated_at` changed, else raise.
- [ ] **Step 4: Run the tests; verify they pass.**
- [ ] **Step 5: Implement the two preflights (spec §9).**
      `preflight_media_sha(fetch)`: a ranged GET of one pinned-SHA `tracking` media URL
      must return HTTP `206`; otherwise raise (the commit-SHA media ref is unconfirmed).
      `preflight_if_match(s3, bucket, index_key)`: GET the index (fresh bytes + ETag), then
      a conditional `put_object` of **those exact bytes** with a deliberately stale
      `IfMatch` must raise `PreconditionFailed` (S3 evaluates the precondition before
      writing, so nothing is written). If it instead returns `200`, the bucket is **not**
      honoring `If-Match` — the whole conditional-write design is unsafe: raise and write
      nothing further. **Name the failure case:** in that `200` path the probe has written a
      new index object version, but from the just-read current bytes, so the content is
      identical (a no-op version, caught by bucket versioning); the preflight then aborts
      the run before any artifact PUT. Both preflights run in the **dry-run**, so a bad ref
      or an unhonored precondition is found before `--apply`, not during it.
- [ ] **Step 6: Implement `main`.** Dry-run: run both preflights, print a per-entry field
      diff and the per-artifact PUT plan, and record each target object's VersionId to the
      ledger (and a scratchpad copy). `--apply`: run both preflights, then for each target
      id: GET the index (fresh ETag), stage from the pin, `put_object` each changed CSV with
      `IfMatch=<that object's ETag>`, set that one entry in the freshly-read index, and
      `put_object` `matches.json` with `IfMatch=<the fresh index ETag>`. A
      `PreconditionFailed` on an artifact or on this match's index write aborts **that one
      match**, reports it (object key + sha256 + size, as the backfill does), and the loop
      continues with the next id (the per-match abort scope spec §6.3/§7 defines). Re-reading
      the index ETag each iteration means a prior match's own write never `412`s the next.
      Nothing is ever deleted; a partial match (artifacts PUT, index not) converges on a
      re-run (spec §6.3).
- [ ] **Step 7: Run the tests; `ruff check`/`ruff format --check` on `src/` AND
      `scripts/resync_skillcorner_opendata.py` (CI lints `src/` only); `pyright src/`.**

### Task 4: the verify script

**Files:**
- Create: `scripts/verify_skillcorner_opendata_resync.py`
- Modify: `pyproject.toml` (`per-file-ignores`: `scripts/verify_skillcorner_opendata_resync.py = ["S310"]`)
- Test: `src/tests/test_verify_skillcorner_opendata_resync.py`

**Interfaces:**
- Consumes: `is_lfs_pointer`, `parse_lfs_pointer` (for the pinned upstream pointer oid);
  the pinned raw host; `_verify_http` helpers; `UPSTREAM_COMMIT`.
- Produces: pure `check_revision_stamped(matches)`, `check_artifact_identity(entry,
  fetch_stored, fetch_pin_digest)` → problems lists. `main()` wires to the live API +
  pinned upstream.

- [ ] **Step 1: Write the failing tests** (injected callables):

```
test_verify_passes_when_all_match
test_verify_fails_when_revision_missing
test_verify_fails_when_stored_sha_differs_from_pin
test_verify_fails_when_stored_bytes_differ_from_artifact_meta
test_verify_tracking_hashed_from_stored_bytes   # tracking proven from bytes, not oid alone
test_verify_fails_on_size_mismatch
```

- [ ] **Step 2: Run the tests; verify they fail** (the checks do not exist yet).
- [ ] **Step 3: Implement the pure checks.** `check_revision_stamped`: every public entry
      has `source.revision == UPSTREAM_COMMIT`. `check_artifact_identity`: for each of the
      four artifacts, assert the stored object's sha256 AND size equal both the pinned
      upstream file (fetched/hashed fresh; `tracking` via download + hash) and the entry's
      `artifact_meta[key]`.
- [ ] **Step 4: Run the tests; verify they pass.**
- [ ] **Step 5: Implement `main`** — fetch the public listing, assert 20 entries and every
      id present (reuse `verify_listing`), run both checks for every entry, keep the
      servability/not-a-pointer checks, exit non-zero on any problem.
- [ ] **Step 6: Run the tests; `ruff check`/`ruff format --check` on `src/` AND
      `scripts/verify_skillcorner_opendata_resync.py` (CI lints `src/` only);
      `pyright src/`.**

### Task 5: ADR 0015, docs, version bump

**Files:**
- Create: `docs/decisions/0015-skillcorner-opendata-pin-and-resync.md`
- Modify: `docs/decisions/README.md` (index row), `docs/api-reference.md` (the `source`
  object near line 159 gains `revision`), `docs/context/scripts-and-migrations.md`
  (the new scripts), `AGENTS.md` (the `scripts/` line if it enumerates these),
  `CHANGELOG.md` (new version section), `pyproject.toml` + `uv.lock` (version bump)
- Test: none new; `test_schemas.py` and the doc-consistency tests must stay green.

- [ ] **Step 1: Write ADR 0015** — pin to a named commit; `source.revision`; conditional
      re-sync; upsert-only (report-and-keep); deliberate pin bump. Add the README index row
      and a `See Also` bump in ADR 0013 if warranted.
- [ ] **Step 2: Update `docs/api-reference.md`** — document `source.revision` in the
      `source` object and the rotation-free pin/bump note; update
      `docs/context/scripts-and-migrations.md` and `AGENTS.md` as needed.
- [ ] **Step 3: Bump the version 0.10.0 → 0.11.0** (minor: a backward-compatible schema
      field, new scripts, a data re-sync) — `pyproject.toml` and `uv.lock`; add the
      `CHANGELOG.md [0.11.0]` section with the compare link. Confirm no stale old version:
      `git grep -nF "0.10.0"` returns nothing outside history/CHANGELOG.
- [ ] **Step 4: Run the gates** — `uv run pytest` (record the new baseline count; cycle B
      ended at 723 passed + 1 skipped, so expect that plus the new tests), `ruff check` and
      `ruff format --check` on `src/` AND both new `scripts/*.py`, `pyright src/`,
      `uv run pip-audit`, and `detect-secrets scan --baseline .secrets.baseline` then
      `git diff --exit-code .secrets.baseline`. All green.

### Final steps (not a task — the executing-plans gate)

The executing-plans "self-review" is overridden by the owner's standing rule: every
implementation review is done by **two independent reviewer sessions (A and B)** that the
owner routes; never a self-review, never a subagent. The gates below match the previous
cycle (`docs/superpowers/plans/2026-10-27-list-token-tier.md`, Task 7).

- [ ] Run the `final-review` skill (C4, doc drift, release hygiene) before the commit.
- [ ] **A/B implementation review** (owner-routed, two independent sessions). Fix or
      answer every finding; take any A/B disagreement to the owner.
- [ ] **Owner gate — commit:** show the diff / file list; commit only on the owner's
      explicit yes for that commit. One commit for the whole cycle.
- [ ] **Owner gate — push + PR:** push and open the PR only on the owner's go; watch CI to
      green. (CI lints `src/` only — the `scripts/` lint ran locally in Tasks 3-4.)
- [ ] **Owner gate — merge:** admin squash-merge only on the owner's go. This cycle
      touches no `terraform/**`, so the merge triggers no `terraform apply`.
- [ ] **Rollout (spec §7), each with the owner's go:** record the pre-apply VersionIds of
      `skillcorner/matches.json` and every target artifact (to the ledger
      `.superpowers/sdd/2026-10-27-skillcorner-opendata-resync/progress.md` and a scratchpad
      copy); run the dry-run (both preflights, field diff); **owner go for `--apply`**; run
      the verify (§6.6); **owner go** for tag `v0.11.0` + GitHub Release.

---

## Review dispositions — plan A/B round 1 (2026-10-27)

No disagreement between A and B; every finding accepted.

- SOR-PLAN-01 (A, SHOULD FIX) = B-SCR-PLAN-02 (B, SHOULD FIX) — spec §9 confirm-against-live
  asserts had no plan step. Fixed: Task 3 adds `preflight_media_sha` (ranged GET must be
  `206`) and `preflight_if_match` (stale `IfMatch` must raise `PreconditionFailed`, a
  non-mutating probe of the index's own bytes), both run in the dry-run; tests
  `test_preflight_media_sha_asserts_206`, `test_preflight_if_match_requires_412`.
- B-SCR-PLAN-01 (B, SHOULD FIX) — the per-match index write with a once-read ETag would
  `412` matches 2-20. Fixed: Task 3 states the index + ETag are read once, the in-memory
  index is updated per successful match, and the index is written once at the end with the
  start ETag (the backfill shape); per-match `If-Match` applies to artifact objects only;
  test `test_resync_two_matches_one_index_write`.
- B-SCR-PLAN-03 (B, SHOULD FIX) — the final steps lacked the per-cycle gates. Fixed: the
  final steps now have the A/B implementation review (owner-routed, not self-review), and
  separate owner gates for commit, push+PR, and merge, matching the list-token-tier plan.
- SOR-PLAN-02 (A) / B-05 (B) — name `test_source_revision_in_schema`; `test_models.py` does
  not exist. Fixed: Task 1 names it and targets `test_match_entry_source_revision.py` +
  `test_schemas.py`.
- SOR-PLAN-03 (A) / B-04 (B) — expected results / gate paths. Fixed: Task 1 and Task 4
  steps carry the expected reason; Global Constraints and Task 5 Step 4 name the gate paths
  (lint `src/` AND `scripts/`, full pytest baseline, `pip-audit`, `detect-secrets`).
- B-06 — target version. Fixed: 0.10.0 → 0.11.0 (Global Constraints, Task 5).
- B-07 — VersionId record location. Fixed: the final steps record it to the ledger
  `.superpowers/sdd/2026-10-27-skillcorner-opendata-resync/progress.md` and a scratchpad copy.

## Review dispositions — plan A/B round 2 (2026-10-27)

A r2: APPROVE (SOR-PLAN-01..03 resolved). B r2: APPROVE (7/7 resolved) with two new
CONSIDER, both B's own honest flags on content B had steered. No disagreement; both fixed.

- B-SCR-PLAN-08 — the round-1 fix's single/batched index write widened the `412` abort
  scope beyond spec §6.3/§7 ("aborts that one match"). Fixed by aligning the plan to the
  spec instead of diverging: the index is now written **per match** with the ETag re-read
  each iteration (read-modify-write), so a `412` aborts only that match and a prior match's
  own write never `412`s the next. Test renamed `test_resync_two_matches_refresh_etag`. No
  spec change needed; the plan now matches the approved spec.
- B-SCR-PLAN-09 — `preflight_if_match` "nothing is mutated" held only when S3 honors
  `If-Match`. Fixed: the probe now re-reads the index bytes immediately before the
  conditional PUT, so the failure path (S3 ignores `If-Match`) writes a new object version
  with **identical content** (a no-op version under bucket versioning) and then aborts
  before any artifact PUT. The case is named in Task 3 Step 5.
