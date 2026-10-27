"""The request's contract check: what ``GET /v1/{provider}/matches`` lists equals the stored metadata.

End to end for each provider and tier (spec §5.10, §6): ``upload_game`` writes an invented match into
a dict-backed S3 double, and the real ``list_matches`` handler serves the index with the owner token.
Going through the handler also proves that no handler change is needed. Invented values only.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from formats.provider_metadata import read_metadata_identity
from mock_api.upload import upload_game

_OWN_TOK = "owner-token-invented-0123456789abcdefXYZ"  # pragma: allowlist secret

LAMBDA_SRC = Path(__file__).parent.parent.parent / "terraform" / "modules" / "functions" / "src"
sys.path.insert(0, str(LAMBDA_SRC))

FIXTURES = Path(__file__).parent / "fixtures"
_FIELDS = ("competition_id", "competition_name", "season_id", "season_name")


class _NoSuchKeyError(Exception):
    pass


class DictS3:
    """Dict-backed S3 double shared by the upload tool and the Lambda handler."""

    class exceptions:  # noqa: N801 - mirrors boto3's client.exceptions namespace
        NoSuchKey = _NoSuchKeyError

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        if Key not in self.store:
            raise _NoSuchKeyError(Key)
        return {"Body": io.BytesIO(self.store[Key])}

    def put_object(self, Bucket: str, Key: str, Body: bytes, **_kw: Any) -> None:
        self.store[Key] = Body

    def upload_file(self, Filename: str, Bucket: str, Key: str) -> None:
        self.store[Key] = Path(Filename).read_bytes()


def _statsbomb_meta() -> bytes:
    return json.dumps(
        {
            "match_id": 8888001,
            "match_date": "2099-07-15",
            "competition": {"competition_id": 555001, "competition_name": "Synthetic Continental Cup"},
            "season": {"season_id": 777, "season_name": "2099"},
            "home_team": {"home_team_name": "Northmoor Synthetic"},
            "away_team": {"away_team_name": "Southford Invented"},
        }
    ).encode()


# (provider, visibility, game id, metadata filename, metadata bytes, hand-derived four fields)
CASES = [
    pytest.param(
        "gradientsports",
        "private",
        "990001",
        "metadata.json",
        (FIXTURES / "gradientsports_metadata_synthetic.json").read_bytes(),
        {"competition_id": "77", "competition_name": "Synthetic Cup", "season_id": None, "season_name": "2099"},
        id="gradientsports-private",
    ),
    pytest.param(
        "idsse",
        "public",
        "DFL-MAT-SYN001",
        "metadata.xml",
        (FIXTURES / "idsse_matchinformation_synthetic.xml").read_bytes(),
        {
            "competition_id": "DFL-COM-000099",
            "competition_name": "Synthetic League",
            "season_id": "DFL-SEA-SYN001",
            "season_name": "2099/2100",
        },
        id="idsse-public",
    ),
    pytest.param(
        "skillcorner",
        "public",
        "9000002",
        "9000002_match.json",
        (FIXTURES / "skillcorner_v3_meta_competition_synthetic.json").read_bytes(),
        {
            "competition_id": "301",
            "competition_name": "Synthetic Premier",
            "season_id": "701",
            "season_name": "2099/2100",
        },
        id="skillcorner-public",
    ),
    pytest.param(
        "skillcorner",
        "private",
        "9000002",
        "metadata.json",
        (FIXTURES / "skillcorner_v3_meta_competition_synthetic.json").read_bytes(),
        {
            "competition_id": "301",
            "competition_name": "Synthetic Premier",
            "season_id": "701",
            "season_name": "2099/2100",
        },
        id="skillcorner-private",
    ),
    pytest.param(
        "statsbomb",
        "private",
        "8888001",
        "metadata.json",
        _statsbomb_meta(),
        {
            "competition_id": "555001",
            "competition_name": "Synthetic Continental Cup",
            "season_id": "777",
            "season_name": "2099",
        },
        id="statsbomb-private",
    ),
]


@pytest.fixture
def s3(monkeypatch) -> DictS3:
    import shared

    double = DictS3()
    monkeypatch.setenv("API_TOKEN", "pub-tok")
    monkeypatch.setattr(shared, "_s3_client", double)
    monkeypatch.setattr(shared, "_get_owner_token", lambda: _OWN_TOK)
    monkeypatch.setattr(shared, "_get_list_token", lambda: None)
    return double


def _upload(s3: DictS3, tmp_path: Path, provider: str, visibility: str, game_id: str, files: dict[str, bytes], **kw):
    staging = tmp_path / "stage"
    staging.mkdir()
    for name, body in files.items():
        (staging / name).write_bytes(body)
    with patch("mock_api.upload.boto3.client", return_value=s3):
        upload_game(staging, provider, game_id, "test-bucket", visibility=visibility, **kw)


def _list(provider: str) -> tuple[dict, str]:
    import list_matches

    event = {"headers": {"authorization": f"Bearer {_OWN_TOK}"}, "pathParameters": {"provider": provider}}
    result = list_matches.handler(event, None)
    assert result["statusCode"] == 200
    return json.loads(result["body"]), result["body"]


@pytest.mark.parametrize(("provider", "visibility", "game_id", "metadata_name", "metadata", "want"), CASES)
def test_list_equals_metadata(s3, tmp_path, provider, visibility, game_id, metadata_name, metadata, want):
    files = {metadata_name: metadata, "tracking.bin": b"synthetic tracking bytes"}
    identity = read_metadata_identity(provider, visibility, metadata)
    _upload(s3, tmp_path, provider, visibility, game_id, files, **identity.competition.as_kwargs())

    body, _raw = _list(provider)
    listed = next(m for m in body["matches"] if m["id"] == game_id)

    stored_key = f"{provider}/_private/{game_id}" if visibility == "private" else f"{provider}/{game_id}"
    stored_metadata = s3.store[f"{stored_key}/{metadata_name}"]
    from_stored = read_metadata_identity(provider, visibility, stored_metadata).competition.as_kwargs()
    assert {k: listed.get(k) for k in _FIELDS} == from_stored
    assert {k: listed.get(k) for k in _FIELDS} == want
    assert identity.match_id == game_id
    for name, filename in listed["artifacts"].items():
        stored = s3.store[f"{stored_key}/{filename}"]
        assert listed["artifact_meta"][name] == {"sha256": hashlib.sha256(stored).hexdigest(), "size": len(stored)}


def test_unicode_names_round_trip_verbatim(s3, tmp_path):
    competition_name = "1. FC Ünïon"
    season_name = "Women's Cup 2099"
    _upload(
        s3,
        tmp_path,
        "statsbomb",
        "private",
        "8888001",
        {"metadata.json": _statsbomb_meta()},
        competition_name=competition_name,
        season_name=season_name,
    )
    body, _raw = _list("statsbomb")
    listed = body["matches"][0]
    assert listed["competition_name"].encode("utf-8") == competition_name.encode("utf-8")
    assert listed["season_name"].encode("utf-8") == season_name.encode("utf-8")
    stored_index = json.loads(s3.store["statsbomb/matches.json"].decode("utf-8"))
    assert stored_index["matches"][0]["competition_name"] == competition_name
