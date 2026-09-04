"""Pure transforms for the canonical owner-tier SkillCorner artifact set.

Functional core (no I/O): tracking JSON <-> nested Parquet under a fixed schema, events
CSV/Parquet -> Parquet (zstd), combined physical JSON -> per-match Parquet. All bytes/objects
in, bytes out. The scripts/ adapters do the S3/HF I/O.

See docs/superpowers/specs/2026-09-01-skillcorner-canonical-parquet-format-design.md.
"""

from __future__ import annotations

import io
from collections import defaultdict

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from formats.skillcorner_events_reference import load_events_reference_schema, reference_arrow_schema

# --- tracking (spec §4) ------------------------------------------------------

# Integer-typed tracking scalars: value AND type must match (spec §4.2).
_INT_SCALARS = {"frame", "period", "player_id"}

_CORNER_KEYS = (
    "x_top_left",
    "y_top_left",
    "x_bottom_left",
    "y_bottom_left",
    "x_bottom_right",
    "y_bottom_right",
    "x_top_right",
    "y_top_right",
)
_BALL = pa.struct([("x", pa.float64()), ("y", pa.float64()), ("z", pa.float64()), ("is_detected", pa.bool_())])
_POSS = pa.struct([("player_id", pa.int64()), ("group", pa.string())])
_CORNERS = pa.struct([(k, pa.float64()) for k in _CORNER_KEYS])
_PLAYER = pa.struct([("x", pa.float64()), ("y", pa.float64()), ("player_id", pa.int64()), ("is_detected", pa.bool_())])

# Fixed nested schema (spec §4.1) — pinned so EVERY match writes an identical schema.
TRACKING_SCHEMA = pa.schema(
    [
        ("frame", pa.int32()),
        ("timestamp", pa.string()),
        ("period", pa.int8()),
        ("ball_data", _BALL),
        ("possession", _POSS),
        ("image_corners_projection", _CORNERS),
        ("player_data", pa.list_(_PLAYER)),
    ]
)


def tracking_to_parquet(frames: list[dict]) -> bytes:
    """Nested one-row-per-frame Parquet (zstd) under the fixed TRACKING_SCHEMA (spec §4.1)."""
    allowed = set(TRACKING_SCHEMA.names)
    for frame in frames:  # clear diagnostic if a NEW source field appears (the schema is pinned)
        extra = set(frame) - allowed
        if extra:
            raise ValueError(
                f"unexpected tracking field(s) {sorted(extra)} — schema is pinned; update TRACKING_SCHEMA (spec §4.1)"
            )
    table = pa.Table.from_pylist(frames, schema=TRACKING_SCHEMA)
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    return buf.getvalue()


def parquet_to_tracking(parquet_bytes: bytes) -> list[dict]:
    """Inverse of tracking_to_parquet — reconstruct the frame list."""
    return pq.read_table(io.BytesIO(parquet_bytes)).to_pylist()


def _scalar_equal(key: str, a: object, b: object) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if key in _INT_SCALARS:
        return type(a) is type(b) and a == b  # type-strict on integer columns
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)  # float coordinates: numeric equality after widening
    return a == b  # string (bool is captured by the numeric branch above)


def _struct_equal(a: dict, b: dict) -> bool:
    for key in set(a) | set(b):
        av, bv = a.get(key), b.get(key)
        if isinstance(av, dict) or isinstance(bv, dict):
            if not _struct_equal(av or {}, bv or {}):
                return False
        elif not _scalar_equal(key, av, bv):
            return False
    return True


def _frame_equal(a: dict, b: dict) -> bool:
    for key in set(a) | set(b):
        av, bv = a.get(key), b.get(key)
        if isinstance(av, dict) or isinstance(bv, dict):
            if not _struct_equal(av or {}, bv or {}):
                return False
        elif isinstance(av, list) or isinstance(bv, list):
            av, bv = av or [], bv or []
            if len(av) != len(bv) or any(not _struct_equal(x, y) for x, y in zip(av, bv, strict=False)):
                return False
        elif not _scalar_equal(key, av, bv):
            return False
    return True


def frames_equivalent(a: list[dict], b: list[dict]) -> bool:
    """Spec §4.2 equivalence: absent-key -> null normalized; per-column-type comparison."""
    return len(a) == len(b) and all(_frame_equal(x, y) for x, y in zip(a, b, strict=False))


# --- events / physical (spec §5) --------------------------------------------


def recompress_parquet_zstd(parquet_bytes: bytes) -> bytes:
    """Re-encode an existing Parquet with zstd; values unchanged."""
    table = pq.read_table(io.BytesIO(parquet_bytes))
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    return buf.getvalue()


def _conform_events_table(table: pa.Table) -> bytes:
    """Conform an events Arrow table to the pinned reference dtypes -> Parquet (zstd).

    Shared columns are SAFE-cast to the reference dtype: a value that cannot be represented
    raises rather than silently truncating/overflowing — preserving spec §5.1 "values unchanged".
    Newer-season columns absent from the reference keep their inferred nullable type. Shared by the
    CSV (raw family) and Parquet (parquet family) events paths so every source produces the SAME
    event schema (the shared 294-col core identically typed; newer-season extras nullable).
    """
    ref = load_events_reference_schema()
    extra_cols = {c: str(table.schema.field(c).type) for c in table.schema.names if c not in ref}
    target = reference_arrow_schema(extra_cols)
    ordered = [field.name for field in target if field.name in table.schema.names]
    target_ordered = pa.schema([target.field(name) for name in ordered])
    table = table.select(ordered).cast(target_ordered)  # safe=True default: raises on lossy cast
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    return buf.getvalue()


def events_csv_to_parquet(csv_bytes: bytes) -> bytes:
    """CSV events (raw family: dynamic_events/*.json, actually CSV) -> Parquet (zstd), conformed.

    low_memory=False: read the whole file so per-column dtype inference is deterministic (the
    default chunked read infers mixed types on the wider real-season CSVs). Shared columns are
    pinned by the reference cast in _conform_events_table; this fixes the ~16 extra columns too.
    """
    df = pd.read_csv(io.BytesIO(csv_bytes), low_memory=False)
    return _conform_events_table(pa.Table.from_pandas(df, preserve_index=False))


def events_parquet_to_parquet(parquet_bytes: bytes) -> bytes:
    """Parquet events (parquet family: RM/PL24-25 dynamic/*.parquet) -> Parquet (zstd), conformed.

    Same reference-conform contract as the CSV path, so parquet-family events carry a schema
    identical to the CSV-family events — no per-source schema drift for the lakehouse consumer.
    """
    return _conform_events_table(pq.read_table(io.BytesIO(parquet_bytes)))


def physical_json_to_per_match_parquet(results: list[dict]) -> dict[str, bytes]:
    """Split a combined physical results list into per-match Parquet (zstd), keyed by match_id."""
    by_match: dict[str, list[dict]] = defaultdict(list)
    for row in results:
        by_match[str(row["match_id"])].append(row)
    out: dict[str, bytes] = {}
    for match_id, rows in by_match.items():
        buf = io.BytesIO()
        pq.write_table(pa.Table.from_pylist(rows), buf, compression="zstd")
        out[match_id] = buf.getvalue()
    return out
