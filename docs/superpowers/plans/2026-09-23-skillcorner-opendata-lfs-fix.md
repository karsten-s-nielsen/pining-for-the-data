# SkillCorner Open Data Git-LFS Resolution Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the public SkillCorner Open Data ingest resolve Git-LFS blobs (not pointers) so every A-League `_tracking_extrapolated.jsonl` served by the mock API is real newline-delimited JSON, and repair the 10 tracking objects already stored on S3 as 133-byte pointers.

**Architecture:** The opendata repo LFS-tracks all `*.jsonl` (`.gitattributes: *.jsonl filter=lfs`), and `raw.githubusercontent.com` serves the 133-byte pointer, not the blob. Fix in four layers: (1) **prevention** — the loader detects a pointer, refetches the real blob from `media.githubusercontent.com`, and verifies it against the pointer's own `sha256`/`size` (content-addressed integrity = byte-identity with upstream); (2) **detection** — the verify script cheaply range-GETs the tracking body and fails on the LFS magic; (3) **remediation** — a one-shot, idempotent, dry-run-first backfill re-uploads the resolved blob for the 10 broken S3 objects; (4) **regression tests** cover every pure unit.

**Tech Stack:** Python 3.12+, hatch, boto3 (S3), `urllib.request` (anonymous HTTP), pytest, ruff, pyright. No new runtime dependency; no git/git-lfs dependency added.

**Spec:** No separate spec doc — this is a characterized bugfix. The "Context & Requirements" section below is the spec-equivalent, derived from the 2026-09-23 live investigation (every fact verified against live S3 and the upstream repo, not a paraphrase). The plan argues from it; executors read both.

---

## Context & Requirements (spec-equivalent — all facts verified live 2026-09-23)

**Root-cause chain (confirmed):**
1. `github.com/SkillCorner/opendata` `.gitattributes` = `*.jsonl filter=lfs diff=lfs merge=lfs -text` → every `_tracking_extrapolated.jsonl` is a Git-LFS blob.
2. `scripts/upload_skillcorner_opendata.py::http_get` (line 52-55) fetches from `https://raw.githubusercontent.com/SkillCorner/opendata/master/data/…`, which serves the **133-byte LFS pointer text**, not the blob.
3. `stage_match` (line 58-73) guards only `if not body` (empty) — a 133-byte non-empty pointer passes and is uploaded to S3 as the tracking artifact.
4. `scripts/verify_skillcorner_opendata_load.py` checks artifact **fetch status only** (302/200, line 83-86); a pointer serves 302, so verify passed.

**Blast radius (live S3, bucket `karstenskyt-pining-for-the-data`, profile `devops-agent`):** 10 of 20 public A-League `_tracking_extrapolated.jsonl` objects are 133-byte pointers — exactly the +10 added in PR #58 (v0.8.0); the loader was born in that PR and never touched the original 10 (which carry real blobs from an earlier path). Broken ids (== `verify_skillcorner_opendata_load.NEW_MATCH_IDS`):
```
1874553 1927964 1959846 1986691 1996436 2006363 2007448 2007721 2010085 2016236
```
Example pointer body (`1874553`): `version https://git-lfs.github.com/spec/v1` / `oid sha256:ea97f58f8eaad925feaeacc6395ec24860dd80027af8a89450276adebd29d265` / `size 90729279`. Sibling artifacts (`_match.json`, `_dynamic_events.csv`, `_phases_of_play.csv`) are real — only `.jsonl` is hit.

**Resolution mechanism (verified working):** `https://media.githubusercontent.com/media/SkillCorner/opendata/master/data/matches/1874553/1874553_tracking_extrapolated.jsonl` → `HTTP 200`, `Content-Length: 90729279` (== pointer `size`), body begins `{"frame": 0, "timestamp": null, …}`.

**Downstream contract:** consumers (luxury-lakehouse / silly-kicks) read the tracking via the API and expect per-frame newline-delimited JSON. `provenance="redistributed"` means **byte-parity with upstream** — the served bytes must equal the upstream blob. `sha256(body)==oid` guarantees exactly that. A DGX-side `git lfs pull` is the wrong layer: the cache faithfully mirrors the 133-byte S3 object; only fixing S3 (the source the API serves) fixes downstream.

## Global Constraints

Every task's requirements implicitly include this section.

- **Python 3.12+**, hatch build system.
- **Ruff** clean, line-length **120** (lint + format).
- **Pyright** basic mode clean.
- **pytest**; tests live in `src/tests/`.
- **No tracking data files in the repo** — fixtures are tiny and synthetic.
- **Fixtures wholly invented** (never a renamed real record). LFS-pointer fixtures use an invented 64-hex `oid` and an invented `size`; blob-verification fixtures use self-constructed byte strings whose `sha256` is computed in the test itself — never a real licensed `id→entity` datum.
- **Byte-parity contract:** a resolved blob MUST satisfy `len(body)==size` AND `sha256(body).hexdigest()==oid`; mismatch raises. `provenance` stays `"redistributed"`.
- **Public A-League tier is S3-only** and stays in native V3 four-artifact JSONL shape (ADR 0011 keeps the public tier out of the canonical Parquet set). Do NOT convert to Parquet. Do NOT push to HuggingFace.
- **Anonymous HTTP only** in the loader/backfill; no git or git-lfs dependency is added.
- **Subagent model routing** (user global rule): reading→`haiku`, research→`sonnet`, implementation→`opus`.
- **COMMIT DISCIPLINE (overrides the writing-plans template's per-step commits):** NO per-task, per-step, or micro-commits. The entire code change (Tasks 1-4) lands as **one coherent, fully-tested commit**, made ONLY after Karsten's explicit "commit" for that specific diff. Tasks 1-4 therefore contain **no commit step** — they end at green tests. Task 5 is the single quality-gate + explicit-approval + commit + PR task.
- **Branch, not worktree:** one feature branch `fix/skillcorner-opendata-lfs` off `main`. No worktrees, no fan-out across branches.
- **The live S3 backfill RUN is a separate, gated ops action** (Task 6), NOT part of the commit: dry-run → show output → explicit approval → `--apply` → post-verify. Overwriting live S3 objects is outward-facing and is never done without that gate.
- **Run `final-review` skill before the final commit** (project CLAUDE.md hard rule).

---

## File Structure

- **Modify** `src/formats/skillcorner_opendata.py` — add pure LFS helpers (`is_lfs_pointer`, `parse_lfs_pointer`, `verify_blob`) and a pure selector (`public_opendata_ids`). Pure, no I/O — the module's existing character.
- **Modify** `scripts/upload_skillcorner_opendata.py` — add `MEDIA_BASE`, `media_get`, `fetch_resolved`; wire `main` to stage via `fetch_resolved`. The I/O boundary.
- **Modify** `scripts/verify_skillcorner_opendata_load.py` — add pure `check_tracking_not_pointer` + a header-free presigned range-fetch; wire into `main` for all `NEW_MATCH_IDS`.
- **Create** `scripts/backfill_skillcorner_opendata_lfs.py` — one-shot, dry-run-first, idempotent S3 repair reusing `fetch_resolved` + `public_opendata_ids`.
- **Modify** `src/tests/test_skillcorner_opendata.py` — tests for the four new pure helpers.
- **Modify** `src/tests/test_upload_skillcorner_opendata.py` — tests for `fetch_resolved`.
- **Modify** `src/tests/test_verify_skillcorner_opendata.py` — test for `check_tracking_not_pointer`.
- **Create** `src/tests/test_backfill_skillcorner_opendata_lfs.py` — tests for the backfill's pure decision helper.
- **Modify** `CHANGELOG.md`, `ARCHITECTURE.md` (scripts inventory), project `CLAUDE.md` (scripts/ inventory) — documentation, folded into Task 5.

Existing tests unaffected by design: `stage_match` keeps its `if not body` guard (defense-in-depth), so `TestStageMatch` and every other current test stays green.

---

### Task 0: Create the feature branch

**Files:** none.

- [ ] **Step 1: Branch off main**

The working tree is on `main`. Create and switch to the feature branch before any edit:
```bash
git checkout main
git pull --ff-only
git checkout -b fix/skillcorner-opendata-lfs
```
Expected: `Switched to a new branch 'fix/skillcorner-opendata-lfs'`. All of Tasks 1-5 happen on this one branch (no worktree, no second branch). No commit here.

---

### Task 1: Pure Git-LFS helpers + public-opendata selector

**Files:**
- Modify: `src/formats/skillcorner_opendata.py`
- Test: `src/tests/test_skillcorner_opendata.py`

**Interfaces:**
- Consumes: nothing (leaf module, stdlib only).
- Produces:
  - `is_lfs_pointer(body: bytes) -> bool`
  - `parse_lfs_pointer(body: bytes) -> tuple[str, int]` — returns `(oid_hex, size)`; raises `ValueError` if malformed.
  - `verify_blob(body: bytes, oid: str, size: int) -> None` — raises `ValueError` on size or sha256 mismatch.
  - `public_opendata_ids(matches: list[dict]) -> list[str]` — sorted ids of public entries that carry a `*_tracking_extrapolated` artifact key.

- [ ] **Step 1: Write the failing tests**

Add to `src/tests/test_skillcorner_opendata.py`:

```python
import hashlib

from formats.skillcorner_opendata import (
    is_lfs_pointer,
    parse_lfs_pointer,
    public_opendata_ids,
    verify_blob,
)

_POINTER = (
    b"version https://git-lfs.github.com/spec/v1\n"
    b"oid sha256:" + b"a" * 64 + b"\n"
    b"size 90729279\n"
)


class TestIsLfsPointer:
    def test_true_for_pointer(self) -> None:
        assert is_lfs_pointer(_POINTER) is True

    def test_false_for_real_jsonl(self) -> None:
        assert is_lfs_pointer(b'{"frame": 0, "timestamp": null}\n') is False

    def test_false_for_empty(self) -> None:
        assert is_lfs_pointer(b"") is False


class TestParseLfsPointer:
    def test_extracts_oid_and_size(self) -> None:
        oid, size = parse_lfs_pointer(_POINTER)
        assert oid == "a" * 64
        assert size == 90729279

    def test_raises_on_missing_oid(self) -> None:
        with pytest.raises(ValueError, match="malformed LFS pointer"):
            parse_lfs_pointer(b"version https://git-lfs.github.com/spec/v1\nsize 5\n")

    def test_raises_on_missing_size(self) -> None:
        with pytest.raises(ValueError, match="malformed LFS pointer"):
            parse_lfs_pointer(b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"a" * 64 + b"\n")


class TestVerifyBlob:
    def test_ok_when_sha_and_size_match(self) -> None:
        body = b'{"frame": 0}\n'
        verify_blob(body, hashlib.sha256(body).hexdigest(), len(body))  # no raise

    def test_raises_on_size_mismatch(self) -> None:
        body = b'{"frame": 0}\n'
        with pytest.raises(ValueError, match="size mismatch"):
            verify_blob(body, hashlib.sha256(body).hexdigest(), len(body) + 1)

    def test_raises_on_sha_mismatch(self) -> None:
        body = b'{"frame": 0}\n'
        with pytest.raises(ValueError, match="sha256 mismatch"):
            verify_blob(body, "b" * 64, len(body))


class TestPublicOpendataIds:
    def test_selects_public_with_tracking_sorted(self) -> None:
        matches = [
            {"id": "1959846", "visibility": "public", "artifacts": {"1959846_tracking_extrapolated": "x.jsonl"}},
            {"id": "1874553", "visibility": "public", "artifacts": {"1874553_tracking_extrapolated": "y.jsonl"}},
            {"id": "5001", "visibility": "private", "artifacts": {"5001_tracking_extrapolated": "z.jsonl"}},
            {"id": "9999", "visibility": "public", "artifacts": {"9999_match": "m.json"}},
        ]
        assert public_opendata_ids(matches) == ["1874553", "1959846"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest src/tests/test_skillcorner_opendata.py -v`
Expected: FAIL — `ImportError: cannot import name 'is_lfs_pointer'`.

- [ ] **Step 3: Implement the helpers**

Add to `src/formats/skillcorner_opendata.py` (imports `hashlib`, `re` at top with the existing imports; keep the module I/O-free):

```python
import hashlib
import re

_LFS_MAGIC = b"version https://git-lfs.github.com/spec/v1"
_LFS_OID_RE = re.compile(r"^oid sha256:([0-9a-f]{64})$", re.MULTILINE)
_LFS_SIZE_RE = re.compile(r"^size (\d+)$", re.MULTILINE)


def is_lfs_pointer(body: bytes) -> bool:
    """True if ``body`` is a Git-LFS pointer file rather than the real blob.

    raw.githubusercontent.com serves this 133-byte text for any LFS-tracked path; the opendata
    repo LFS-tracks every ``*.jsonl`` (``.gitattributes: *.jsonl filter=lfs``).
    """
    return body.startswith(_LFS_MAGIC)


def parse_lfs_pointer(body: bytes) -> tuple[str, int]:
    """Extract ``(oid_sha256_hex, size)`` from a Git-LFS pointer body.

    Raises ValueError if either the ``oid sha256:<hex>`` or ``size <int>`` line is absent/malformed.
    """
    text = body.decode("utf-8", errors="replace")
    oid_m = _LFS_OID_RE.search(text)
    size_m = _LFS_SIZE_RE.search(text)
    if not oid_m or not size_m:
        raise ValueError("malformed LFS pointer (need 'oid sha256:<64-hex>' and 'size <int>')")
    return oid_m.group(1), int(size_m.group(1))


def verify_blob(body: bytes, oid: str, size: int) -> None:
    """Assert a resolved blob matches the pointer's content-addressed identity.

    The pointer's ``oid`` IS the sha256 of the real content, so this proves byte-parity with
    upstream — the redistribution contract. Raises ValueError on size or sha256 mismatch
    (truncation, corruption, LFS-bandwidth error page, or wrong object).
    """
    if len(body) != size:
        raise ValueError(f"LFS blob size mismatch: got {len(body)}, expected {size}")
    digest = hashlib.sha256(body).hexdigest()
    if digest != oid:
        raise ValueError(f"LFS blob sha256 mismatch: got {digest}, expected {oid}")


def public_opendata_ids(matches: list[dict]) -> list[str]:
    """Sorted ids of public entries carrying a ``*_tracking_extrapolated`` artifact.

    Used by the backfill to select the live A-League matches whose tracking object may be an
    unresolved LFS pointer. ``_sorted_ids`` keeps numeric SkillCorner ids in numeric order.
    """
    ids = [
        str(m["id"])
        for m in matches
        if m.get("visibility") == "public"
        and any("tracking_extrapolated" in k for k in (m.get("artifacts") or {}))
    ]
    return _sorted_ids(ids)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest src/tests/test_skillcorner_opendata.py -v`
Expected: PASS (all new tests + all pre-existing `match_info`/discovery tests).

---

### Task 2: Loader resolves LFS pointers via the media host

**Files:**
- Modify: `scripts/upload_skillcorner_opendata.py`
- Test: `src/tests/test_upload_skillcorner_opendata.py`

**Interfaces:**
- Consumes (Task 1): `is_lfs_pointer`, `parse_lfs_pointer`, `verify_blob`.
- Produces:
  - `media_get(rel: str) -> bytes` — fetch a `data/`-root-relative path from `media.githubusercontent.com`.
  - `fetch_resolved(rel: str, *, raw: Callable[[str], bytes] = http_get, media: Callable[[str], bytes] = media_get) -> bytes` — fetch an artifact, transparently resolving an LFS pointer and verifying it; raises `ValueError` on empty or on integrity mismatch. Reused by the backfill (Task 4).

- [ ] **Step 1: Write the failing tests**

Add to `src/tests/test_upload_skillcorner_opendata.py`:

```python
import hashlib


class TestFetchResolved:
    def test_returns_real_body_unchanged(self, opendata_adapter) -> None:
        body = b'{"frame": 0}\n'
        out = opendata_adapter.fetch_resolved("x.jsonl", raw=lambda r: body, media=lambda r: b"UNUSED")
        assert out == body

    def test_resolves_pointer_via_media_and_verifies(self, opendata_adapter) -> None:
        blob = b'{"frame": 0, "timestamp": null}\n'
        oid = hashlib.sha256(blob).hexdigest()
        pointer = (
            b"version https://git-lfs.github.com/spec/v1\n"
            b"oid sha256:" + oid.encode() + b"\n"
            b"size " + str(len(blob)).encode() + b"\n"
        )
        out = opendata_adapter.fetch_resolved("x.jsonl", raw=lambda r: pointer, media=lambda r: blob)
        assert out == blob

    def test_raises_on_media_integrity_mismatch(self, opendata_adapter) -> None:
        blob = b'{"frame": 0}\n'
        oid = hashlib.sha256(blob).hexdigest()
        pointer = (
            b"version https://git-lfs.github.com/spec/v1\n"
            b"oid sha256:" + oid.encode() + b"\n"
            b"size " + str(len(blob)).encode() + b"\n"
        )
        with pytest.raises(ValueError, match="sha256 mismatch"):
            opendata_adapter.fetch_resolved("x.jsonl", raw=lambda r: pointer, media=lambda r: b"CORRUPT")

    def test_raises_on_empty(self, opendata_adapter) -> None:
        with pytest.raises(ValueError, match="empty"):
            opendata_adapter.fetch_resolved("x.jsonl", raw=lambda r: b"", media=lambda r: b"UNUSED")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest src/tests/test_upload_skillcorner_opendata.py::TestFetchResolved -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'fetch_resolved'`.

- [ ] **Step 3: Implement resolution in the loader**

In `scripts/upload_skillcorner_opendata.py`, extend the `formats.skillcorner_opendata` import (line 38-43) to also import the helpers:

```python
from formats.skillcorner_opendata import (  # noqa: E402
    discover_match_ids,
    is_lfs_pointer,
    match_info,
    opendata_files,
    parse_lfs_pointer,
    select_new_matches,
    verify_blob,
)
```

Add the media base beside `RAW_BASE` (line 49):

```python
MEDIA_BASE = "https://media.githubusercontent.com/media/SkillCorner/opendata/master/data"
```

Add after `http_get` (after line 55):

```python
def media_get(rel: str) -> bytes:
    """Fetch a ``data/``-root-relative path's real LFS blob from the media host (anonymous)."""
    with urllib.request.urlopen(f"{MEDIA_BASE}/{rel}", timeout=180) as resp:
        return resp.read()


def fetch_resolved(
    rel: str,
    *,
    raw: Callable[[str], bytes] = http_get,
    media: Callable[[str], bytes] = media_get,
) -> bytes:
    """Fetch an opendata artifact, transparently resolving a Git-LFS pointer.

    ``raw`` (raw.githubusercontent) serves a 133-byte pointer for any LFS-tracked file — all
    ``*.jsonl`` per the repo ``.gitattributes``. On a pointer, refetch the real blob from ``media``
    and verify it against the pointer's own ``sha256``/``size`` (byte-parity with upstream). Raises
    ValueError on an empty artifact or an integrity mismatch. Generalized to any artifact, so a
    future upstream LFS migration of a non-jsonl file is handled without a code change.
    """
    body = raw(rel)
    if not body:
        raise ValueError(f"artifact {rel} is empty")
    if is_lfs_pointer(body):
        oid, size = parse_lfs_pointer(body)
        blob = media(rel)
        verify_blob(blob, oid, size)
        return blob
    return body
```

Wire `main` to stage via the resolver — change the `stage_match` call (line 157) from `http_get` to `fetch_resolved`:

```python
            staging = stage_match(fetch_resolved, Path(tmp), mid)
```

Leave `stage_match` itself unchanged (it keeps the `if not body` guard as defense-in-depth; `fetch_resolved` already raises on empty, and on the resolved path returns a non-empty blob). Update the `stage_match` docstring's mention of `fetch(rel)` to note the production callable is `fetch_resolved`, which resolves LFS pointers.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest src/tests/test_upload_skillcorner_opendata.py -v`
Expected: PASS (new `TestFetchResolved` + unchanged `TestStageMatch`/`TestLiveMatchIds`/`TestPrivatePlayerIds`).

---

### Task 3: Verify script fails on an unresolved pointer body

**Files:**
- Modify: `scripts/verify_skillcorner_opendata_load.py`
- Test: `src/tests/test_verify_skillcorner_opendata.py`

**Interfaces:**
- Consumes: nothing new (pure check is self-contained; the presigned range-fetch reuses the existing `NoFollow` opener).
- Produces:
  - `check_tracking_not_pointer(match_id: str, first_bytes: bytes) -> list[str]` — `[]` when the body is real, one problem string when it starts with the LFS magic.

- [ ] **Step 1: Write the failing test**

Add to `src/tests/test_verify_skillcorner_opendata.py`:

```python
class TestCheckTrackingNotPointer:
    def test_ok_for_real_jsonl(self, verify) -> None:
        assert verify.check_tracking_not_pointer("1874553", b'{"frame": 0, "timestamp": null}') == []

    def test_flags_lfs_pointer(self, verify) -> None:
        problems = verify.check_tracking_not_pointer("1874553", b"version https://git-lfs.github.com/spec/v1\n")
        assert any("unresolved Git-LFS pointer" in p for p in problems)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest src/tests/test_verify_skillcorner_opendata.py::TestCheckTrackingNotPointer -v`
Expected: FAIL — `AttributeError: ... 'check_tracking_not_pointer'`.

- [ ] **Step 3: Implement the check + wire it in**

Add the pure check to `scripts/verify_skillcorner_opendata_load.py` (near `check_public_match`):

```python
def check_tracking_not_pointer(match_id: str, first_bytes: bytes) -> list[str]:
    """Problem if the tracking body is still an unresolved Git-LFS pointer (empty == ok).

    A pointer serves a normal 302/200, so the status check passes; only inspecting the body
    catches it. Checking the first bytes is decisive and avoids the ~90 MB download.
    """
    if first_bytes.startswith(b"version https://git-lfs"):
        return [f"{match_id}: tracking artifact is an unresolved Git-LFS pointer"]
    return []
```

Add a header-free presigned range-fetch in `main` (after `artifact_status_for` is defined). It mirrors the two-step pattern `NoFollow` exists for: hit the API with the bearer token (302 raises `HTTPError`), read `Location`, then fetch the presigned URL with a `Range` header and NO `Authorization` (S3 rejects double auth — see `_verify_http.NoFollow`):

```python
    def tracking_first_bytes(match_id: str, n: int = 64) -> bytes:
        # Handle BOTH serve modes: a 302 to a presigned S3 URL (fetch it header-free with a Range),
        # and a direct 200 that streams the body (read the first n bytes off the response). The
        # existing artifact_status check accepts 200 OR 302, so a pointer could arrive either way.
        key = f"{match_id}_tracking_extrapolated"
        req = urllib.request.Request(
            f"{args.api}/{PROVIDER}/matches/{match_id}/{key}",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            resp = opener.open(req, timeout=30)
            location = resp.headers.get("Location")
            if not location:  # 200 — body is streamed directly; read the first n bytes
                return resp.read(n)
        except urllib.error.HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                return b""  # non-pointer sentinel; artifact_status already asserts servability
            location = exc.headers.get("Location")
        if not location:
            return b""
        ranged = urllib.request.Request(location, headers={"Range": f"bytes=0-{n - 1}"})
        with urllib.request.urlopen(ranged, timeout=30) as blob_resp:
            return blob_resp.read()
```

Then extend the problem collection in `main` — after the sample-artifact block, check the tracking body of **every** new id (10 cheap 64-byte range reads, the artifact that was actually corrupted):

```python
    for mid in NEW_MATCH_IDS:
        if mid in present:
            problems += check_tracking_not_pointer(mid, tracking_first_bytes(mid))
```

Update the module docstring's assertion list to add: "each new match's tracking body is real JSONL, not an unresolved Git-LFS pointer (first-bytes range read)."

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest src/tests/test_verify_skillcorner_opendata.py -v`
Expected: PASS (new `TestCheckTrackingNotPointer` + all existing verify tests).

---

### Task 4: One-shot backfill for the 10 broken S3 objects

**Files:**
- Create: `scripts/backfill_skillcorner_opendata_lfs.py`
- Test: `src/tests/test_backfill_skillcorner_opendata_lfs.py`

**Interfaces:**
- Consumes (Task 1): `public_opendata_ids`, `is_lfs_pointer`, `parse_lfs_pointer`, `opendata_files`. (Task 2): `fetch_resolved`.
- Produces:
  - `select_repairs(probes: list[tuple[str, bytes]]) -> list[str]` — pure: from `(match_id, head_bytes)` pairs, the ids whose current S3 tracking body is a Git-LFS pointer (`is_lfs_pointer(head_bytes)`). This is the sole repair predicate — decided on body content, never on an object's byte count.
  - `main()` — dry-run-first, idempotent CLI.

Design notes (audit-trail one-shot, matching the `backfill_*`/`migrate_*` convention):
- Reads the **live** `skillcorner/matches.json`, selects ids via `public_opendata_ids` (verify live state, never a plan paraphrase).
- **No exact-size gate.** A Git-LFS pointer is `125 + len(str(size))` bytes (e.g. 133 for a 10–100 MB blob, 134 at ≥100 MB) — an exact `== 133` filter would silently skip a pointer whose blob size has a different digit count. Instead, `get_object` the first 256 bytes of **every** public opendata tracking object via a `Range` request (256 bytes off a ~90 MB object is trivial), then let `select_repairs` decide on the body via `is_lfs_pointer`. A real blob's first bytes (`{"frame": 0,…`) are never a pointer, so real objects are skipped — regardless of their size.
- Repair = `fetch_resolved(opendata_files(mid)["tracking_extrapolated"])` (raw→pointer→media→`verify_blob`), write to a temp file named exactly `{mid}_tracking_extrapolated.jsonl`, then `s3.upload_file(tmp, bucket, key)` — the **same** call `upload_game` uses, so ContentType/metadata match a fresh ingest byte-for-byte.
- Idempotent + re-runnable: a real object is skipped. Dry-run default; `--apply` writes.
- After each `--apply` write, `head_object` again and assert new `ContentLength == len(blob)`; report per id.
- No index write — the artifact filename is unchanged, so `matches.json` stays correct.

- [ ] **Step 1: Write the failing test**

Create `src/tests/test_backfill_skillcorner_opendata_lfs.py`:

```python
"""Tests for the opendata LFS backfill's pure decision helper (synthetic data only).

scripts/ is not a package; the `load_script` fixture loads the script by path. No S3 or network
is touched here — only the pure `select_repairs` predicate is exercised.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def backfill(load_script):
    return load_script("backfill_skillcorner_opendata_lfs")


_POINTER_HEAD = b"version https://git-lfs.github.com/spec/v1\noid sha256:"
_REAL_HEAD = b'{"frame": 0, "timestamp": null, "period": null}'


class TestSelectRepairs:
    def test_selects_only_pointer_bodies(self, backfill) -> None:
        probes = [
            ("1874553", _POINTER_HEAD),  # pointer (a 10-100 MB blob -> 133-byte pointer today)
            ("1886347", _REAL_HEAD),  # real blob -> skip
            ("2016236", _POINTER_HEAD),  # pointer
        ]
        assert backfill.select_repairs(probes) == ["1874553", "2016236"]

    def test_empty_when_all_real(self, backfill) -> None:
        assert backfill.select_repairs([("1886347", _REAL_HEAD)]) == []

    def test_ignores_size_only_looks_at_body(self, backfill) -> None:
        # A 134-byte pointer (a >=100 MB blob) must still be selected — no byte-count gate.
        big = _POINTER_HEAD + b"a" * 64 + b"\nsize 123456789\n"  # 9-digit size
        assert backfill.select_repairs([("9999999", big)]) == ["9999999"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest src/tests/test_backfill_skillcorner_opendata_lfs.py -v`
Expected: FAIL — the script does not exist yet (`load_script` raises `FileNotFoundError`).

- [ ] **Step 3: Implement the backfill script**

Create `scripts/backfill_skillcorner_opendata_lfs.py`:

```python
"""One-shot: repair public SkillCorner opendata tracking objects stored as Git-LFS pointers.

PR #58 ingested 10 A-League ``_tracking_extrapolated.jsonl`` objects via raw.githubusercontent,
which serves the 133-byte Git-LFS pointer instead of the blob (the repo LFS-tracks every
``*.jsonl``). This script finds every live public opendata tracking object that is still a pointer
and re-uploads the resolved blob, verified byte-for-byte against the pointer's ``sha256``/``size``.

Idempotent and safe to re-run: a real object is skipped; only pointers are replaced. Dry-run by
default; pass ``--apply`` to write. It operates on LIVE S3 state (the source the mock API serves),
never a plan paraphrase, and reuses ``fetch_resolved`` from the loader so resolution + integrity
verification are one code path. Retained as the audit trail for a change applied to live S3, in the
manner of ``backfill_skillcorner_artifacts.py`` / ``migrate_skillcorner_tracking_parquet.py``.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from formats.skillcorner_opendata import (  # noqa: E402
    is_lfs_pointer,
    opendata_files,
    parse_lfs_pointer,
    public_opendata_ids,
)

PROVIDER = "skillcorner"
_PROBE_BYTES = 256  # enough to hold any LFS pointer (~133 B); trivial off a ~90 MB object


def select_repairs(probes: list[tuple[str, bytes]]) -> list[str]:
    """From (match_id, head_bytes) pairs, the ids whose tracking body is a Git-LFS pointer.

    The decision is on body content (``is_lfs_pointer``), never on an object's byte count — a
    pointer is ``125 + len(str(size))`` bytes, so an exact size gate would miss pointers whose
    blob size has a different digit count.
    """
    return [mid for mid, head in probes if is_lfs_pointer(head)]


def _live_public_ids(s3, bucket: str) -> list[str]:
    obj = s3.get_object(Bucket=bucket, Key=f"{PROVIDER}/matches.json")
    data = json.loads(obj["Body"].read().decode("utf-8"))
    return public_opendata_ids(data.get("matches", []))


def _tracking_key(mid: str) -> str:
    return f"{PROVIDER}/{mid}/{mid}_tracking_extrapolated.jsonl"


def main() -> None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

    import boto3
    from upload_skillcorner_opendata import fetch_resolved  # resolve + verify, one code path

    ap = argparse.ArgumentParser(description="Repair opendata tracking objects stored as LFS pointers")
    ap.add_argument("--bucket", default=os.environ.get("PINING_BUCKET"))
    ap.add_argument("--apply", action="store_true", help="apply changes (default is dry-run)")
    args = ap.parse_args()
    if not args.bucket:
        ap.error("--bucket required (or set PINING_BUCKET)")

    s3 = boto3.client("s3")
    ids = _live_public_ids(s3, args.bucket)
    print(f"public opendata matches: {len(ids)} (apply={args.apply})")

    # Probe the first bytes of every tracking object (Range GET) — no byte-count gate.
    probes = [
        (mid, s3.get_object(Bucket=args.bucket, Key=_tracking_key(mid), Range=f"bytes=0-{_PROBE_BYTES - 1}")["Body"].read())
        for mid in ids
    ]
    broken = select_repairs(probes)
    heads = dict(probes)
    for mid in broken:
        _, size = parse_lfs_pointer(heads[mid])
        print(f"  {'REPAIR' if args.apply else 'DRY-RUN'} {mid}: pointer -> {size} bytes")

    if not broken:
        print("Nothing to repair — all public opendata tracking objects are real blobs.")
        return
    if not args.apply:
        print(f"Dry-run only — {len(broken)} object(s) would be repaired. Re-run with --apply.")
        return

    repaired = 0
    for mid in broken:
        key = _tracking_key(mid)
        blob = fetch_resolved(opendata_files(mid)["tracking_extrapolated"])  # raw->pointer->media->verify
        with tempfile.TemporaryDirectory(prefix=f"sc-od-repair-{mid}-") as tmp:
            local = Path(tmp) / f"{mid}_tracking_extrapolated.jsonl"
            local.write_bytes(blob)
            s3.upload_file(str(local), args.bucket, key)  # same call as upload_game -> metadata parity
        new_len = s3.head_object(Bucket=args.bucket, Key=key)["ContentLength"]
        if new_len != len(blob):
            raise RuntimeError(f"{mid}: post-upload size {new_len} != resolved {len(blob)}")
        repaired += 1
        print(f"  OK {mid}: now {new_len} bytes")

    print(f"Done — {repaired} tracking object(s) repaired.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest src/tests/test_backfill_skillcorner_opendata_lfs.py -v`
Expected: PASS.

---

### Task 5: Quality gate, docs, single commit, PR (EXPLICIT-APPROVAL GATE)

**Files:**
- Modify: `CHANGELOG.md`, `ARCHITECTURE.md`, `CLAUDE.md` (scripts/ inventory line for the new backfill).
- No new code.

This is the **only** task that commits. It bundles Tasks 1-4 into one coherent, fully-tested commit — after Karsten's explicit approval of the exact diff.

- [ ] **Step 1: Full local quality gate (Shift Left)**

Run and confirm all clean:
```bash
ruff check .
ruff format --check .
pyright
python -m pytest src/tests/ -q
```
Expected: ruff clean, pyright 0 errors, entire suite green.

- [ ] **Step 2: Documentation updates**

- `CHANGELOG.md` — under a new `Unreleased`/next-version `Fixed` entry: opendata tracking artifacts were stored as Git-LFS pointers; loader now resolves via the media host with sha256/size verification; verify script now fails on a pointer body; add the one-shot backfill. Frame positively around SkillCorner; no delivery-channel or dated-correspondence detail.
- `ARCHITECTURE.md` and project `CLAUDE.md` — add `backfill_skillcorner_opendata_lfs.py` to the `scripts/` "completed one-shot migrations" inventory, one line, matching the surrounding style.

- [ ] **Step 3: Run the `final-review` skill**

Invoke `final-review` (project CLAUDE.md hard rule). Resolve every finding it raises (doc drift, stale references, missing test updates). Re-run Step 1's gate if it changed code.

- [ ] **Step 4: STOP — request explicit commit approval**

Show Karsten the full `git diff --stat` and the complete diff. State: nothing is committed yet. **Wait for an explicit "commit"** for this specific diff. Do NOT proceed on "tests are green", a prior approval, or this plan's existence.

- [ ] **Step 5: Commit (only after explicit approval)**

On the feature branch `fix/skillcorner-opendata-lfs`:
```bash
git add src/formats/skillcorner_opendata.py \
        scripts/upload_skillcorner_opendata.py \
        scripts/verify_skillcorner_opendata_load.py \
        scripts/backfill_skillcorner_opendata_lfs.py \
        src/tests/test_skillcorner_opendata.py \
        src/tests/test_upload_skillcorner_opendata.py \
        src/tests/test_verify_skillcorner_opendata.py \
        src/tests/test_backfill_skillcorner_opendata_lfs.py \
        CHANGELOG.md ARCHITECTURE.md CLAUDE.md
git commit  # message below
```
Commit message:
```
fix(skillcorner): resolve opendata Git-LFS tracking blobs on ingest

raw.githubusercontent serves a 133-byte LFS pointer for opendata *.jsonl
(the repo LFS-tracks every .jsonl). The public A-League loader uploaded
those pointers as tracking artifacts; 10 matches (PR #58) served a pointer
instead of per-frame JSONL.

- loader resolves a pointer via media.githubusercontent and verifies the
  blob against the pointer's own sha256/size (byte-parity with upstream)
- verify script range-reads the tracking body and fails on the LFS magic
- one-shot backfill re-uploads the resolved blob for already-stored pointers
- pure helpers unit-tested (pointer parse, integrity verify, id selection)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

- [ ] **Step 6: Open the PR (after commit)**

Push the branch and open a PR from `fix/skillcorner-opendata-lfs` into `main`. PR body: problem, four-layer fix, verified blast radius (10/20), and a note that the live S3 backfill (Task 6) is a separate gated ops step run against dev after merge. End the PR description with:
```
🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

---

### Task 6: Run the backfill against live dev S3 (SEPARATE GATED OPS ACTION — post-merge)

Not part of the commit/PR. Outward-facing (overwrites 10 live S3 objects). Requires its own approval gate.

- [ ] **Step 1: Dry-run**

```bash
export MSYS_NO_PATHCONV=1
export AWS_PROFILE=devops-agent
export PINING_BUCKET=karstenskyt-pining-for-the-data
python scripts/backfill_skillcorner_opendata_lfs.py   # dry-run (no --apply)
```
Expected: reports 10 DRY-RUN ids (the broken set) and their target sizes.

- [ ] **Step 2: STOP — show dry-run output, request explicit approval to `--apply`**

Present the dry-run list. Wait for Karsten's explicit go to write to live S3.

- [ ] **Step 3: Apply**

```bash
AWS_PROFILE=devops-agent python scripts/backfill_skillcorner_opendata_lfs.py --apply
```
Expected: 10 objects repaired, each post-upload size == the pointer's declared size.

- [ ] **Step 4: Post-load verification**

```bash
AWS_PROFILE=devops-agent PINING_API=<dev-api-base> PINING_PUBLIC_TOKEN=<public-token> \
  python scripts/verify_skillcorner_opendata_load.py
```
Expected: `OK` — 20 matches, players non-empty, sample serves its 4 artifacts, and no new-id tracking body is a pointer. Also spot-check one repaired object directly:
```bash
AWS_PROFILE=devops-agent aws s3api head-object \
  --bucket karstenskyt-pining-for-the-data \
  --key skillcorner/1874553/1874553_tracking_extrapolated.jsonl \
  --query ContentLength   # expect 90729279, not 133
```

- [ ] **Step 5: Notify downstream**

Tell the silly-kicks / luxury-lakehouse side the S3 source is fixed so they can refresh their DGX pining cache (their `git lfs pull` was the wrong layer; the corrected bytes now come straight from the API).

---

## Self-Review

**Spec coverage:** (1) prevention → Task 2; (2) detection → Task 3; (3) remediation → Tasks 4+6; (4) regression tests → Tasks 1-4 each ship tests; byte-parity contract → `verify_blob` (Task 1), enforced in `fetch_resolved` (Task 2) and the backfill post-check (Task 4); live-state discipline → backfill reads live `matches.json` (Task 4); all 10 ids covered → `public_opendata_ids` selects them, verify checks all `NEW_MATCH_IDS`. No spec requirement is unmapped.

**Placeholder scan:** no TBD/TODO/"handle edge cases"/"similar to Task N"; every code and test step carries real content.

**Type consistency:** `is_lfs_pointer(bytes)->bool`, `parse_lfs_pointer(bytes)->tuple[str,int]`, `verify_blob(bytes,str,int)->None`, `public_opendata_ids(list[dict])->list[str]`, `fetch_resolved(str,*,raw,media)->bytes`, `check_tracking_not_pointer(str,bytes)->list[str]`, `select_repairs(list[tuple[str,bytes]])->list[str]` — names/signatures used identically wherever consumed across tasks.

**Commit-discipline check:** Task 0 creates the one feature branch; Tasks 1-4 contain no commit step; Task 5 is the single commit behind an explicit-approval gate; Task 6's live write is a separate gate. Branch, not worktree. Matches user global rules and project CLAUDE.md.

## Review follow-ups incorporated (2026-09-23 plan review)

- **S1** (byte-count fragility) — the backfill's exact `== 133` gate is removed; `select_repairs` decides on body content (`is_lfs_pointer`) after a 256-byte `Range` probe of every object, so a pointer at any blob-size digit count is caught. Comment corrected (pointer = `125 + len(str(size))`).
- **C1** — the decision is a pure, unit-tested `select_repairs()` (`TestSelectRepairs`, incl. a 9-digit-size pointer case).
- **C2** — `tracking_first_bytes` now reads the body on the 200 (streamed) branch, not only the 302 branch.
- **C3** — Task 0 adds the explicit `git checkout -b fix/skillcorner-opendata-lfs`.
