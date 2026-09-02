"""Tests for the raw-JSON ingest adapter's pure per-match transform (synthetic data only)."""

from __future__ import annotations

import json
from pathlib import Path

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


@pytest.fixture
def raw_ingest(load_script):
    return load_script("upload_skillcorner_raw")


def test_ingest_builds_canonical_staging(raw_ingest, tmp_path):
    raw = {
        "matches/900.json": json.dumps({"id": 900}).encode(),
        "tracking/900.json": json.dumps(_frames()).encode(),
        "dynamic_events/900.json": b"index,x_start\n1,0.5\n2,1.5\n",  # CSV mislabelled .json
    }
    staging = raw_ingest.ingest_match(
        lambda p: raw[p],
        tmp_path,
        "900",
        physical_rows=[{"match_id": "900", "player_id": 1, "psv99": 30.0}],
    )
    names = {p.name for p in Path(staging).iterdir()}
    assert names == {"metadata.json", "tracking.parquet", "events.parquet", "physical.parquet"}
    frames = parquet_to_tracking((Path(staging) / "tracking.parquet").read_bytes())
    assert frames_equivalent(_frames(), frames)


def test_ingest_without_physical_rows_omits_physical(raw_ingest, tmp_path):
    raw = {
        "matches/901.json": json.dumps({"id": 901}).encode(),
        "tracking/901.json": json.dumps(_frames()).encode(),
        "dynamic_events/901.json": b"index,x_start\n1,0.5\n",
    }
    staging = raw_ingest.ingest_match(lambda p: raw[p], tmp_path, "901", physical_rows=[])
    names = {p.name for p in Path(staging).iterdir()}
    assert "physical.parquet" not in names
    assert names == {"metadata.json", "tracking.parquet", "events.parquet"}
