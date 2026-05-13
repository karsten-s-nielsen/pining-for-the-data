"""Tests for the pining-upload-players CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure Lambda shared module is importable (tests use the same mocked S3 path).
LAMBDA_SRC = Path(__file__).parent.parent.parent / "terraform" / "modules" / "functions" / "src"
sys.path.insert(0, str(LAMBDA_SRC))


def _canonical_json_path(tmp_path: Path) -> Path:
    """Write a canonical JSON file with two synthetic player records.

    Synthetic IDs and names — never use real provider data in committed test
    fixtures (memory: feedback_no_licensed_mapping_in_test_fixtures.md).
    """
    p = tmp_path / "players.json"
    p.write_text(
        json.dumps(
            [
                {
                    "id": "test-001",
                    "firstName": "Test",
                    "lastName": "Alpha",
                    "nickname": "Test Alpha",
                    "dob": "2000-01-01",
                    "height": 180.0,
                    "positionGroupType": "D",
                },
                {
                    "id": "test-002",
                    "firstName": "Test",
                    "lastName": "Beta",
                    "nickname": "Test Beta",
                    "dob": "2000-02-02",
                    "height": 175.0,
                    "positionGroupType": "M",
                },
            ]
        ),
        encoding="utf-8",
    )
    return p


def _empty_s3():
    s3 = MagicMock()
    s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
    s3.get_object.side_effect = s3.exceptions.NoSuchKey()
    return s3


class TestUploadPlayersCSVRejection:
    def test_csv_input_is_rejected_with_helpful_message(self, tmp_path):
        from mock_api.upload_players import upload_players

        csv_file = tmp_path / "players.csv"
        csv_file.write_text("id,nickname\ntest-001,Test\n", encoding="utf-8")

        with patch("mock_api.upload_players.boto3.client", return_value=_empty_s3()):
            with pytest.raises(ValueError, match="canonical JSON"):
                upload_players(csv_file, provider="gradient-sports", bucket="b", visibility="private")

    def test_csv_rejection_message_mentions_reference_adapter(self, tmp_path):
        from mock_api.upload_players import upload_players

        csv_file = tmp_path / "players.csv"
        csv_file.write_text("id,nickname\n", encoding="utf-8")

        with patch("mock_api.upload_players.boto3.client", return_value=_empty_s3()):
            with pytest.raises(ValueError, match="upload_gradient_wc2022"):
                upload_players(csv_file, provider="gradient-sports", bucket="b", visibility="private")


class TestUploadPlayersCanonicalJSON:
    def test_private_visibility_writes_under_private_prefix(self, tmp_path):
        from mock_api.upload_players import upload_players

        json_file = _canonical_json_path(tmp_path)

        s3 = _empty_s3()
        with patch("mock_api.upload_players.boto3.client", return_value=s3):
            upload_players(
                json_file,
                provider="gradient-sports",
                bucket="b",
                visibility="private",
                source_name="Gradient Sports",
                source_url="https://www.gradientsports.com/",
                source_licence="Restricted",
            )

        keys = [c.kwargs.get("Key") for c in s3.put_object.call_args_list]
        assert "gradient-sports/_private/players.json" in keys

    def test_public_visibility_writes_to_provider_root(self, tmp_path):
        from mock_api.upload_players import upload_players

        json_file = _canonical_json_path(tmp_path)

        s3 = _empty_s3()
        with patch("mock_api.upload_players.boto3.client", return_value=s3):
            upload_players(json_file, provider="sc", bucket="b", visibility="public")

        keys = [c.kwargs.get("Key") for c in s3.put_object.call_args_list]
        assert "sc/players.json" in keys
        assert "sc/_private/players.json" not in keys

    def test_index_payload_shape_with_updated_at(self, tmp_path):
        from mock_api.upload_players import upload_players

        json_file = _canonical_json_path(tmp_path)

        s3 = _empty_s3()
        with patch("mock_api.upload_players.boto3.client", return_value=s3):
            upload_players(
                json_file,
                provider="gradient-sports",
                bucket="b",
                visibility="private",
                source_name="Gradient Sports",
            )

        private_key = "gradient-sports/_private/players.json"
        put_calls = [c for c in s3.put_object.call_args_list if c.kwargs.get("Key") == private_key]
        assert len(put_calls) == 1
        body = json.loads(put_calls[0].kwargs["Body"].decode("utf-8"))
        assert body["provider"] == "gradient-sports"
        assert len(body["players"]) == 2

        alpha = next(p for p in body["players"] if p["id"] == "test-001")
        assert alpha["firstName"] == "Test"
        assert alpha["lastName"] == "Alpha"
        assert alpha["nickname"] == "Test Alpha"
        assert alpha["dob"] == "2000-01-01"
        assert alpha["height"] == 180.0
        assert alpha["positionGroupType"] == "D"
        assert alpha["visibility"] == "private"
        assert alpha["updated_at"].endswith("Z")
        assert alpha["source"]["name"] == "Gradient Sports"

    def test_pydantic_validation_rejects_record_missing_a_name(self, tmp_path):
        from mock_api.upload_players import upload_players

        bad_file = tmp_path / "bad.json"
        bad_file.write_text(json.dumps([{"id": "x"}]), encoding="utf-8")

        with patch("mock_api.upload_players.boto3.client", return_value=_empty_s3()):
            with pytest.raises(Exception):  # noqa: B017 — pydantic.ValidationError
                upload_players(bad_file, provider="gradient-sports", bucket="b", visibility="private")

    def test_idempotent_reupload_replaces_existing(self, tmp_path):
        from mock_api.upload_players import upload_players

        json_file = _canonical_json_path(tmp_path)

        existing_private = {
            "provider": "gradient-sports",
            "players": [
                {
                    "id": "test-001",
                    "nickname": "Old Name",
                    "visibility": "private",
                    "updated_at": "2025-01-01T00:00:00Z",
                },
                {
                    "id": "test-999",
                    "nickname": "Untouched",
                    "visibility": "private",
                    "updated_at": "2025-01-01T00:00:00Z",
                },
            ],
        }
        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})

        def get_obj(Bucket, Key):
            if Key == "gradient-sports/_private/players.json":
                return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(existing_private).encode()))}
            raise s3.exceptions.NoSuchKey()

        s3.get_object.side_effect = get_obj

        with patch("mock_api.upload_players.boto3.client", return_value=s3):
            upload_players(json_file, provider="gradient-sports", bucket="b", visibility="private")

        private_key = "gradient-sports/_private/players.json"
        put_calls = [c for c in s3.put_object.call_args_list if c.kwargs.get("Key") == private_key]
        body = json.loads(put_calls[0].kwargs["Body"].decode("utf-8"))
        ids = sorted(p["id"] for p in body["players"])
        assert ids == ["test-001", "test-002", "test-999"]
        alpha = next(p for p in body["players"] if p["id"] == "test-001")
        assert alpha["nickname"] == "Test Alpha"  # updated, not "Old Name"
        untouched = next(p for p in body["players"] if p["id"] == "test-999")
        assert untouched["nickname"] == "Untouched"

    def test_tier_mixing_within_same_file_rejected(self, tmp_path):
        from mock_api.upload_players import upload_players

        json_file = _canonical_json_path(tmp_path)

        # An entry already exists in the OTHER tier (public), with the same id
        # the incoming private upload contains. This trips cross-tier dedup.
        existing_public = {
            "provider": "gradient-sports",
            "players": [
                {"id": "test-001", "nickname": "Existing", "visibility": "public", "updated_at": "2025-01-01T00:00:00Z"}
            ],
        }
        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})

        def get_obj(Bucket, Key):
            if Key == "gradient-sports/players.json":
                return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(existing_public).encode()))}
            raise s3.exceptions.NoSuchKey()

        s3.get_object.side_effect = get_obj

        with patch("mock_api.upload_players.boto3.client", return_value=s3):
            with pytest.raises(ValueError, match=r"[Cc]ross-tier"):
                upload_players(json_file, provider="gradient-sports", bucket="b", visibility="private")

    def test_cross_tier_dedup_check_scans_both_files(self, tmp_path):
        """Spec §6.5: cross-tier dedup check reads BOTH players.json files before write."""
        from mock_api.upload_players import upload_players

        json_file = _canonical_json_path(tmp_path)

        existing_public = {
            "provider": "gradient-sports",
            "players": [
                {
                    "id": "test-001",
                    "nickname": "Other Tier",
                    "visibility": "public",
                    "updated_at": "2025-01-01T00:00:00Z",
                }
            ],
        }
        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})

        def get_obj(Bucket, Key):
            if Key == "gradient-sports/players.json":
                return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(existing_public).encode()))}
            raise s3.exceptions.NoSuchKey()

        s3.get_object.side_effect = get_obj

        with patch("mock_api.upload_players.boto3.client", return_value=s3):
            with pytest.raises(ValueError, match=r"[Cc]ross-tier|other tier"):
                upload_players(json_file, provider="gradient-sports", bucket="b", visibility="private")


class TestSourceLicenseAlias:
    def test_argparse_accepts_british_spelling(self, tmp_path, monkeypatch):
        from mock_api import upload_players as upload_players_mod

        captured: dict = {}

        def fake_upload(**kwargs):
            captured.update(kwargs)
            return 0

        monkeypatch.setattr(upload_players_mod, "upload_players", fake_upload)
        json_file = tmp_path / "players.json"
        json_file.write_text("[]", encoding="utf-8")
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "pining-upload-players",
                str(json_file),
                "--provider",
                "p",
                "--bucket",
                "b",
                "--source-licence",
                "British License",
            ],
        )
        upload_players_mod.main()
        assert captured["source_licence"] == "British License"

    def test_argparse_accepts_american_alias(self, tmp_path, monkeypatch):
        from mock_api import upload_players as upload_players_mod

        captured: dict = {}

        def fake_upload(**kwargs):
            captured.update(kwargs)
            return 0

        monkeypatch.setattr(upload_players_mod, "upload_players", fake_upload)
        json_file = tmp_path / "players.json"
        json_file.write_text("[]", encoding="utf-8")
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "pining-upload-players",
                str(json_file),
                "--provider",
                "p",
                "--bucket",
                "b",
                "--source-license",
                "American License",
            ],
        )
        upload_players_mod.main()
        assert captured["source_licence"] == "American License"
