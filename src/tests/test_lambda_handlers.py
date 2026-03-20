"""Tests for Lambda handler logic (no AWS needed — mocked S3)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Add Lambda src to path so handlers can import shared
LAMBDA_SRC = Path(__file__).parent.parent.parent / "terraform" / "modules" / "functions" / "src"
sys.path.insert(0, str(LAMBDA_SRC))

os.environ.setdefault("API_TOKEN", "test-token")
os.environ.setdefault("DATA_BUCKET", "test-bucket")

import shared  # noqa: E402


def _mock_s3() -> MagicMock:
    """Create a fresh mock S3 client and inject it into shared module."""
    mock = MagicMock()
    shared._s3_client = mock
    return mock


class _ResetS3:
    """Mixin that resets the shared S3 client after each test."""

    def teardown_method(self) -> None:
        shared._s3_client = None


class TestValidateToken:
    def test_valid_token(self) -> None:
        from shared import validate_token

        event = {"headers": {"authorization": "Bearer test-token"}}
        assert validate_token(event) is None

    def test_valid_token_capitalized_header(self) -> None:
        from shared import validate_token

        event = {"headers": {"Authorization": "Bearer test-token"}}
        assert validate_token(event) is None

    def test_missing_header(self) -> None:
        from shared import validate_token

        event = {"headers": {}}
        result = validate_token(event)
        assert result is not None
        assert result["statusCode"] == 401

    def test_wrong_token(self) -> None:
        from shared import validate_token

        event = {"headers": {"authorization": "Bearer wrong-token"}}
        result = validate_token(event)
        assert result is not None
        assert result["statusCode"] == 401

    def test_no_bearer_prefix(self) -> None:
        from shared import validate_token

        event = {"headers": {"authorization": "Basic abc123"}}
        result = validate_token(event)
        assert result is not None
        assert result["statusCode"] == 401

    def test_null_headers(self) -> None:
        from shared import validate_token

        event = {"headers": None}
        result = validate_token(event)
        assert result is not None
        assert result["statusCode"] == 401


class TestListProviders(_ResetS3):
    def test_returns_providers(self) -> None:
        from list_providers import handler

        mock_s3 = _mock_s3()
        body = json.dumps({"providers": ["metrica", "respovision"]}).encode()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body))}

        event = {"headers": {"authorization": "Bearer test-token"}}
        result = handler(event, None)
        assert result["statusCode"] == 200
        assert "metrica" in json.loads(result["body"])["providers"]

    def test_rejects_no_auth(self) -> None:
        from list_providers import handler

        result = handler({"headers": {}}, None)
        assert result["statusCode"] == 401


class TestListMatches(_ResetS3):
    def test_returns_matches(self) -> None:
        from list_matches import handler

        mock_s3 = _mock_s3()
        body = json.dumps({"provider": "metrica", "matches": [{"id": "game_03"}]}).encode()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body))}

        event = {
            "headers": {"authorization": "Bearer test-token"},
            "pathParameters": {"provider": "metrica"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        assert json.loads(result["body"])["matches"][0]["id"] == "game_03"

    def test_unknown_provider_returns_404(self) -> None:
        from list_matches import handler

        mock_s3 = _mock_s3()
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey()

        event = {
            "headers": {"authorization": "Bearer test-token"},
            "pathParameters": {"provider": "unknown"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 404


class TestGetArtifact(_ResetS3):
    def test_returns_redirect(self) -> None:
        from get_artifact import handler

        mock_s3 = _mock_s3()
        mock_s3.list_objects_v2.return_value = {
            "Contents": [{"Key": "metrica/game_03/tracking.txt"}]
        }
        mock_s3.generate_presigned_url.return_value = "https://s3.example.com/presigned"

        event = {
            "headers": {"authorization": "Bearer test-token"},
            "pathParameters": {"provider": "metrica", "id": "game_03", "artifact": "tracking"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 302
        assert result["headers"]["Location"] == "https://s3.example.com/presigned"

    def test_artifact_not_found(self) -> None:
        from get_artifact import handler

        mock_s3 = _mock_s3()
        mock_s3.list_objects_v2.return_value = {"Contents": []}

        event = {
            "headers": {"authorization": "Bearer test-token"},
            "pathParameters": {"provider": "metrica", "id": "game_99", "artifact": "tracking"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 404

    def test_filters_by_exact_artifact_name(self) -> None:
        from get_artifact import handler

        mock_s3 = _mock_s3()
        mock_s3.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "metrica/game_03/tracking.txt"},
                {"Key": "metrica/game_03/tracking_summary.json"},
            ]
        }
        mock_s3.generate_presigned_url.return_value = "https://s3.example.com/presigned"

        event = {
            "headers": {"authorization": "Bearer test-token"},
            "pathParameters": {"provider": "metrica", "id": "game_03", "artifact": "tracking"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 302
        # Should match tracking.txt, not tracking_summary.json
        mock_s3.generate_presigned_url.assert_called_once()
        call_args = mock_s3.generate_presigned_url.call_args
        assert call_args[1]["Params"]["Key"] == "metrica/game_03/tracking.txt"

    def test_missing_path_parameters(self) -> None:
        from get_artifact import handler

        event = {
            "headers": {"authorization": "Bearer test-token"},
            "pathParameters": {"provider": "metrica"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 400
