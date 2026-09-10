"""Tests for the public opendata ingest adapter's I/O-injected helpers (synthetic data only).

scripts/ is not a package; the `load_script` fixture (conftest) loads the adapter by path and
`stage_match` takes an injected `fetch`, so no network or S3 is touched here.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def opendata_adapter(load_script):
    return load_script("upload_skillcorner_opendata")


def _raw_for(match_id: str = "3001") -> dict[str, bytes]:
    return {
        f"matches/{match_id}/{match_id}_match.json": json.dumps({"id": int(match_id)}).encode(),
        f"matches/{match_id}/{match_id}_tracking_extrapolated.jsonl": b'{"frame": 0}\n',
        f"matches/{match_id}/{match_id}_dynamic_events.csv": b"event,x\npass,0.5\n",
        f"matches/{match_id}/{match_id}_phases_of_play.csv": b"phase,start\n1,0\n",
    }


def _mock_s3():
    s3 = MagicMock()
    s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
    return s3


class TestStageMatch:
    def test_writes_four_id_prefixed_artifacts(self, opendata_adapter, tmp_path: Path) -> None:
        raw = _raw_for("3001")
        staging = opendata_adapter.stage_match(lambda p: raw[p], tmp_path, "3001")
        names = {p.name for p in Path(staging).iterdir()}
        assert names == {
            "3001_match.json",
            "3001_tracking_extrapolated.jsonl",
            "3001_dynamic_events.csv",
            "3001_phases_of_play.csv",
        }

    def test_raises_on_empty_artifact(self, opendata_adapter, tmp_path: Path) -> None:
        raw = _raw_for("3001")
        raw["matches/3001/3001_tracking_extrapolated.jsonl"] = b""
        with pytest.raises(ValueError, match="empty"):
            opendata_adapter.stage_match(lambda p: raw[p], tmp_path, "3001")


class TestLiveMatchIds:
    def test_reads_all_ids_regardless_of_tier(self, opendata_adapter) -> None:
        s3 = _mock_s3()
        index = {"matches": [{"id": "1886347", "visibility": "public"}, {"id": "5001", "visibility": "private"}]}
        s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=json.dumps(index).encode()))}
        assert opendata_adapter.live_match_ids(s3, "bucket") == {"1886347", "5001"}

    def test_empty_when_index_absent(self, opendata_adapter) -> None:
        s3 = _mock_s3()
        s3.get_object.side_effect = s3.exceptions.NoSuchKey()
        assert opendata_adapter.live_match_ids(s3, "bucket") == set()


class TestPrivatePlayerIds:
    def test_reads_owner_tier_player_ids(self, opendata_adapter) -> None:
        s3 = _mock_s3()
        idx = {"players": [{"id": "38673"}, {"id": "51713"}, {"no_id": True}]}
        s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=json.dumps(idx).encode()))}
        assert opendata_adapter.private_player_ids(s3, "bucket") == {"38673", "51713"}

    def test_empty_when_absent(self, opendata_adapter) -> None:
        s3 = _mock_s3()
        s3.get_object.side_effect = s3.exceptions.NoSuchKey()
        assert opendata_adapter.private_player_ids(s3, "bucket") == set()
