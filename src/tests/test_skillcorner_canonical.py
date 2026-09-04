"""Unit tests for the canonical SkillCorner transforms (pure; wholly-synthetic data)."""

from __future__ import annotations

import io

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from formats.skillcorner_canonical import (
    TRACKING_SCHEMA,
    events_csv_to_parquet,
    events_parquet_to_parquet,
    frames_equivalent,
    parquet_to_tracking,
    physical_json_to_per_match_parquet,
    recompress_parquet_zstd,
    tracking_to_parquet,
)
from formats.skillcorner_events_reference import load_events_reference_schema, reference_arrow_schema

# --- reference schema (Task 1) ----------------------------------------------


def test_reference_schema_loads_as_name_to_type_map():
    schema = load_events_reference_schema()
    assert isinstance(schema, dict) and schema
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in schema.items())
    assert "event_id" in schema
    assert schema["index"] == "int64"


def test_reference_arrow_schema_appends_extra_nullable_columns():
    sch = reference_arrow_schema({"new_metric": "double"})
    assert "index" in sch.names and "new_metric" in sch.names
    assert sch.field("new_metric").nullable


# --- tracking transforms + equivalence (Task 2) -----------------------------


def _ball(x: float | None = 1.5, y: float | None = 2.5, z: float | None = 0.0, det: bool | None = True):
    return {"x": x, "y": y, "z": z, "is_detected": det}


def _corners(v: float | None = None):
    keys = [
        "x_top_left",
        "y_top_left",
        "x_bottom_left",
        "y_bottom_left",
        "x_bottom_right",
        "y_bottom_right",
        "x_top_right",
        "y_top_right",
    ]
    return {k: v for k in keys}


def _frame(
    n: int,
    players: list,
    ball: dict | None = None,
    ts: str | None = "00:00:00.00",
    period: int | None = 1,
    poss: dict | None = None,
    corners: dict | None = None,
):
    return {
        "frame": n,
        "timestamp": ts,
        "period": period,
        "ball_data": ball if ball is not None else _ball(),
        "possession": poss if poss is not None else {"player_id": 7, "group": "home"},
        "image_corners_projection": corners if corners is not None else _corners(1.0),
        "player_data": players,
    }


SYNTHETIC = [
    _frame(
        0,
        [],
        ball=_ball(None, None, None, None),
        ts=None,
        period=None,
        poss={"player_id": None, "group": None},
        corners=_corners(None),
    ),  # empty/pre-kickoff
    _frame(
        1,
        [
            {"x": 3.0, "y": 4.0, "player_id": 101, "is_detected": True},
            {"x": 5.5, "y": 6.5, "player_id": None, "is_detected": False},
        ],
    ),  # null player_id
    _frame(2, [{"x": 0, "y": 0, "player_id": 202, "is_detected": True}]),  # integer coordinates
    _frame(3, [{"x": 7.0, "y": 8.0, "player_id": 303, "is_detected": True}], period=2),
]


def test_tracking_parquet_round_trip_is_equivalent():
    assert frames_equivalent(SYNTHETIC, parquet_to_tracking(tracking_to_parquet(SYNTHETIC)))


def test_integer_coordinate_frame_passes_after_float_widening():
    reconstructed = parquet_to_tracking(tracking_to_parquet(SYNTHETIC))
    assert reconstructed[2]["player_data"][0]["x"] == 0.0
    assert frames_equivalent([SYNTHETIC[2]], [reconstructed[2]])


def test_absent_optional_key_normalizes_to_null_and_passes():
    src = [_frame(9, [{"x": 1.0, "y": 2.0, "player_id": 1, "is_detected": True}])]
    del src[0]["possession"]  # absent optional key
    assert frames_equivalent(src, parquet_to_tracking(tracking_to_parquet(src)))


def test_genuine_value_change_aborts():
    a = parquet_to_tracking(tracking_to_parquet(SYNTHETIC))
    a[1]["player_data"][0]["x"] = 999.0
    assert not frames_equivalent(SYNTHETIC, a)


def test_integer_typed_column_type_change_aborts():
    a = parquet_to_tracking(tracking_to_parquet(SYNTHETIC))
    a[0]["frame"] = 0.0  # frame must stay int
    assert not frames_equivalent(SYNTHETIC, a)


def test_written_parquet_uses_the_fixed_schema():
    schema = pq.read_schema(io.BytesIO(tracking_to_parquet(SYNTHETIC)))
    assert schema.field("frame").type == "int32"  # not inferred int64
    assert schema.field("period").type == "int8"
    assert schema.equals(TRACKING_SCHEMA)


def test_unexpected_source_field_raises_clear_error():
    bad = [dict(SYNTHETIC[1], surprise_field=1)]  # a field not in the pinned schema
    with pytest.raises(ValueError, match="unexpected tracking field"):
        tracking_to_parquet(bad)


# --- events / physical transforms (Task 3) ----------------------------------


def _parquet_bytes(df, compression="snappy"):
    buf = io.BytesIO()
    df.to_parquet(buf, compression=compression)
    return buf.getvalue()


def test_recompress_preserves_values_and_uses_zstd():
    import pandas as pd

    df = pd.DataFrame({"a": [1, 2, 3], "b": [1.5, 2.5, 3.5]})
    out = recompress_parquet_zstd(_parquet_bytes(df, "snappy"))
    meta = pq.read_metadata(io.BytesIO(out))
    assert meta.row_group(0).column(0).compression.lower() == "zstd"
    pd.testing.assert_frame_equal(pd.read_parquet(io.BytesIO(out)), df)


def test_events_csv_conforms_shared_columns_and_keeps_extras():
    # 'index' is int64 and 'x_start' is double in the reference; 'new_metric' is not -> nullable extra
    out = events_csv_to_parquet(b"index,x_start,new_metric\n1,0.5,9.9\n2,1.5,\n")
    t = pq.read_table(io.BytesIO(out))
    assert str(t.schema.field("index").type) == "int64"
    assert str(t.schema.field("x_start").type) == "double"
    assert "new_metric" in t.schema.names
    assert t.num_rows == 2


def test_events_lossy_cast_aborts():
    # 'index' is int64 in the reference; a non-integral value cannot cast without loss -> abort
    with pytest.raises(pa.ArrowInvalid):
        events_csv_to_parquet(b"index,x_start\n1.5,0\n")


def _events_parquet(cols: dict) -> bytes:
    # Build events Parquet directly from a pyarrow table (no pandas index column) — mirrors the
    # parquet-family dynamic/*.parquet source shape.
    buf = io.BytesIO()
    pq.write_table(pa.table(cols), buf, compression="snappy")
    return buf.getvalue()


def test_events_parquet_conforms_shared_columns_and_keeps_extras():
    out = events_parquet_to_parquet(
        _events_parquet({"index": [1, 2], "x_start": [0.5, 1.5], "new_metric": [9.9, None]})
    )
    t = pq.read_table(io.BytesIO(out))
    assert str(t.schema.field("index").type) == "int64"
    assert str(t.schema.field("x_start").type) == "double"
    assert "new_metric" in t.schema.names
    assert t.num_rows == 2
    # conformed events are zstd, like the CSV path
    assert pq.read_metadata(io.BytesIO(out)).row_group(0).column(0).compression.lower() == "zstd"


def test_events_parquet_and_csv_produce_identical_schema():
    # Same logical columns via either source family must yield an identical event schema.
    from_parquet = pq.read_table(
        io.BytesIO(events_parquet_to_parquet(_events_parquet({"index": [1, 2], "x_start": [0.5, 1.5]})))
    )
    from_csv = pq.read_table(io.BytesIO(events_csv_to_parquet(b"index,x_start\n1,0.5\n2,1.5\n")))
    assert from_parquet.schema.equals(from_csv.schema)


def test_events_parquet_lossy_cast_aborts():
    # 'index' is int64 in the reference; a fractional value cannot cast without loss -> abort
    with pytest.raises(pa.ArrowInvalid):
        events_parquet_to_parquet(_events_parquet({"index": [1.5], "x_start": [0.0]}))


def test_physical_split_by_match_id():
    results = [
        {"match_id": "m1", "player_id": 1, "psv99": 30.0},
        {"match_id": "m1", "player_id": 2, "psv99": 31.0},
        {"match_id": "m2", "player_id": 3, "psv99": 29.0},
    ]
    out = physical_json_to_per_match_parquet(results)
    assert set(out) == {"m1", "m2"}
    assert pq.read_table(io.BytesIO(out["m1"])).num_rows == 2
