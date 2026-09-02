# SkillCorner Canonical Parquet/zstd Format — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Converge owner-tier SkillCorner artifacts onto a canonical columnar Parquet/zstd set (nested `tracking.parquet`, zstd `events`/`physical`, `freeze` dropped, `format_version` marker), migrate the existing Real Madrid matches in place, and ingest Premier League 2025/26.

**Architecture:** Pure transforms in `src/formats/` (functional core); all S3/HF I/O and orchestration in `scripts/` (imperative shell), with the S3 client dependency-injected so adapters are testable without network. Tracking JSON is columnarized to a nested Parquet that round-trips under a defined per-column-type equivalence relation; the migration never deletes/overwrites a source object until the *stored* replacement has been re-fetched from S3 and verified.

**Tech Stack:** Python 3.12+, pyarrow (already a dep), pandas (already a dep), boto3, pydantic (models only, outside the Lambda zip), pytest, ruff, pyright. No new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-09-01-skillcorner-canonical-parquet-format-design.md` (executors MUST read it alongside this plan — it carries the rationale, the review history, and the safety invariants this plan implements).

## Global Constraints

- **Commit discipline (OVERRIDES the writing-plans skill):** no per-task commits, no micro-commits. Build the whole feature with TDD; each task ends at "tests green." A **single** commit (doc + ADR + code + tests) happens only at the end, after `ruff` + `pyright` + `pytest` are all green **and** Karsten has explicitly approved that specific commit. The spec/plan/ADR docs land *in that same commit*, never earlier.
- **Branch:** do the work on one feature branch off `main` (e.g. `feat/skillcorner-canonical-parquet`); no worktrees. Open the PR from it after approval.
- **Owner-tier data is irreplaceable:** every destructive S3 op (overwrite/delete) is gated on a re-fetched, verified *stored* object. Never delete-then-verify.
- **No licensed data in the repo:** test fixtures are wholly invented (synthetic match/player ids, coordinates, event values); never renamed real SkillCorner rows. No operator-local paths in committed files — use env-var placeholders.
- **Ruff line-length 120; pyright basic mode; tests live in `src/tests/`.**
- **Scope this cycle:** write to S3 for **RM (migrate)** + **PL 2025/26 (ingest)** only. PL 24/25 + CL readers/transforms are built and unit-tested but **not run** against S3 (held pending the missing-data answer).

---

## File Structure

**Create:**
- `src/formats/skillcorner_canonical.py` — pure transforms (tracking ⇄ Parquet, events/physical → Parquet/zstd, equivalence relation).
- `src/formats/skillcorner_events_reference.py` — loader for the pinned events reference schema.
- `schemas/skillcorner_events_reference.json` — pinned column→Arrow-dtype map (committed; seeded once).
- `src/formats/skillcorner_raw.py` — raw-JSON-family reader (CL 25/26, PL 25/26).
- `scripts/seed_skillcorner_events_reference.py` — one-time seeder for the reference schema.
- `scripts/migrate_skillcorner_tracking_parquet.py` — RM in-place S3 migration adapter.
- `scripts/upload_skillcorner_raw.py` — raw-JSON-family ingest adapter (PL 25/26 this cycle).
- `scripts/verify_skillcorner_canonical_load.py` — post-run ops verification.
- `docs/decisions/0011-canonical-owner-tier-skillcorner-format.md` — ADR.
- Tests: `src/tests/test_skillcorner_canonical.py`, `src/tests/test_skillcorner_raw.py`, `src/tests/test_skillcorner_bundle_pl.py`, `src/tests/test_migrate_skillcorner_tracking.py`, `src/tests/test_upload_skillcorner_raw.py`, `src/tests/test_match_entry_format_version.py`.

**Modify:**
- `src/formats/skillcorner_bundle.py` — **additive only:** add `REQUIRED_ROLES` + `missing_required` (metadata/tracking/events required; physical/freeze not required, tolerating PL 24/25's missing layers). `ARTIFACT_SPECS`/`source_files`/`is_complete` are left unchanged (see Task 6).
- `src/canonical/models.py` — `MatchEntry` gains optional `format_version: int | None`.
- `src/mock_api/upload.py` — `upload_game` accepts `format_version` and threads it into the `MatchEntry`.
- `schemas/matches.schema.json` — regenerated via `scripts/regenerate_schemas.py`.
- `docs/decisions/README.md` — add the 0011 row.
- `CLAUDE.md` — architecture note: owner-tier SkillCorner canonical Parquet/zstd format.

---

## Task 1: Pinned events reference schema (seed + loader)

**Files:**
- Create: `scripts/seed_skillcorner_events_reference.py`
- Create: `schemas/skillcorner_events_reference.json`
- Create: `src/formats/skillcorner_events_reference.py`
- Test: `src/tests/test_skillcorner_canonical.py` (shared test module; this task adds the reference-schema tests)

**Interfaces:**
- Produces: `load_events_reference_schema() -> dict[str, str]` (column name → Arrow type string, e.g. `{"event_id": "int64", "x_start": "double", ...}`); `reference_arrow_schema(extra_cols: dict[str, str]) -> pyarrow.Schema`.

**Confidentiality note (surface at review):** the committed JSON is a *column-name → dtype* map, not data values. SkillCorner's event column vocabulary is already public via the openly-redistributed A-League `*_dynamic_events.csv`; the restricted part is the row *values*, which are not in this file. If Karsten judges the full column list too exposing for the public repo, the fallback is to store the JSON operator-side and load via `$SKILLCORNER_EVENTS_REF`. **Two consequences of that fallback to settle first:** (a) with no committed `schemas/skillcorner_events_reference.json`, `test_reference_schema_loads...` cannot run in CI — it would need a skip-if-absent guard or a committed *synthetic* reference; (b) determinism: seed the reference **once** from a specific documented authored events Parquet, after which the committed JSON is the source of truth (re-seeding uses the same recorded source — record *which* match in the seed commit, not in this public plan).

- [ ] **Step 1: Write the failing test for the loader**

```python
# src/tests/test_skillcorner_canonical.py
import json
from formats.skillcorner_events_reference import load_events_reference_schema, reference_arrow_schema

def test_reference_schema_loads_as_name_to_type_map():
    schema = load_events_reference_schema()
    assert isinstance(schema, dict) and schema
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in schema.items())
    # a couple of well-known SkillCorner event columns are present and typed
    assert schema["event_id"]  # exists
    assert "x_start" in schema

def test_reference_arrow_schema_appends_extra_nullable_columns():
    import pyarrow as pa
    extra = {"new_metric": "double"}
    sch = reference_arrow_schema(extra)
    names = sch.names
    assert "event_id" in names and "new_metric" in names
    assert sch.field("new_metric").nullable
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest src/tests/test_skillcorner_canonical.py -k reference -v`
Expected: FAIL (module `formats.skillcorner_events_reference` not found).

- [ ] **Step 3: Seed the reference JSON from an authored events Parquet**

Write `scripts/seed_skillcorner_events_reference.py`: read an authored SkillCorner events Parquet whose path comes from `$SKILLCORNER_EVENTS_REF_SOURCE` (an operator-local file — never hard-code a path), extract `{column: str(arrow_type)}` in file order, and write `schemas/skillcorner_events_reference.json` (sorted keys off; preserve column order via a JSON array of `[name, type]` pairs to keep ordering deterministic).

```python
# scripts/seed_skillcorner_events_reference.py
import json, os, sys
from pathlib import Path
import pyarrow.parquet as pq

def main() -> None:
    src = os.environ.get("SKILLCORNER_EVENTS_REF_SOURCE")
    if not src:
        sys.exit("set $SKILLCORNER_EVENTS_REF_SOURCE to an authored events .parquet")
    schema = pq.read_schema(src)
    pairs = [[f.name, str(f.type)] for f in schema]
    out = Path(__file__).resolve().parents[1] / "schemas" / "skillcorner_events_reference.json"
    out.write_text(json.dumps({"columns": pairs}, indent=2), encoding="utf-8")
    print(f"wrote {out} ({len(pairs)} columns)")

if __name__ == "__main__":
    main()
```

Run it once (operator step): `SKILLCORNER_EVENTS_REF_SOURCE=<authored events.parquet> python scripts/seed_skillcorner_events_reference.py`. Commit the resulting `schemas/skillcorner_events_reference.json`.

- [ ] **Step 4: Write the loader module**

```python
# src/formats/skillcorner_events_reference.py
from __future__ import annotations
import json
from pathlib import Path
import pyarrow as pa

_REF = Path(__file__).resolve().parents[2] / "schemas" / "skillcorner_events_reference.json"

def load_events_reference_schema() -> dict[str, str]:
    """Ordered {column: arrow-type-string} map from the pinned reference JSON."""
    data = json.loads(_REF.read_text(encoding="utf-8"))
    return {name: typ for name, typ in data["columns"]}

def reference_arrow_schema(extra_cols: dict[str, str] | None = None) -> pa.Schema:
    """Build a pyarrow schema: pinned reference columns first, then extra columns (nullable)."""
    fields = [pa.field(name, pa.type_for_alias(typ), nullable=True)
              for name, typ in load_events_reference_schema().items()]
    for name, typ in (extra_cols or {}).items():
        fields.append(pa.field(name, pa.type_for_alias(typ), nullable=True))
    return pa.schema(fields)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest src/tests/test_skillcorner_canonical.py -k reference -v`
Expected: PASS.

---

## Task 2: Tracking transforms + equivalence relation (the core)

**Files:**
- Create: `src/formats/skillcorner_canonical.py`
- Test: `src/tests/test_skillcorner_canonical.py`

**Interfaces:**
- Produces: `tracking_to_parquet(frames: list[dict]) -> bytes`; `parquet_to_tracking(parquet_bytes: bytes) -> list[dict]`; `frames_equivalent(a: list[dict], b: list[dict]) -> bool`.
- Consumes: nothing.

The nested schema (spec §4.1): one row per frame; `frame:int32`, `timestamp:string?`, `period:int8?`, `ball_data:struct{x,y,z:double, is_detected:bool}`, `possession:struct{player_id:int64?, group:string?}`, `image_corners_projection:struct{8 doubles}`, `player_data:list<struct{x,y:double, player_id:int64?, is_detected:bool}>`. Compression zstd.

- [ ] **Step 1: Write the failing round-trip + equivalence tests**

Use a **wholly-synthetic** fixture covering the spec §4.2 edge cases. Expected outcomes are stated per the equivalence relation (int coords PASS via float widening; absent optional keys PASS via null normalization; a genuine value change ABORTs).

```python
# src/tests/test_skillcorner_canonical.py (append)
from formats.skillcorner_canonical import tracking_to_parquet, parquet_to_tracking, frames_equivalent

def _ball(x=1.5, y=2.5, z=0.0, det=True):
    return {"x": x, "y": y, "z": z, "is_detected": det}

def _corners(v=None):
    keys = ["x_top_left","y_top_left","x_bottom_left","y_bottom_left",
            "x_bottom_right","y_bottom_right","x_top_right","y_top_right"]
    return {k: v for k in keys}

def _frame(n, players, ball=None, ts="00:00:00.00", period=1, poss=None, corners=None):
    return {"frame": n, "timestamp": ts, "period": period,
            "ball_data": ball if ball is not None else _ball(),
            "possession": poss if poss is not None else {"player_id": 7, "group": "home"},
            "image_corners_projection": corners if corners is not None else _corners(1.0),
            "player_data": players}

SYNTHETIC = [
    _frame(0, [], ball=_ball(None, None, None, None), ts=None, period=None,
           poss={"player_id": None, "group": None}, corners=_corners(None)),   # empty/pre-kickoff
    _frame(1, [{"x": 3.0, "y": 4.0, "player_id": 101, "is_detected": True},
               {"x": 5.5, "y": 6.5, "player_id": None, "is_detected": False}]),  # null player_id
    _frame(2, [{"x": 0, "y": 0, "player_id": 202, "is_detected": True}]),        # integer coords
    _frame(3, [{"x": 7.0, "y": 8.0, "player_id": 303, "is_detected": True}], period=2),
]

def test_tracking_parquet_round_trip_is_equivalent():
    reconstructed = parquet_to_tracking(tracking_to_parquet(SYNTHETIC))
    assert frames_equivalent(SYNTHETIC, reconstructed)

def test_integer_coordinate_frame_passes_after_float_widening():
    # frame 2 has integer coords; storage is float64 -> must be judged equivalent
    reconstructed = parquet_to_tracking(tracking_to_parquet(SYNTHETIC))
    assert reconstructed[2]["player_data"][0]["x"] == 0.0
    assert frames_equivalent([SYNTHETIC[2]], [reconstructed[2]])

def test_absent_optional_key_normalizes_to_null_and_passes():
    src = [_frame(9, [{"x": 1.0, "y": 2.0, "player_id": 1, "is_detected": True}])]
    del src[0]["possession"]  # absent optional key
    assert frames_equivalent(src, parquet_to_tracking(tracking_to_parquet(src)))

def test_genuine_value_change_aborts():
    a = parquet_to_tracking(tracking_to_parquet(SYNTHETIC))
    a[1]["player_data"][0]["x"] = 999.0  # corrupt a value
    assert not frames_equivalent(SYNTHETIC, a)

def test_integer_typed_column_type_change_aborts():
    a = parquet_to_tracking(tracking_to_parquet(SYNTHETIC))
    a[0]["frame"] = 0.0  # frame must stay int
    assert not frames_equivalent(SYNTHETIC, a)

def test_written_parquet_uses_the_fixed_schema():
    import io
    import pyarrow.parquet as pq
    from formats.skillcorner_canonical import TRACKING_SCHEMA
    schema = pq.read_schema(io.BytesIO(tracking_to_parquet(SYNTHETIC)))
    assert schema.field("frame").type == "int32"   # not inferred int64
    assert schema.field("period").type == "int8"
    assert schema.equals(TRACKING_SCHEMA)

def test_unexpected_source_field_raises_clear_error():
    import pytest
    bad = [dict(SYNTHETIC[1], surprise_field=1)]   # a field not in the pinned schema
    with pytest.raises(ValueError, match="unexpected tracking field"):
        tracking_to_parquet(bad)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest src/tests/test_skillcorner_canonical.py -k tracking -v`
Expected: FAIL (`formats.skillcorner_canonical` not found).

- [ ] **Step 3: Implement the tracking transforms + equivalence relation**

```python
# src/formats/skillcorner_canonical.py
from __future__ import annotations
import io
import pyarrow as pa
import pyarrow.parquet as pq

# Integer-typed tracking columns: value AND type must match (spec §4.2).
_INT_SCALARS = {"frame", "period", "player_id"}

# Fixed nested schema (spec §4.1) — pinned so EVERY match writes an identical schema.
# from_pylist WITHOUT a schema would infer frame/period as int64 (spec: int32/int8) and
# coord types data-dependently, defeating "one canonical format" and the §4.1 contract.
_CORNER_KEYS = ("x_top_left", "y_top_left", "x_bottom_left", "y_bottom_left",
                "x_bottom_right", "y_bottom_right", "x_top_right", "y_top_right")
_BALL = pa.struct([("x", pa.float64()), ("y", pa.float64()), ("z", pa.float64()), ("is_detected", pa.bool_())])
_POSS = pa.struct([("player_id", pa.int64()), ("group", pa.string())])
_CORNERS = pa.struct([(k, pa.float64()) for k in _CORNER_KEYS])
_PLAYER = pa.struct([("x", pa.float64()), ("y", pa.float64()), ("player_id", pa.int64()), ("is_detected", pa.bool_())])
TRACKING_SCHEMA = pa.schema([
    ("frame", pa.int32()), ("timestamp", pa.string()), ("period", pa.int8()),
    ("ball_data", _BALL), ("possession", _POSS),
    ("image_corners_projection", _CORNERS), ("player_data", pa.list_(_PLAYER)),
])

def tracking_to_parquet(frames: list[dict]) -> bytes:
    """Nested one-row-per-frame Parquet (zstd) under the fixed TRACKING_SCHEMA (spec §4.1)."""
    allowed = set(TRACKING_SCHEMA.names)
    for fr in frames:  # clear diagnostic if a NEW source field appears (the schema is pinned)
        extra = set(fr) - allowed
        if extra:
            raise ValueError(f"unexpected tracking field(s) {sorted(extra)} — schema is pinned; "
                             f"update TRACKING_SCHEMA (spec §4.1)")
    table = pa.Table.from_pylist(frames, schema=TRACKING_SCHEMA)
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    return buf.getvalue()

def parquet_to_tracking(parquet_bytes: bytes) -> list[dict]:
    """Inverse of tracking_to_parquet — reconstruct the frame list."""
    table = pq.read_table(io.BytesIO(parquet_bytes))
    return table.to_pylist()

def _scalar_equal(key: str, a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if key in _INT_SCALARS:
        return type(a) is type(b) and a == b            # type-strict on int columns
    if isinstance(a, float) or isinstance(b, float):
        return float(a) == float(b)                     # float coords: numeric after widening
    return a == b                                       # bool / string

def _norm(v):
    """Normalize absent-vs-null: treat a missing key as an explicit None (handled by callers)."""
    return v

def _frame_equal(a: dict, b: dict) -> bool:
    keys = set(a) | set(b)
    for k in keys:
        av, bv = a.get(k), b.get(k)
        if isinstance(av, dict) or isinstance(bv, dict):
            if not _struct_equal(av or {}, bv or {}):
                return False
        elif isinstance(av, list) or isinstance(bv, list):
            av, bv = av or [], bv or []
            if len(av) != len(bv) or any(not _struct_equal(x, y) for x, y in zip(av, bv)):
                return False
        elif not _scalar_equal(k, av, bv):
            return False
    return True

def _struct_equal(a: dict, b: dict) -> bool:
    for k in set(a) | set(b):
        av, bv = a.get(k), b.get(k)
        if isinstance(av, dict) or isinstance(bv, dict):
            if not _struct_equal(av or {}, bv or {}):
                return False
        elif not _scalar_equal(k, av, bv):
            return False
    return True

def frames_equivalent(a: list[dict], b: list[dict]) -> bool:
    """Spec §4.2 equivalence: absent-key→null normalized; per-column-type comparison."""
    return len(a) == len(b) and all(_frame_equal(x, y) for x, y in zip(a, b))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest src/tests/test_skillcorner_canonical.py -k tracking -v`
Expected: PASS (all five tracking tests).

---

## Task 3: Events + physical transforms

**Files:**
- Modify: `src/formats/skillcorner_canonical.py`
- Test: `src/tests/test_skillcorner_canonical.py`

**Interfaces:**
- Produces: `recompress_parquet_zstd(parquet_bytes: bytes) -> bytes`; `events_csv_to_parquet(csv_bytes: bytes) -> bytes`; `physical_json_to_per_match_parquet(results: list[dict]) -> dict[str, bytes]`.

- [ ] **Step 1: Write the failing tests**

```python
# src/tests/test_skillcorner_canonical.py (append)
import io, pandas as pd, pyarrow.parquet as pq
from formats.skillcorner_canonical import (
    recompress_parquet_zstd, events_csv_to_parquet, physical_json_to_per_match_parquet)

def _parquet_bytes(df, compression="snappy"):
    buf = io.BytesIO(); df.to_parquet(buf, compression=compression); return buf.getvalue()

def test_recompress_preserves_values_and_uses_zstd():
    df = pd.DataFrame({"a": [1, 2, 3], "b": [1.5, 2.5, 3.5]})
    out = recompress_parquet_zstd(_parquet_bytes(df, "snappy"))
    meta = pq.read_metadata(io.BytesIO(out))
    assert meta.row_group(0).column(0).compression.lower() == "zstd"
    pd.testing.assert_frame_equal(pd.read_parquet(io.BytesIO(out)), df)

def test_events_csv_conforms_shared_columns_and_keeps_extras():
    # 'event_id' is in the reference; 'new_metric' is not -> appended nullable
    csv = b"event_id,x_start,new_metric\n1,0,9.9\n2,1,\n"
    out = events_csv_to_parquet(csv)
    t = pq.read_table(io.BytesIO(out))
    assert str(t.schema.field("event_id").type) == "int64"   # matches reference dtype
    assert "new_metric" in t.schema.names
    assert t.num_rows == 2

def test_physical_split_by_match_id():
    results = [{"match_id": "m1", "player_id": 1, "psv99": 30.0},
               {"match_id": "m1", "player_id": 2, "psv99": 31.0},
               {"match_id": "m2", "player_id": 3, "psv99": 29.0}]
    out = physical_json_to_per_match_parquet(results)
    assert set(out) == {"m1", "m2"}
    assert pq.read_table(io.BytesIO(out["m1"])).num_rows == 2

def test_events_lossy_cast_aborts():
    import pytest
    # 'event_id' is int64 in the reference; a non-integral value cannot cast without loss -> abort
    with pytest.raises(Exception):
        events_csv_to_parquet(b"event_id,x_start\n1.5,0\n")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest src/tests/test_skillcorner_canonical.py -k "recompress or events_csv or physical_split" -v`
Expected: FAIL (functions not defined).

- [ ] **Step 3: Implement the transforms**

```python
# src/formats/skillcorner_canonical.py (append)
import pandas as pd
from collections import defaultdict
from formats.skillcorner_events_reference import load_events_reference_schema, reference_arrow_schema

def recompress_parquet_zstd(parquet_bytes: bytes) -> bytes:
    table = pq.read_table(io.BytesIO(parquet_bytes))
    buf = io.BytesIO(); pq.write_table(table, buf, compression="zstd"); return buf.getvalue()

def events_csv_to_parquet(csv_bytes: bytes) -> bytes:
    df = pd.read_csv(io.BytesIO(csv_bytes))
    ref = load_events_reference_schema()
    table = pa.Table.from_pandas(df, preserve_index=False)
    # extra (newer-season) columns keep their inferred type; shared columns conform to the reference
    extra_cols = {c: str(table.schema.field(c).type) for c in table.schema.names if c not in ref}
    target = reference_arrow_schema(extra_cols)
    ordered = [f.name for f in target if f.name in table.schema.names]
    target_ordered = pa.schema([target.field(n) for n in ordered])
    # SAFE cast (default safe=True): a value that cannot be represented in the reference dtype
    # RAISES rather than silently truncating/overflowing — preserves §5.1 "values unchanged".
    table = table.select(ordered).cast(target_ordered)
    buf = io.BytesIO(); pq.write_table(table, buf, compression="zstd"); return buf.getvalue()

def physical_json_to_per_match_parquet(results: list[dict]) -> dict[str, bytes]:
    by_match: dict[str, list[dict]] = defaultdict(list)
    for row in results:
        by_match[str(row["match_id"])].append(row)
    out: dict[str, bytes] = {}
    for mid, rows in by_match.items():
        table = pa.Table.from_pylist(rows)
        buf = io.BytesIO(); pq.write_table(table, buf, compression="zstd"); out[mid] = buf.getvalue()
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest src/tests/test_skillcorner_canonical.py -v`
Expected: PASS (whole canonical module).

---

## Task 4: `MatchEntry.format_version` + `upload_game` + schema regen

**Files:**
- Modify: `src/canonical/models.py`
- Modify: `src/mock_api/upload.py:21-154` (`upload_game` signature + `_build_match_entry`, which spans l.120-154)
- Modify: `schemas/matches.schema.json` (regenerated)
- Test: `src/tests/test_match_entry_format_version.py`

**Interfaces:**
- Produces: `MatchEntry(..., format_version: int | None = None)`; `upload_game(..., format_version: int | None = None)`.

- [ ] **Step 1: Write the failing test**

```python
# src/tests/test_match_entry_format_version.py
from canonical.models import MatchEntry

def test_format_version_optional_and_absent_by_default():
    e = MatchEntry.model_validate({"id": "m1", "artifacts": {"tracking": "tracking.parquet"},
                                   "visibility": "private", "updated_at": "2026-09-01T00:00:00Z"})
    assert e.model_dump(exclude_none=True).get("format_version") is None

def test_format_version_recorded_when_set():
    e = MatchEntry.model_validate({"id": "m1", "artifacts": {"tracking": "tracking.parquet"},
                                   "visibility": "private", "updated_at": "2026-09-01T00:00:00Z",
                                   "format_version": 2})
    assert e.model_dump(exclude_none=True)["format_version"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest src/tests/test_match_entry_format_version.py -v`
Expected: FAIL (extra field not surfaced as a typed attribute — depending on current model, the `== 2` assertion fails or the attribute is absent).

- [ ] **Step 3: Add the field + thread it through `upload_game`**

In `src/canonical/models.py`, add to `MatchEntry` (keep `extra="allow"` as-is):

```python
    format_version: int | None = None
```

In `src/mock_api/upload.py`, add `format_version: int | None = None` to `upload_game`'s signature and to `_build_match_entry`'s signature, and set it in the payload:

```python
    if format_version is not None:
        payload["format_version"] = format_version
```

Thread `format_version=format_version` from `upload_game` into the `_build_match_entry(...)` call.

- [ ] **Step 4: Regenerate the schema and run tests**

Run: `python scripts/regenerate_schemas.py` (updates `schemas/matches.schema.json`).
Run: `python -m pytest src/tests/test_match_entry_format_version.py src/tests/ -k "schema" -v`
Expected: PASS, and the schema-drift test stays green (the field is additive; `extra="allow"` keeps it schema-safe).

---

## Task 5: Raw-JSON-family reader (`skillcorner_raw.py`)

**Files:**
- Create: `src/formats/skillcorner_raw.py`
- Test: `src/tests/test_skillcorner_raw.py`

**Interfaces:**
- Produces: `discover_manifest_matches(manifest_csv: bytes) -> list[str]`; `raw_role_files(match_id: str) -> dict[str, str]` (role → source-relative path in the raw layout); reuses `formats.skillcorner_bundle.match_info` / `players_from_meta`.

- [ ] **Step 1: Write the failing tests**

```python
# src/tests/test_skillcorner_raw.py
from formats.skillcorner_raw import discover_manifest_matches, raw_role_files

def test_discover_reads_manifest_ids_skips_header():
    csv = b"match_id\n1001\n1002\n1003\n"
    assert discover_manifest_matches(csv) == ["1001", "1002", "1003"]

def test_raw_role_files_maps_layout():
    m = raw_role_files("1001")
    assert m["metadata"].endswith("matches/1001.json")
    assert m["tracking"].endswith("tracking/1001.json")
    assert m["events"].endswith("dynamic_events/1001.json")  # CSV-mislabelled-.json
    assert "freeze_frames" not in m
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest src/tests/test_skillcorner_raw.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the reader**

```python
# src/formats/skillcorner_raw.py
from __future__ import annotations

# role -> (subdir, extension) for the raw-JSON family (spec §2.1/§6.2)
RAW_ROLE_LAYOUT: dict[str, tuple[str, str]] = {
    "metadata": ("matches", ".json"),
    "tracking": ("tracking", ".json"),
    "events": ("dynamic_events", ".json"),  # actually CSV; content-sniffed downstream
}

def discover_manifest_matches(manifest_csv: bytes) -> list[str]:
    """Match ids from available_dynamic_event_match_ids.csv, skipping the header."""
    lines = manifest_csv.decode("utf-8").splitlines()
    return [ln.strip() for ln in lines[1:] if ln.strip()]

def raw_role_files(match_id: str) -> dict[str, str]:
    """Map each role to its source-relative path in the raw layout (physical is a shared file)."""
    return {role: f"{sub}/{match_id}{ext}" for role, (sub, ext) in RAW_ROLE_LAYOUT.items()}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest src/tests/test_skillcorner_raw.py -v`
Expected: PASS.

---

## Task 6: Parquet-family reader — relaxed completeness for PL 24/25 (additive only)

**Files:**
- Modify: `src/formats/skillcorner_bundle.py` — **ADD symbols only.** `ARTIFACT_SPECS`, `source_files`, `missing_artifacts`, `is_complete` are LEFT UNCHANGED.
- Test: `src/tests/test_skillcorner_bundle_pl.py`

**Why additive (resolves review CANON-PLAN-05):** `test_skillcorner_bundle_format.py:128` pins the 5-role `source_files` set and `:34` includes `freeze_frames`; `upload_skillcorner_realmadrid.py:stage_match` iterates `ARTIFACT_SPECS`. Removing `freeze_frames` from `ARTIFACT_SPECS` would break those tests and silently change the historical uploader — and it is unnecessary. `ARTIFACT_SPECS` describes the *source* bundle layout (RM source really does contain freeze); the canonical *output* drops freeze via the transforms + migration, a separate concern. So we add a relaxed completeness check and change nothing existing.

**Interfaces:**
- Produces: `REQUIRED_ROLES` (`{"metadata", "tracking", "events"}`); `missing_required(root, match_id) -> list[str]` (checks only the required roles; `physical`/`freeze` never force incompleteness). `source_files` still returns all 5 roles, so `files[role]` for the 3 required roles is always present.

- [ ] **Step 1: Write the failing tests** (temp dir mimicking PL 24/25: `meta/`, `tracking/`, `dynamic/` present; no `freeze/`/`physical/`).

```python
# src/tests/test_skillcorner_bundle_pl.py
from pathlib import Path
from formats.skillcorner_bundle import missing_required, REQUIRED_ROLES

def _make_pl_match(tmp_path: Path, mid: str) -> Path:
    for sub, ext in [("meta", ".json"), ("tracking", ".json"), ("dynamic", ".parquet")]:
        d = tmp_path / sub; d.mkdir(exist_ok=True); (d / f"{mid}{ext}").write_text("{}")
    return tmp_path

def test_pl_match_complete_without_freeze_or_physical(tmp_path):
    root = _make_pl_match(tmp_path, "500")
    assert missing_required(root, "500") == []       # complete on required roles only

def test_missing_required_reports_absent_required_role(tmp_path):
    root = _make_pl_match(tmp_path, "500")
    (root / "tracking" / "500.json").unlink()
    assert missing_required(root, "500") == ["tracking"]

def test_required_roles_exclude_freeze_and_physical():
    assert REQUIRED_ROLES == {"metadata", "tracking", "events"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest src/tests/test_skillcorner_bundle_pl.py -v`
Expected: FAIL (`missing_required`/`REQUIRED_ROLES` not defined).

- [ ] **Step 3: Implement (additive)** — in `skillcorner_bundle.py`, add these symbols; do NOT modify `ARTIFACT_SPECS`, `source_files`, `missing_artifacts`, or `is_complete`:

```python
REQUIRED_ROLES = {"metadata", "tracking", "events"}

def missing_required(root: Path, match_id: str) -> list[str]:
    """Required roles whose source file is absent. physical/freeze are optional and ignored."""
    files = source_files(root, match_id)
    return sorted(role for role in REQUIRED_ROLES if not files[role].is_file())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest src/tests/test_skillcorner_bundle_pl.py src/tests/test_skillcorner_bundle_format.py -v`
Expected: PASS — the new PL tests pass **and** every existing `test_skillcorner_bundle_format.py` test stays green (nothing existing was touched).

---

## Task 7: RM in-place S3 migration adapter

**Files:**
- Create: `scripts/migrate_skillcorner_tracking_parquet.py`
- Test: `src/tests/test_migrate_skillcorner_tracking.py`

**Interfaces:**
- Produces: `migrate_match(s3, bucket: str, match_id: str, *, dry_run: bool) -> str` (returns an action summary); `migrate_all(s3, bucket, *, limit, dry_run)`. The `s3` client is injected (defaults to `boto3.client("s3")` in `main`), so tests pass a fake — no network, no moto dependency.

Implements spec §7.1 exactly: write-new → **verify STORED object from S3** → repoint index → delete-old; events/physical stage→verify→swap; self-healing re-run.

- [ ] **Step 1: Write the failing tests with an in-memory fake S3**

```python
# src/tests/test_migrate_skillcorner_tracking.py
import gzip, io, json
import pyarrow as pa, pyarrow.parquet as pq
from scripts.migrate_skillcorner_tracking_parquet import migrate_match, _guard_key

class FakeS3:
    """Minimal in-memory S3 double: get/put/copy/delete + head + a corrupt-put hook."""
    def __init__(self): self.store = {}; self.corrupt_key = None
    def get_object(self, Bucket, Key):
        if Key not in self.store: raise KeyError(Key)
        return {"Body": io.BytesIO(self.store[Key])}
    def put_object(self, Bucket, Key, Body, **kw):
        data = Body if isinstance(Body, bytes) else Body.read()
        if Key == self.corrupt_key: data = data[: len(data) // 2]  # truncated PUT
        self.store[Key] = data
    def upload_fileobj(self, Fileobj, Bucket, Key): self.store[Key] = Fileobj.read()
    def copy_object(self, Bucket, Key, CopySource): self.store[Key] = self.store[CopySource["Key"]]
    def delete_object(self, Bucket, Key): self.store.pop(Key, None)
    def head_object(self, Bucket, Key):
        if Key not in self.store: raise KeyError(Key)
        return {"ContentLength": len(self.store[Key])}

def _seed_rm_match(s3, bucket, mid):
    pref = f"skillcorner/_private/{mid}"
    frames = [{"frame": 0, "timestamp": "00:00:00.00", "period": 1,
               "ball_data": {"x": 1.0, "y": 2.0, "z": 0.0, "is_detected": True},
               "possession": {"player_id": 7, "group": "home"},
               "image_corners_projection": {k: 1.0 for k in
                   ["x_top_left","y_top_left","x_bottom_left","y_bottom_left",
                    "x_bottom_right","y_bottom_right","x_top_right","y_top_right"]},
               "player_data": [{"x": 3.0, "y": 4.0, "player_id": 101, "is_detected": True}]}]
    s3.store[f"{pref}/tracking.json.gz"] = gzip.compress(json.dumps(frames).encode())
    for role in ("events", "physical", "freeze_frames"):
        buf = io.BytesIO(); pq.write_table(pa.table({"a": [1]}), buf, compression="snappy")
        s3.store[f"{pref}/{role}.parquet"] = buf.getvalue()
    s3.store[f"{pref}/metadata.json"] = b"{}"
    s3.store["skillcorner/matches.json"] = json.dumps({"provider": "skillcorner", "matches": [
        {"id": mid, "visibility": "private",
         "artifacts": {"tracking": "tracking.json.gz", "events": "events.parquet",
                       "physical": "physical.parquet", "freeze_frames": "freeze_frames.parquet",
                       "metadata": "metadata.json"}, "updated_at": "2026-06-29T00:00:00Z"}]}).encode()

def test_migrate_produces_canonical_and_removes_legacy():
    s3 = FakeS3(); _seed_rm_match(s3, "b", "1001")
    migrate_match(s3, "b", "1001", dry_run=False)
    keys = set(s3.store)
    assert "skillcorner/_private/1001/tracking.parquet" in keys
    assert "skillcorner/_private/1001/tracking.json.gz" not in keys      # legacy gone
    assert "skillcorner/_private/1001/freeze_frames.parquet" not in keys  # freeze dropped
    idx = json.loads(s3.store["skillcorner/matches.json"])["matches"][0]
    assert idx["artifacts"]["tracking"] == "tracking.parquet"
    assert "freeze_frames" not in idx["artifacts"]
    assert idx["format_version"] == 2
    # events value-preservation: the recompressed events.parquet holds the same table
    ev = pq.read_table(io.BytesIO(s3.store["skillcorner/_private/1001/events.parquet"]))
    assert ev.equals(pa.table({"a": [1]}))

def test_corrupt_stored_tracking_blocks_delete():
    s3 = FakeS3(); _seed_rm_match(s3, "b", "1001")
    s3.corrupt_key = "skillcorner/_private/1001/tracking.parquet"  # truncate the PUT
    try:
        migrate_match(s3, "b", "1001", dry_run=False)
    except Exception:
        pass
    assert "skillcorner/_private/1001/tracking.json.gz" in s3.store  # NOT deleted

def test_rerun_is_noop_but_sweeps_orphans():
    s3 = FakeS3(); _seed_rm_match(s3, "b", "1001")
    migrate_match(s3, "b", "1001", dry_run=False)
    before = dict(s3.store)
    migrate_match(s3, "b", "1001", dry_run=False)   # idempotent
    assert set(s3.store) == set(before)

def test_dry_run_mutates_nothing():
    s3 = FakeS3(); _seed_rm_match(s3, "b", "1001"); before = dict(s3.store)
    migrate_match(s3, "b", "1001", dry_run=True)
    assert s3.store == before

def test_guard_refuses_public_key():
    import pytest
    with pytest.raises(ValueError, match="non-private"):
        _guard_key("skillcorner/1886347/tracking.parquet")   # a PUBLIC key

def test_rerun_sweeps_orphaned_legacy():
    s3 = FakeS3(); _seed_rm_match(s3, "b", "1001")
    migrate_match(s3, "b", "1001", dry_run=False)
    # simulate a crash AFTER the index flipped to v2 but BEFORE the legacy delete
    s3.store["skillcorner/_private/1001/tracking.json.gz"] = b"orphan"
    s3.store["skillcorner/_private/1001/freeze_frames.parquet"] = b"orphan"
    migrate_match(s3, "b", "1001", dry_run=False)   # re-run must sweep them
    assert "skillcorner/_private/1001/tracking.json.gz" not in s3.store
    assert "skillcorner/_private/1001/freeze_frames.parquet" not in s3.store
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest src/tests/test_migrate_skillcorner_tracking.py -v`
Expected: FAIL (script/functions not defined).

- [ ] **Step 3: Implement the migration adapter** (spec §7.1 ordering — abort before any mutation on a failed stored-object gate; only touch `_private/` keys).

```python
# scripts/migrate_skillcorner_tracking_parquet.py
from __future__ import annotations
import argparse, gzip, io, json, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import boto3  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402
from formats.skillcorner_canonical import (  # noqa: E402
    tracking_to_parquet, parquet_to_tracking, frames_equivalent, recompress_parquet_zstd)

PROVIDER = "skillcorner"

def _prefix(mid: str) -> str:
    return f"{PROVIDER}/_private/{mid}"

def _guard_key(key: str) -> str:
    """Refuse to mutate any key outside the private tier (the shared index is the sole exception)."""
    if key == f"{PROVIDER}/matches.json":
        return key
    if not key.startswith(f"{PROVIDER}/_private/"):
        raise ValueError(f"refusing to touch non-private key: {key}")
    return key

def _get(s3, bucket, key) -> bytes:
    return s3.get_object(Bucket=bucket, Key=key)["Body"].read()

def _put(s3, bucket, key, body, **kw) -> None:
    s3.put_object(Bucket=bucket, Key=_guard_key(key), Body=body, **kw)

def _delete(s3, bucket, key) -> None:
    s3.delete_object(Bucket=bucket, Key=_guard_key(key))

def _copy(s3, bucket, dst, src) -> None:
    s3.copy_object(Bucket=bucket, Key=_guard_key(dst), CopySource={"Bucket": bucket, "Key": src})

def _tables_equal(a: bytes, b: bytes) -> bool:
    return pq.read_table(io.BytesIO(a)).equals(pq.read_table(io.BytesIO(b)))

def migrate_match(s3, bucket: str, match_id: str, *, dry_run: bool) -> str:
    pref = _prefix(match_id)
    idx = json.loads(_get(s3, bucket, f"{PROVIDER}/matches.json"))
    entry = next(m for m in idx["matches"] if m["id"] == match_id)

    already = entry.get("format_version") == 2
    if already:
        # self-heal: sweep any legacy artifacts left by a crash between index-update and delete
        if not dry_run:
            for legacy in ("tracking.json.gz", "freeze_frames.parquet"):
                _delete(s3, bucket, f"{pref}/{legacy}")
        return f"{match_id}: already canonical (orphans swept)"

    frames = json.loads(gzip.decompress(_get(s3, bucket, f"{pref}/tracking.json.gz")))
    tracking_parquet = tracking_to_parquet(frames)
    if not frames_equivalent(frames, parquet_to_tracking(tracking_parquet)):   # transform gate
        raise ValueError(f"{match_id}: in-memory tracking round-trip failed")

    events_orig = _get(s3, bucket, f"{pref}/events.parquet")
    physical_orig = _get(s3, bucket, f"{pref}/physical.parquet")
    events_new = recompress_parquet_zstd(events_orig)
    physical_new = recompress_parquet_zstd(physical_orig)
    # in-memory value gate (spec §7.1 step 4): recompress must preserve the table exactly
    if not _tables_equal(events_orig, events_new):
        raise ValueError(f"{match_id}: events recompress altered values")
    if not _tables_equal(physical_orig, physical_new):
        raise ValueError(f"{match_id}: physical recompress altered values")

    if dry_run:
        return f"{match_id}: DRY-RUN would migrate (tracking + events/physical + drop freeze)"

    # upload new: tracking to final key (additive), events/physical to staging keys
    _put(s3, bucket, f"{pref}/tracking.parquet", tracking_parquet)
    _put(s3, bucket, f"{pref}/events.parquet.staging", events_new)
    _put(s3, bucket, f"{pref}/physical.parquet.staging", physical_new)

    # STORED-object gate: re-fetch from S3 and verify before any destructive step
    if not frames_equivalent(frames, parquet_to_tracking(_get(s3, bucket, f"{pref}/tracking.parquet"))):
        raise ValueError(f"{match_id}: stored tracking.parquet failed verification")
    if _get(s3, bucket, f"{pref}/events.parquet.staging") != events_new:
        raise ValueError(f"{match_id}: stored events staging mismatch")
    if _get(s3, bucket, f"{pref}/physical.parquet.staging") != physical_new:
        raise ValueError(f"{match_id}: stored physical staging mismatch")

    # swap events/physical: copy staging -> final, delete staging
    for role in ("events", "physical"):
        _copy(s3, bucket, f"{pref}/{role}.parquet", f"{pref}/{role}.parquet.staging")
        _delete(s3, bucket, f"{pref}/{role}.parquet.staging")

    # repoint index (only references verified, existing files)
    entry["artifacts"]["tracking"] = "tracking.parquet"
    entry["artifacts"].pop("freeze_frames", None)
    entry["format_version"] = 2
    _put(s3, bucket, f"{PROVIDER}/matches.json",
         json.dumps(idx, indent=2).encode(), ContentType="application/json")

    # delete legacy last
    _delete(s3, bucket, f"{pref}/tracking.json.gz")
    _delete(s3, bucket, f"{pref}/freeze_frames.parquet")
    return f"{match_id}: migrated"

def migrate_all(s3, bucket: str, *, limit: int | None, dry_run: bool) -> None:
    idx = json.loads(_get(s3, bucket, f"{PROVIDER}/matches.json"))
    ids = [m["id"] for m in idx["matches"] if m.get("visibility") == "private"]
    for mid in ids[: limit or None]:
        print(migrate_match(s3, bucket, mid, dry_run=dry_run))

def main() -> None:
    ap = argparse.ArgumentParser(description="Migrate RM SkillCorner owner-tier tracking to canonical Parquet")
    ap.add_argument("--bucket", default=os.environ.get("PINING_BUCKET"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--apply", action="store_true", help="apply changes (default is dry-run)")
    args = ap.parse_args()
    if not args.bucket:
        ap.error("--bucket required (or set PINING_BUCKET)")
    migrate_all(boto3.client("s3"), args.bucket, limit=args.limit, dry_run=not args.apply)

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest src/tests/test_migrate_skillcorner_tracking.py -v`
Expected: PASS (all four migration tests, including the corrupt-stored-object safety test).

---

## Task 8: PL 25/26 raw-JSON ingest adapter

**Files:**
- Create: `scripts/upload_skillcorner_raw.py`
- Test: `src/tests/test_upload_skillcorner_raw.py`

**Interfaces:**
- Consumes: `formats.skillcorner_raw`, `formats.skillcorner_canonical`, `formats.skillcorner_bundle.match_info`/`players_from_meta`, `mock_api.upload.upload_game`.
- Produces: `ingest_match(fetch, staging_dir, match_id, physical_rows) -> Path` (transforms one match's raw inputs into a canonical staging dir); a `main()` that fetches from HF and calls `upload_game(..., visibility="private", format_version=2)`. `fetch` is an injected callable `(role_path) -> bytes` so tests avoid HF/network.

- [ ] **Step 1: Write the failing test** (fake `fetch` returning synthetic raw inputs; assert the staging dir holds the 4 canonical files and tracking round-trips).

```python
# src/tests/test_upload_skillcorner_raw.py
import gzip, io, json
import pyarrow.parquet as pq
from pathlib import Path
from scripts.upload_skillcorner_raw import ingest_match
from formats.skillcorner_canonical import parquet_to_tracking, frames_equivalent

def _frames():
    return [{"frame": 0, "timestamp": "00:00:00.00", "period": 1,
             "ball_data": {"x": 1.0, "y": 2.0, "z": 0.0, "is_detected": True},
             "possession": {"player_id": 7, "group": "home"},
             "image_corners_projection": {k: 1.0 for k in
                 ["x_top_left","y_top_left","x_bottom_left","y_bottom_left",
                  "x_bottom_right","y_bottom_right","x_top_right","y_top_right"]},
             "player_data": [{"x": 3.0, "y": 4.0, "player_id": 101, "is_detected": True}]}]

def test_ingest_builds_canonical_staging(tmp_path):
    raw = {"matches/900.json": json.dumps({"id": 900}).encode(),
           "tracking/900.json": json.dumps(_frames()).encode(),
           "dynamic_events/900.json": b"event_id,x_start\n1,0\n2,1\n"}
    staging = ingest_match(lambda p: raw[p], tmp_path, "900",
                           physical_rows=[{"match_id": "900", "player_id": 1, "psv99": 30.0}])
    names = {p.name for p in Path(staging).iterdir()}
    assert names == {"metadata.json", "tracking.parquet", "events.parquet", "physical.parquet"}
    frames = parquet_to_tracking((Path(staging) / "tracking.parquet").read_bytes())
    assert frames_equivalent(_frames(), frames)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest src/tests/test_upload_skillcorner_raw.py -v`
Expected: FAIL (script not defined).

- [ ] **Step 3: Implement the ingest adapter**

```python
# scripts/upload_skillcorner_raw.py
from __future__ import annotations
import json, sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from formats.skillcorner_raw import raw_role_files  # noqa: E402
from formats.skillcorner_canonical import tracking_to_parquet, events_csv_to_parquet, physical_json_to_per_match_parquet  # noqa: E402

def ingest_match(fetch: Callable[[str], bytes], staging_dir: Path, match_id: str,
                 physical_rows: list[dict]) -> Path:
    """Transform one raw match into a canonical staging dir; return the dir path."""
    dest = Path(staging_dir) / match_id
    dest.mkdir(parents=True, exist_ok=True)
    roles = raw_role_files(match_id)
    (dest / "metadata.json").write_bytes(fetch(roles["metadata"]))
    (dest / "tracking.parquet").write_bytes(tracking_to_parquet(json.loads(fetch(roles["tracking"]))))
    (dest / "events.parquet").write_bytes(events_csv_to_parquet(fetch(roles["events"])))
    if physical_rows:
        per = physical_json_to_per_match_parquet(physical_rows)
        if match_id in per:
            (dest / "physical.parquet").write_bytes(per[match_id])
    return dest
```

(`main()` — fetch the manifest + combined `physical.json` from HF via `huggingface_hub.hf_hub_download`, index physical rows by `match_id`, then per manifest match call `ingest_match(...)` and `upload_game(game_dir=staging, provider="skillcorner", visibility="private", format_version=2, ...)` with `date/home/away` from `match_info(metadata)`. Add a dry-run flag. Derive + upload owner-tier players via the existing RM pattern. Full `main()` code mirrors `scripts/upload_skillcorner_realmadrid.py:95-157` — reuse its `derive_players`/`public_player_ids` helpers by importing them.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest src/tests/test_upload_skillcorner_raw.py -v`
Expected: PASS.

---

## Task 9: ADR 0011 + docs

**Files:**
- Create: `docs/decisions/0011-canonical-owner-tier-skillcorner-format.md`
- Modify: `docs/decisions/README.md`
- Modify: `CLAUDE.md`

**Interfaces:** none (documentation).

- [ ] **Step 1: Write ADR 0011** using the spec §10 content — Decision (canonical set + freeze dropped + role keys stable + `format_version`), Consequences (amends the 2026-06-29 RM spec's "format normalization: none" stance; public tier untouched; faithful-archive retained; `freeze_frames` still a valid vocab key for StatsBomb; consumer retools). Status: Accepted.

- [ ] **Step 2: Add the 0011 row to `docs/decisions/README.md`** (mirror the existing table format).

- [ ] **Step 3: Add a `CLAUDE.md` architecture line** noting owner-tier SkillCorner is stored as the canonical Parquet/zstd set (nested tracking, freeze dropped, `format_version`).

- [ ] **Step 4: Verify docs consistency** — Run: `python -m pytest src/tests/ -k "adr or docs" -v` if such tests exist; otherwise manual read. Expected: no drift.

---

## Task 10: Post-run ops verification script

**Files:**
- Create: `scripts/verify_skillcorner_canonical_load.py`
- Test: `src/tests/test_verify_skillcorner_canonical.py` (unit-test the pure assertions with a fake HTTP responder; reuse `scripts/_verify_http.py`)

**Interfaces:**
- Produces: `check_match_canonical(get, base, token, match_id) -> list[str]` (returns a list of problems; empty == OK): tracking artifact is `.parquet` and parses, `events`/`physical` parse, `freeze` returns 404, `format_version == 2`.

- [ ] **Step 1: Write the failing test** with a fake `get` callable returning canned artifact/index responses (no network); assert a healthy match yields `[]` and a match still serving `tracking.json.gz` yields a problem.

- [ ] **Step 2: Run it to verify it fails.** Run: `python -m pytest src/tests/test_verify_skillcorner_canonical.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement** `check_match_canonical` (pure, `get`-injected) + a `main()` that reads the owner token from env and iterates the provider's private matches. Reuse `_verify_http.py` helpers; sample-from-live-response (no committed licensed ids).

- [ ] **Step 4: Run test to verify it passes.** Expected: PASS.

---

## Final: green gate + single approval-gated commit

- [ ] **Step 1: Full local quality gate.** Run: `ruff check . && ruff format --check . && pyright && python -m pytest src/tests/ -q`. Expected: all green. Fix anything red before proceeding (Shift Left).
- [ ] **Step 2: Show Karsten the complete diff + file list.** Do **not** commit. Present `git status` + `git diff --stat` and wait for explicit approval of *this* commit.
- [ ] **Step 3: On explicit approval only — one commit** of doc + ADR + code + tests together (a single coherent, fully-tested change), on the feature branch. Message describes the feature; ends with the required Co-Authored-By/Session trailers. No push/PR until separately approved.

## Ops execution (separate, gated — NOT part of the code tasks)

These run against **live dev S3** and only after the commit is approved and the code is merged/available. Each is dry-run-first, and each apply step is its own explicit human-approval gate (spec §11):

1. Seed + commit `schemas/skillcorner_events_reference.json` (Task 1 Step 3) if not already committed.
2. `python scripts/migrate_skillcorner_tracking_parquet.py --bucket $PINING_BUCKET` (dry-run) → inspect → `--apply` on approval.
3. `python scripts/upload_skillcorner_raw.py ...` for PL 25/26 (dry-run) → inspect → apply on approval.
4. `python scripts/verify_skillcorner_canonical_load.py` → confirm RM tracking now `.parquet`, freeze 404s, `format_version==2`, counts unchanged; PL 25/26 matches + players present.
5. Rotate nothing (no auth-surface change).

---

## Self-Review (against the spec)

- **Spec coverage:** §3 set → Tasks 4/7/8; §4 nested tracking + equivalence → Task 2; §5 events/physical → Task 3 + Task 1 (pinned schema); §6 readers/boundary → Tasks 5/6 + injected-client adapters; §7 migration/ingest flows → Tasks 7/8; §8 correctness/tests → each task's tests + Final gate; §9 security/faithfulness → preserved (no reshaping; `_private/` guard in Task 7); §10 ADR → Task 9; §11 rollout → Ops section; §12 decisions → pinned schema (Task 1), `format_version` int (Task 4). No uncovered section.
- **Placeholder scan:** the only prose-described (not fully-coded) piece is Task 8's `main()` and Task 10's `main()`, both delegated to an existing, cited pattern (`upload_skillcorner_realmadrid.py`, `_verify_http.py`) with exact reuse points — the *tested* logic (`ingest_match`, `check_match_canonical`) is fully coded. Acceptable; no TBDs.
- **Type consistency:** `frames_equivalent`, `tracking_to_parquet`, `parquet_to_tracking`, `recompress_parquet_zstd`, `events_csv_to_parquet`, `physical_json_to_per_match_parquet`, `raw_role_files`, `discover_manifest_matches`, `migrate_match`, `ingest_match` names/signatures are consistent across the tasks that consume them.
- **Open item for the plan reviewer:** the pinned events-reference confidentiality call (Task 1 note). *(Commit strategy is settled: one single commit of doc + ADR + code + tests — not up for debate.)*
