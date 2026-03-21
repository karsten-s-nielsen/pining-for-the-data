"""Tests for mock_api.upload (mocked S3)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestUploadGame:
    @patch("mock_api.upload.boto3")
    def test_uploads_files_and_updates_indexes(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3

        # No existing indexes
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey()

        # Create test files
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
        # Should have called put_object for matches.json and providers.json
        assert mock_s3.put_object.call_count == 2

    @patch("mock_api.upload.boto3")
    def test_updates_existing_matches_json(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3

        existing_matches = {
            "provider": "skillcorner",
            "matches": [{"id": "game_01", "artifacts": ["tracking"]}],
        }
        existing_providers = {"providers": ["skillcorner"]}

        def get_object_side_effect(Bucket, Key):  # noqa: N803
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
        # matches.json updated (put_object called), but providers.json not updated (already exists)
        assert mock_s3.put_object.call_count == 1

    @patch("mock_api.upload.boto3")
    def test_empty_directory_uploads_nothing(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3

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
            source_license="MIT",
        )

        matches_put = next(
            call for call in mock_s3.put_object.call_args_list if call.kwargs["Key"] == "skillcorner/matches.json"
        )
        body = json.loads(matches_put.kwargs["Body"].decode())
        game = next(m for m in body["matches"] if m["id"] == "game_01")
        assert game["provenance"] == "redistributed"
        assert game["source"] == {
            "name": "SkillCorner Open Data",
            "url": "https://github.com/SkillCorner/opendata",
            "license": "MIT",
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


class TestUpdateMatchesJson:
    @patch("mock_api.upload.boto3")
    def test_replaces_existing_game_entry(self, mock_boto3: MagicMock, tmp_path: Path) -> None:
        from mock_api.upload import upload_game

        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3

        existing = {
            "provider": "skillcorner",
            "matches": [{"id": "game_03", "artifacts": ["tracking"]}],
        }
        existing_providers = {"providers": ["skillcorner"]}

        def get_object_side_effect(Bucket, Key):  # noqa: N803
            if Key == "skillcorner/matches.json":
                return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(existing).encode()))}
            if Key == "providers.json":
                return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(existing_providers).encode()))}
            raise mock_s3.exceptions.NoSuchKey()

        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = get_object_side_effect

        # Upload with new artifacts
        (tmp_path / "tracking.txt").write_text("data")
        (tmp_path / "roster.json").write_text("{}")
        upload_game(tmp_path, "skillcorner", "game_03", "test-bucket")

        # Verify matches.json was written with updated artifacts
        put_call = mock_s3.put_object.call_args
        body = json.loads(put_call.kwargs["Body"].decode())
        game = next(m for m in body["matches"] if m["id"] == "game_03")
        assert sorted(game["artifacts"]) == ["roster", "tracking"]
