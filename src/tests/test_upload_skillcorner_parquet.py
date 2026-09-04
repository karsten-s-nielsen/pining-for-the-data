"""Tests for the parquet-family ingest adapter's pure per-match transform (synthetic data only)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from formats.skillcorner_canonical import frames_equivalent, parquet_to_tracking


def _frames():
    return [
        {
            "frame": 0,
            "timestamp": "00:00:00.00",
            "period": 1,
            "ball_data": {"x": 1.0, "y": 2.0, "z": 0.0, "is_detected": True},
            "possession": {"player_id": 7, "group": "home"},
            "image_corners_projection": {
                k: 1.0
                for k in [
                    "x_top_left",
                    "y_top_left",
                    "x_bottom_left",
                    "y_bottom_left",
                    "x_bottom_right",
                    "y_bottom_right",
                    "x_top_right",
                    "y_top_right",
                ]
            },
            "player_data": [{"x": 3.0, "y": 4.0, "player_id": 101, "is_detected": True}],
        }
    ]


def _events_parquet():
    # parquet-family events source: already Parquet (dynamic/<id>.parquet)
    buf = io.BytesIO()
    pq.write_table(pa.table({"index": [1, 2], "x_start": [0.5, 1.5]}), buf, compression="snappy")
    return buf.getvalue()


def _physical_parquet():
    buf = io.BytesIO()
    pq.write_table(pa.table({"match_id": ["700"], "player_id": [1], "psv99": [30.0]}), buf, compression="snappy")
    return buf.getvalue()


@pytest.fixture
def pq_ingest(load_script):
    return load_script("upload_skillcorner_parquet")


def test_ingest_builds_canonical_staging_with_physical(pq_ingest, tmp_path):
    raw = {
        "meta/700.json": json.dumps({"id": 700}).encode(),
        "tracking/700.json": json.dumps(_frames()).encode(),
        "dynamic/700.parquet": _events_parquet(),  # events already Parquet, not CSV
    }
    staging = pq_ingest.ingest_match(lambda p: raw[p], tmp_path, "700", physical_bytes=_physical_parquet())
    names = {p.name for p in Path(staging).iterdir()}
    assert names == {"metadata.json", "tracking.parquet", "events.parquet", "physical.parquet"}
    frames = parquet_to_tracking((Path(staging) / "tracking.parquet").read_bytes())
    assert frames_equivalent(_frames(), frames)
    # events conformed to the reference dtypes + zstd
    events = (Path(staging) / "events.parquet").read_bytes()
    assert str(pq.read_table(io.BytesIO(events)).schema.field("index").type) == "int64"
    assert pq.read_metadata(io.BytesIO(events)).row_group(0).column(0).compression.lower() == "zstd"


def test_ingest_without_physical_omits_it_and_never_writes_freeze(pq_ingest, tmp_path):
    raw = {
        "meta/701.json": json.dumps({"id": 701}).encode(),
        "tracking/701.json": json.dumps(_frames()).encode(),
        "dynamic/701.parquet": _events_parquet(),
    }
    staging = pq_ingest.ingest_match(lambda p: raw[p], tmp_path, "701", physical_bytes=None)
    names = {p.name for p in Path(staging).iterdir()}
    assert names == {"metadata.json", "tracking.parquet", "events.parquet"}
    assert "physical.parquet" not in names
    assert "freeze_frames.parquet" not in names and "freeze.parquet" not in names
