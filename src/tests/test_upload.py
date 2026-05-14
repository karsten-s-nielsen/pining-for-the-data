"""Tests for mock_api.upload (mocked S3)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
    def test_reupload_refreshes_updated_at_even_if_unchanged(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
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
