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
