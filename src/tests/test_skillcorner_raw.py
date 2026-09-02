"""Unit tests for formats.skillcorner_raw (pure reader; synthetic data only)."""

from __future__ import annotations

from formats.skillcorner_raw import discover_manifest_matches, raw_role_files


def test_discover_reads_manifest_ids_skips_header():
    csv = b"match_id\n1001\n1002\n1003\n"
    assert discover_manifest_matches(csv) == ["1001", "1002", "1003"]


def test_discover_ignores_blank_lines():
    csv = b"match_id\n1001\n\n1002\n"
    assert discover_manifest_matches(csv) == ["1001", "1002"]


def test_raw_role_files_maps_layout():
    m = raw_role_files("1001")
    assert m["metadata"].endswith("matches/1001.json")
    assert m["tracking"].endswith("tracking/1001.json")
    assert m["events"].endswith("dynamic_events/1001.json")  # CSV-mislabelled-.json
    assert "freeze_frames" not in m
    assert "physical" not in m  # combined file, not per-match
