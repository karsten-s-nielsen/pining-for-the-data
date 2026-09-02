"""Test for the tracking-format footprint benchmark's pure function (synthetic data)."""

from __future__ import annotations

import json

import pytest

_FRAMES = [
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
def bench(load_script):
    return load_script("benchmark_skillcorner_tracking_format")


def test_tracking_format_sizes_reports_all_encodings(bench):
    body = json.dumps(_FRAMES).encode()
    sizes = bench.tracking_format_sizes(body)
    assert set(sizes) == {"raw_json", "gzip", "parquet_snappy", "parquet_zstd"}
    assert all(isinstance(v, int) and v > 0 for v in sizes.values())
    assert sizes["raw_json"] == len(body)  # raw is the uncompressed body as delivered


def test_gzipped_input_is_transparently_decompressed(bench):
    import gzip

    body = json.dumps(_FRAMES).encode()
    assert bench.tracking_format_sizes(gzip.compress(body)) == bench.tracking_format_sizes(body)
