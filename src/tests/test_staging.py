"""Tests for the shared staging helper (src/mock_api/staging.py)."""

from __future__ import annotations

import gzip
import json

from mock_api.staging import stage_artifacts

SPECS = (
    ("events", "events.json", "events.json.gz"),
    ("roster", "lineups.json", "roster.json"),
)


def test_gzips_gz_specs_copies_plain_and_writes_metadata(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "events.json").write_text(json.dumps([{"id": "e1"}]), encoding="utf-8")
    (src / "lineups.json").write_text(json.dumps([{"team_id": 1}]), encoding="utf-8")
    staging = tmp_path / "stage"
    staging.mkdir()

    stage_artifacts(src, staging, SPECS, {"match_id": 42})

    assert sorted(p.name for p in staging.iterdir()) == ["events.json.gz", "metadata.json", "roster.json"]
    with gzip.open(staging / "events.json.gz", "rt", encoding="utf-8") as f:
        assert json.load(f) == [{"id": "e1"}]
    assert json.loads((staging / "roster.json").read_text(encoding="utf-8")) == [{"team_id": 1}]
    assert json.loads((staging / "metadata.json").read_text(encoding="utf-8")) == {"match_id": 42}


# --- reproducible gzip header (spec §5.8) ----------------------------------------------------


def test_gzip_header_fixed(tmp_path) -> None:
    import gzip

    from mock_api.staging import _gzip_file

    src = tmp_path / "events.json"
    src.write_bytes(b'[{"synthetic": 1}]')
    dest = tmp_path / "events.json.gz"
    _gzip_file(src, dest)
    data = dest.read_bytes()
    assert data[4:8] == b"\x00\x00\x00\x00"  # MTIME
    assert data[3] & 0x08 == 0  # FNAME flag clear
    assert gzip.decompress(data) == b'[{"synthetic": 1}]'


def test_gzip_twice_identical(tmp_path, monkeypatch) -> None:
    import time

    from mock_api.staging import _gzip_file

    src = tmp_path / "events.json"
    src.write_bytes(b'[{"synthetic": 1}]')
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _gzip_file(src, tmp_path / "a" / "events.json.gz")
    monkeypatch.setattr(time, "time", lambda: 4102444800.0)  # a later run, another clock value
    _gzip_file(src, tmp_path / "b" / "events.json.gz")
    assert (tmp_path / "a" / "events.json.gz").read_bytes() == (tmp_path / "b" / "events.json.gz").read_bytes()
