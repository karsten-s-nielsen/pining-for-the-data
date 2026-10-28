"""Tests for mock_api.upload (mocked S3)."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from formats.competition_season import CompetitionKwargs

# Ensure Lambda shared module is importable (tests use the same mocked S3 path).
LAMBDA_SRC = Path(__file__).parent.parent.parent / "terraform" / "modules" / "functions" / "src"
sys.path.insert(0, str(LAMBDA_SRC))


class TestUploadGame:
    @patch("mock_api.upload.boto3")
    def test_uploads_files_and_updates_indexes(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey()

        (tmp_path / "tracking.txt").write_text("frame,x,y")
        (tmp_path / "metadata.xml").write_text("<xml/>")

        artifacts = upload_game(
            game_dir=tmp_path,
            provider="skillcorner",
            game_id="game_03",
            bucket="test-bucket",
            date="2026-03-15",
            home="Wakanda FC",
            away="Shire Town",
        )

        assert sorted(artifacts) == ["metadata", "tracking"]
        assert mock_s3.upload_file.call_count == 2
        # put_object for matches.json + providers.json
        assert mock_s3.put_object.call_count == 2

    @patch("mock_api.upload.boto3")
    def test_updates_existing_matches_json(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3

        existing_matches = {
            "provider": "skillcorner",
            "matches": [
                {
                    "id": "game_01",
                    "artifacts": {"tracking": "tracking.txt"},
                    "visibility": "public",
                    "updated_at": "2025-01-01T00:00:00Z",
                }
            ],
        }
        existing_providers = {"providers": ["skillcorner"]}

        def get_object_side_effect(Bucket, Key):
            if Key == "skillcorner/matches.json":
                return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(existing_matches).encode()))}
            if Key == "providers.json":
                return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(existing_providers).encode()))}
            raise mock_s3.exceptions.NoSuchKey()

        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = get_object_side_effect

        (tmp_path / "tracking.txt").write_text("data")
        artifacts = upload_game(tmp_path, "skillcorner", "game_03", "test-bucket")

        assert artifacts == ["tracking"]
        # matches.json updated; providers.json not (already exists)
        assert mock_s3.put_object.call_count == 1

    @patch("mock_api.upload.boto3")
    def test_empty_directory_uploads_nothing(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey()

        artifacts = upload_game(tmp_path, "skillcorner", "game_99", "test-bucket")
        assert artifacts == []
        mock_s3.upload_file.assert_not_called()
        mock_s3.put_object.assert_not_called()

    @patch("mock_api.upload.boto3")
    def test_upload_with_provenance_and_source(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey()

        (tmp_path / "tracking.txt").write_text("frame,x,y")

        upload_game(
            game_dir=tmp_path,
            provider="skillcorner",
            game_id="game_01",
            bucket="test-bucket",
            date="2026-03-20",
            home="Wakanda FC",
            away="Asgard Athletic",
            provenance="redistributed",
            source_name="SkillCorner Open Data",
            source_url="https://github.com/SkillCorner/opendata",
            source_licence="MIT",
        )

        matches_put = next(
            call for call in mock_s3.put_object.call_args_list if call.kwargs["Key"] == "skillcorner/matches.json"
        )
        body = json.loads(matches_put.kwargs["Body"].decode())
        game = next(m for m in body["matches"] if m["id"] == "game_01")
        assert game["provenance"] == "redistributed"
        # British spelling for the internal field name (spec §8.2.1)
        assert game["source"] == {
            "name": "SkillCorner Open Data",
            "url": "https://github.com/SkillCorner/opendata",
            "licence": "MIT",
        }

    @patch("mock_api.upload.boto3")
    def test_upload_without_provenance_omits_fields(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey()

        (tmp_path / "tracking.txt").write_text("frame,x,y")

        upload_game(
            game_dir=tmp_path,
            provider="skillcorner",
            game_id="game_05",
            bucket="test-bucket",
        )

        matches_put = next(
            call for call in mock_s3.put_object.call_args_list if call.kwargs["Key"] == "skillcorner/matches.json"
        )
        body = json.loads(matches_put.kwargs["Body"].decode())
        game = next(m for m in body["matches"] if m["id"] == "game_05")
        assert "provenance" not in game
        assert "source" not in game


class TestUploadValidation:
    def test_rejects_underscore_prefixed_provider(self):
        from mock_api._cli_common import validate_param

        with pytest.raises(ValueError, match="Invalid provider"):
            validate_param("_private", "provider")

    def test_rejects_underscore_prefixed_game_id(self):
        from mock_api._cli_common import validate_param

        with pytest.raises(ValueError, match="Invalid game_id"):
            validate_param("_files", "game_id")

    def test_accepts_underscore_midstring(self):
        from mock_api._cli_common import validate_param

        validate_param("game_03", "game_id")  # no raise


class TestUploadVisibility:
    @patch("mock_api.upload.boto3")
    def test_public_visibility_writes_to_provider_root(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey()

        (tmp_path / "match.json").write_text("{}")

        upload_game(tmp_path, "skillcorner", "g1", "test-bucket", visibility="public")

        upload_keys = [c.args[2] for c in mock_s3.upload_file.call_args_list]
        assert any(k == "skillcorner/g1/match.json" for k in upload_keys)
        assert not any("_private" in k for k in upload_keys)

    @patch("mock_api.upload.boto3")
    def test_private_visibility_writes_under_private_prefix(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey()

        (tmp_path / "match.json").write_text("{}")
        (tmp_path / "tracking.jsonl").write_text("")

        upload_game(tmp_path, "gradientsports", "m-001", "test-bucket", visibility="private")

        upload_keys = [c.args[2] for c in mock_s3.upload_file.call_args_list]
        assert any(k == "gradientsports/_private/m-001/match.json" for k in upload_keys)

        matches_put = next(
            c for c in mock_s3.put_object.call_args_list if c.kwargs["Key"] == "gradientsports/matches.json"
        )
        body = json.loads(matches_put.kwargs["Body"].decode())
        entry = body["matches"][0]
        assert entry["visibility"] == "private"
        # artifacts is an object {name: filename}, not an array
        assert entry["artifacts"] == {"match": "match.json", "tracking": "tracking.jsonl"}
        # updated_at is set, ISO 8601 with trailing Z
        assert entry["updated_at"].endswith("Z")
        assert "T" in entry["updated_at"]

    @patch("mock_api.upload.boto3")
    def test_default_visibility_is_public(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey()

        (tmp_path / "match.json").write_text("{}")
        upload_game(tmp_path, "sc", "g1", "test-bucket")

        matches_put = next(c for c in mock_s3.put_object.call_args_list if c.kwargs["Key"] == "sc/matches.json")
        body = json.loads(matches_put.kwargs["Body"].decode())
        assert body["matches"][0]["visibility"] == "public"

    @patch("mock_api.upload.boto3")
    def test_reupload_adding_artifact_meta_refreshes_updated_at(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        """The stored entry predates artifact_meta, so the re-upload adds artifact_meta: that is a change."""
        from mock_api.upload import upload_game

        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3

        existing = json.dumps(
            {
                "provider": "sc",
                "matches": [
                    {
                        "id": "g1",
                        "artifacts": {"match": "match.json"},
                        "visibility": "public",
                        "updated_at": "2025-01-01T00:00:00Z",
                    }
                ],
            }
        ).encode()
        existing_providers = json.dumps({"providers": ["sc"]}).encode()

        def get_obj(Bucket, Key):
            if Key == "sc/matches.json":
                return {"Body": MagicMock(read=MagicMock(return_value=existing))}
            if Key == "providers.json":
                return {"Body": MagicMock(read=MagicMock(return_value=existing_providers))}
            raise mock_s3.exceptions.NoSuchKey()

        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = get_obj

        (tmp_path / "match.json").write_text("{}")
        upload_game(tmp_path, "sc", "g1", "test-bucket", visibility="public")

        matches_put = next(c for c in mock_s3.put_object.call_args_list if c.kwargs["Key"] == "sc/matches.json")
        body = json.loads(matches_put.kwargs["Body"].decode())
        assert body["matches"][0]["updated_at"] != "2025-01-01T00:00:00Z"
        assert "artifact_meta" in body["matches"][0]

    @patch("mock_api.upload.boto3")
    def test_tier_mixing_rejected(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3

        existing = json.dumps(
            {
                "provider": "sc",
                "matches": [
                    {
                        "id": "g1",
                        "artifacts": {"match": "match.json"},
                        "visibility": "public",
                        "updated_at": "2025-01-01T00:00:00Z",
                    }
                ],
            }
        ).encode()

        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=existing))}

        (tmp_path / "match.json").write_text("{}")
        with pytest.raises(ValueError, match="tier"):
            upload_game(tmp_path, "sc", "g1", "test-bucket", visibility="private")

    @patch("mock_api.upload.boto3")
    def test_pydantic_validation_runs_before_s3_index_write(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        """Bad data must abort the index write (artifacts upload may have happened, but index never gets bad data)."""
        from mock_api.upload import upload_game

        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey()

        # Filename starts with underscore — produces an artifact key starting with `_`
        # which the MatchEntry validator rejects.
        (tmp_path / "_secret.json").write_text("{}")
        # And a normal artifact too so we get past the empty check
        (tmp_path / "match.json").write_text("{}")

        with pytest.raises(Exception):  # noqa: B017 — pydantic.ValidationError or similar
            upload_game(tmp_path, "sc", "g1", "test-bucket", visibility="public")

        # No matches.json put_object on the failure path
        put_keys = [c.kwargs.get("Key") for c in mock_s3.put_object.call_args_list]
        assert "sc/matches.json" not in put_keys


class TestSourceLicenseAlias:
    def test_argparse_accepts_british_spelling(self, tmp_path, monkeypatch):
        from mock_api import upload as upload_mod

        # Stub upload_game to capture kwargs without needing AWS
        captured: dict = {}

        def fake_upload(**kwargs):
            captured.update(kwargs)
            return []

        monkeypatch.setattr(upload_mod, "upload_game", fake_upload)
        game_dir = tmp_path / "g"
        game_dir.mkdir()
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "pining-upload",
                str(game_dir),
                "--provider",
                "sc",
                "--game-id",
                "g1",
                "--bucket",
                "b",
                "--source-licence",
                "British License",
            ],
        )
        upload_mod.main()
        assert captured["source_licence"] == "British License"

    def test_argparse_accepts_american_alias(self, tmp_path, monkeypatch):
        from mock_api import upload as upload_mod

        captured: dict = {}

        def fake_upload(**kwargs):
            captured.update(kwargs)
            return []

        monkeypatch.setattr(upload_mod, "upload_game", fake_upload)
        game_dir = tmp_path / "g"
        game_dir.mkdir()
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "pining-upload",
                str(game_dir),
                "--provider",
                "sc",
                "--game-id",
                "g1",
                "--bucket",
                "b",
                "--source-license",
                "American License",
            ],
        )
        upload_mod.main()
        # Both spellings populate the same destination (British internal name)
        assert captured["source_licence"] == "American License"


class TestUpdateMatchesJson:
    @patch("mock_api.upload.boto3")
    def test_replaces_existing_game_entry(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3

        existing = {
            "provider": "skillcorner",
            "matches": [
                {
                    "id": "game_03",
                    "artifacts": {"tracking": "tracking.txt"},
                    "visibility": "public",
                    "updated_at": "2025-01-01T00:00:00Z",
                }
            ],
        }
        existing_providers = {"providers": ["skillcorner"]}

        def get_obj(Bucket, Key):
            if Key == "skillcorner/matches.json":
                return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(existing).encode()))}
            if Key == "providers.json":
                return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(existing_providers).encode()))}
            raise mock_s3.exceptions.NoSuchKey()

        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = get_obj

        (tmp_path / "tracking.txt").write_text("data")
        (tmp_path / "roster.json").write_text("{}")
        upload_game(tmp_path, "skillcorner", "game_03", "test-bucket")

        put_call = mock_s3.put_object.call_args
        body = json.loads(put_call.kwargs["Body"].decode())
        game = next(m for m in body["matches"] if m["id"] == "game_03")
        assert sorted(game["artifacts"].keys()) == ["roster", "tracking"]


_OLD_TS = "2001-01-01T00:00:00Z"


def _stored_s3(stored_entries: list[dict] | None = None) -> MagicMock:
    """Mock S3 whose `sc/matches.json` holds `stored_entries` (None = no index yet)."""
    mock_s3 = MagicMock()
    mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
    providers = json.dumps({"providers": ["sc"]}).encode()

    def get_obj(Bucket, Key):
        if Key == "sc/matches.json" and stored_entries is not None:
            body = json.dumps({"provider": "sc", "matches": stored_entries}).encode()
            return {"Body": MagicMock(read=MagicMock(return_value=body))}
        if Key == "providers.json":
            return {"Body": MagicMock(read=MagicMock(return_value=providers))}
        raise mock_s3.exceptions.NoSuchKey()

    mock_s3.get_object.side_effect = get_obj
    return mock_s3


def _written_entry(mock_s3: MagicMock, game_id: str = "g1") -> dict:
    put = next(c for c in mock_s3.put_object.call_args_list if c.kwargs["Key"] == "sc/matches.json")
    body = json.loads(put.kwargs["Body"].decode())
    return next(m for m in body["matches"] if m["id"] == game_id)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_FIELDS: CompetitionKwargs = {
    "competition_id": "301",
    "competition_name": "Synthetic Premier",
    "season_id": "701",
    "season_name": "2099/2100",
}


class TestCompetitionAndArtifactMeta:
    """Spec §5.1, §5.4, §5.5, §5.7. Invented values only."""

    @patch("mock_api.upload.boto3")
    def test_flags_reach_stored_entry(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        mock_s3 = _stored_s3()
        mock_boto3.client.return_value = mock_s3
        (tmp_path / "match.json").write_text("{}")
        upload_game(tmp_path, "sc", "g1", "test-bucket", **_FIELDS)

        entry = _written_entry(mock_s3)
        assert {k: entry[k] for k in _FIELDS} == _FIELDS

    @patch("mock_api.upload.boto3")
    def test_absent_values_stay_absent(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        mock_s3 = _stored_s3()
        mock_boto3.client.return_value = mock_s3
        (tmp_path / "match.json").write_text("{}")
        upload_game(tmp_path, "sc", "g1", "test-bucket", season_name="2099", season_id="")

        entry = _written_entry(mock_s3)
        assert entry["season_name"] == "2099"
        for key in ("competition_id", "competition_name", "season_id"):
            assert key not in entry

    @patch("mock_api.upload.boto3")
    def test_artifact_meta_equals_uploaded_bytes(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        mock_s3 = _stored_s3()
        mock_boto3.client.return_value = mock_s3
        (tmp_path / "tracking.jsonl.bz2").write_bytes(b"\x00\x01synthetic")
        (tmp_path / "match.json").write_bytes(b'{"a": 1}')
        upload_game(tmp_path, "sc", "g1", "test-bucket")

        assert _written_entry(mock_s3)["artifact_meta"] == {
            "tracking": {"sha256": _sha(b"\x00\x01synthetic"), "size": 11},
            "match": {"sha256": _sha(b'{"a": 1}'), "size": 8},
        }

    @staticmethod
    def _stored_identical(tmp_path: Path, fields: CompetitionKwargs) -> dict:
        data = b'{"synthetic": true}'
        (tmp_path / "match.json").write_bytes(data)
        return {
            "id": "g1",
            "artifacts": {"match": "match.json"},
            "visibility": "public",
            "updated_at": _OLD_TS,
            **fields,
            "artifact_meta": {"match": {"sha256": _sha(data), "size": len(data)}},
        }

    @patch("mock_api.upload.boto3")
    def test_noop_reupload_keeps_updated_at_and_hashes(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        stored = self._stored_identical(tmp_path, _FIELDS)
        mock_s3 = _stored_s3([stored])
        mock_boto3.client.return_value = mock_s3
        upload_game(tmp_path, "sc", "g1", "test-bucket", **_FIELDS)

        entry = _written_entry(mock_s3)
        assert entry["updated_at"] == _OLD_TS
        assert entry["artifact_meta"] == stored["artifact_meta"]

    @patch("mock_api.upload.boto3")
    def test_content_change_updates_hash_and_updated_at(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        stored = self._stored_identical(tmp_path, _FIELDS)
        (tmp_path / "match.json").write_bytes(b'{"synthetic": false}')  # one artifact's bytes differ
        mock_s3 = _stored_s3([stored])
        mock_boto3.client.return_value = mock_s3
        upload_game(tmp_path, "sc", "g1", "test-bucket", **_FIELDS)

        entry = _written_entry(mock_s3)
        assert entry["artifact_meta"]["match"]["sha256"] == _sha(b'{"synthetic": false}')
        assert entry["updated_at"] != _OLD_TS

    @patch("mock_api.upload.boto3")
    def test_field_change_updates_updated_at(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        stored = self._stored_identical(tmp_path, _FIELDS)
        mock_s3 = _stored_s3([stored])
        mock_boto3.client.return_value = mock_s3
        changed: CompetitionKwargs = {**_FIELDS, "season_name": "2100/2101"}
        upload_game(tmp_path, "sc", "g1", "test-bucket", **changed)

        entry = _written_entry(mock_s3)
        assert entry["season_name"] == "2100/2101"
        assert entry["updated_at"] != _OLD_TS

    @patch("mock_api.upload.boto3")
    def test_dropped_stored_field_counts_as_change(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        stored = {**self._stored_identical(tmp_path, _FIELDS), "legacy_note": "x"}
        mock_s3 = _stored_s3([stored])
        mock_boto3.client.return_value = mock_s3
        upload_game(tmp_path, "sc", "g1", "test-bucket", **_FIELDS)

        entry = _written_entry(mock_s3)
        assert "legacy_note" not in entry
        assert entry["updated_at"] != _OLD_TS

    @pytest.mark.parametrize("bad", [{"competition_id": "49.0"}, {"competition_id": " A"}])
    @patch("mock_api.upload.boto3")
    def test_refused_value_writes_nothing(self, mock_boto3: MagicMock, bad: dict, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        mock_s3 = _stored_s3()
        mock_boto3.client.return_value = mock_s3
        (tmp_path / "match.json").write_text("{}")
        with pytest.raises(ValidationError):
            upload_game(tmp_path, "sc", "g1", "test-bucket", **bad)
        assert mock_s3.upload_file.call_count == 0
        assert mock_s3.put_object.call_count == 0

    @patch("mock_api.upload.boto3")
    def test_negative_int_id_refused_before_any_write(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from formats.competition_season import native_id
        from mock_api.upload import upload_game

        assert native_id(-5, "season_id") == "-5"
        mock_s3 = _stored_s3()
        mock_boto3.client.return_value = mock_s3
        (tmp_path / "match.json").write_text("{}")
        with pytest.raises(ValidationError, match="season_id"):
            upload_game(tmp_path, "sc", "g1", "test-bucket", season_id="-5")
        assert mock_s3.upload_file.call_count == 0
        assert mock_s3.put_object.call_count == 0

    @patch("mock_api.upload.boto3")
    def test_dotfiles_excluded_from_artifacts_and_meta(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        mock_s3 = _stored_s3()
        mock_boto3.client.return_value = mock_s3
        (tmp_path / "match.json").write_text("{}")
        (tmp_path / ".DS_Store").write_bytes(b"\x00")
        upload_game(tmp_path, "sc", "g1", "test-bucket")

        uploaded = [c.args[0] for c in mock_s3.upload_file.call_args_list]
        assert uploaded == [str(tmp_path / "match.json")]
        entry = _written_entry(mock_s3)
        assert set(entry["artifacts"]) == {"match"}
        assert set(entry["artifact_meta"]) == {"match"}

    def test_cli_flags_parse(self, tmp_path: Path, monkeypatch) -> None:
        from mock_api import upload as upload_mod

        captured: dict = {}

        def fake_upload(**kwargs):
            captured.update(kwargs)
            return []

        monkeypatch.setattr(upload_mod, "upload_game", fake_upload)
        game_dir = tmp_path / "g"
        game_dir.mkdir()
        argv = ["pining-upload", str(game_dir), "--provider", "sc", "--game-id", "g1", "--bucket", "b"]
        argv += ["--competition-id", "301", "--competition-name", "Synthetic Premier"]
        argv += ["--season-id", "701", "--season-name", "2099/2100"]
        monkeypatch.setattr(sys, "argv", argv)
        upload_mod.main()
        assert {k: captured[k] for k in _FIELDS} == _FIELDS


class TestUploadGameSourceRevision:
    """source.revision pass-through in upload_game (opendata re-sync, ADR 0015). Invented data only."""

    @staticmethod
    def _written_entry(mock_s3, provider: str = "skillcorner") -> dict:
        key = f"{provider}/matches.json"
        for call in mock_s3.put_object.call_args_list:
            if call.kwargs.get("Key") == key:
                data = json.loads(call.kwargs["Body"].decode("utf-8"))
                return data["matches"][-1]
        raise AssertionError(f"no put_object for {key}")

    @patch("mock_api.upload.boto3")
    def test_writes_source_revision(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey()
        (tmp_path / "match.json").write_text("{}")

        upload_game(
            game_dir=tmp_path,
            provider="skillcorner",
            game_id="game_09",
            bucket="b",
            source_name="Synthetic Open Data",
            source_revision="a" * 40,
        )
        entry = self._written_entry(mock_s3)
        assert entry["source"]["revision"] == "a" * 40

    @patch("mock_api.upload.boto3")
    def test_no_revision_without_source_name(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey()
        (tmp_path / "match.json").write_text("{}")

        upload_game(
            game_dir=tmp_path,
            provider="skillcorner",
            game_id="game_09",
            bucket="b",
            source_name=None,
            source_revision="a" * 40,
        )
        entry = self._written_entry(mock_s3)
        assert "source" not in entry
