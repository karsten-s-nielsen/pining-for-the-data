"""Tests for Lambda handler logic (no AWS needed — mocked S3)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add Lambda src to path so handlers can import shared
LAMBDA_SRC = Path(__file__).parent.parent.parent / "terraform" / "modules" / "functions" / "src"
sys.path.insert(0, str(LAMBDA_SRC))

os.environ.setdefault("API_TOKEN", "pub-tok")
os.environ.setdefault("DATA_BUCKET", "test-bucket")
os.environ.setdefault("OWNER_TOKEN_PARAM", "/test/api_token_owner")

import shared  # noqa: E402


def _mock_s3() -> MagicMock:
    """Create a fresh mock S3 client and inject it into shared module."""
    mock = MagicMock()
    shared._s3_client = mock
    return mock


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Reset shared module state and stub the owner-token fetcher between tests."""
    shared._s3_client = None
    shared._get_owner_token.cache_clear()
    monkeypatch.setattr(shared, "_get_owner_token", lambda: "own-tok")
    monkeypatch.setenv("API_TOKEN", "pub-tok")
    yield
    shared._s3_client = None


class _ResetS3:
    """Mixin (kept for backward compat with subclasses; the autouse fixture handles reset)."""


# ----- Tier enum + SSM fetcher -----


class TestTierEnum:
    def test_tier_values(self) -> None:
        from shared import Tier

        assert Tier.PUBLIC.value == "public"
        assert Tier.OWNER.value == "owner"

    def test_tier_is_str(self) -> None:
        from shared import Tier

        assert Tier.PUBLIC == "public"


# ----- validate_token (new Tier-returning shape, PUBLIC on duplicate) -----


class TestValidateToken:
    def test_public_token(self) -> None:
        from shared import Tier, validate_token

        event = {"headers": {"authorization": "Bearer pub-tok"}}
        assert validate_token(event) == Tier.PUBLIC

    def test_owner_token(self) -> None:
        from shared import Tier, validate_token

        event = {"headers": {"authorization": "Bearer own-tok"}}
        assert validate_token(event) == Tier.OWNER

    def test_owner_token_capitalized_header(self) -> None:
        from shared import Tier, validate_token

        event = {"headers": {"Authorization": "Bearer own-tok"}}
        assert validate_token(event) == Tier.OWNER

    def test_missing_header(self) -> None:
        from shared import validate_token

        result = validate_token({"headers": {}})
        assert isinstance(result, dict)
        assert result["statusCode"] == 401

    def test_wrong_token(self) -> None:
        from shared import validate_token

        result = validate_token({"headers": {"authorization": "Bearer nope"}})
        assert isinstance(result, dict)
        assert result["statusCode"] == 401

    def test_no_bearer_prefix(self) -> None:
        from shared import validate_token

        result = validate_token({"headers": {"authorization": "Basic abc"}})
        assert isinstance(result, dict)
        assert result["statusCode"] == 401

    def test_null_headers(self) -> None:
        from shared import validate_token

        result = validate_token({"headers": None})
        assert isinstance(result, dict)
        assert result["statusCode"] == 401

    def test_same_public_and_owner_classifies_as_public(self, monkeypatch) -> None:
        """Fail closed: if both tokens are the same string, classify as PUBLIC. Spec §3.2."""
        from shared import Tier, validate_token

        monkeypatch.setenv("API_TOKEN", "duplicate-token")
        monkeypatch.setattr(shared, "_get_owner_token", lambda: "duplicate-token")
        result = validate_token({"headers": {"authorization": "Bearer duplicate-token"}})
        assert result == Tier.PUBLIC


# ----- Path-param validator -----


class TestSafeParam:
    def test_rejects_underscore_prefix(self) -> None:
        from shared import validate_path_param

        result = validate_path_param("_private", "provider")
        assert result is not None
        assert result["statusCode"] == 400

    def test_rejects_any_underscore_prefix(self) -> None:
        from shared import validate_path_param

        for value in ["_files", "_admin", "_anything"]:
            result = validate_path_param(value, "id")
            assert result is not None, f"failed to reject {value!r}"
            assert result["statusCode"] == 400

    def test_accepts_underscore_midstring(self) -> None:
        from shared import validate_path_param

        assert validate_path_param("game_03", "id") is None

    def test_accepts_alphanumeric(self) -> None:
        from shared import validate_path_param

        assert validate_path_param("skillcorner", "provider") is None
        assert validate_path_param("m-001", "id") is None
        assert validate_path_param("123", "id") is None


# ----- Query filter parsing -----


class TestParseQueryFilters:
    def test_no_params_returns_empty_filters(self) -> None:
        from shared import parse_query_filters

        result = parse_query_filters({}, allowed={"updatedSince", "dateFrom", "dateTo"})
        assert result == {}

    def test_null_query_string_returns_empty_filters(self) -> None:
        from shared import parse_query_filters

        event = {"queryStringParameters": None}
        result = parse_query_filters(event, allowed={"updatedSince", "dateFrom", "dateTo"})
        assert result == {}

    def test_valid_updated_since(self) -> None:
        from shared import parse_query_filters

        event = {"queryStringParameters": {"updatedSince": "2026-05-01T00:00:00Z"}}
        result = parse_query_filters(event, allowed={"updatedSince"})
        assert result == {"updatedSince": "2026-05-01T00:00:00Z"}

    def test_valid_date_from(self) -> None:
        from shared import parse_query_filters

        event = {"queryStringParameters": {"dateFrom": "2022-11-20"}}
        result = parse_query_filters(event, allowed={"dateFrom", "dateTo"})
        assert result == {"dateFrom": "2022-11-20"}

    def test_valid_date_to(self) -> None:
        from shared import parse_query_filters

        event = {"queryStringParameters": {"dateTo": "2022-12-19"}}
        result = parse_query_filters(event, allowed={"dateFrom", "dateTo"})
        assert result == {"dateTo": "2022-12-19"}

    def test_all_three_combined(self) -> None:
        from shared import parse_query_filters

        event = {
            "queryStringParameters": {
                "updatedSince": "2026-05-01T00:00:00Z",
                "dateFrom": "2022-11-20",
                "dateTo": "2022-12-19",
            }
        }
        result = parse_query_filters(event, allowed={"updatedSince", "dateFrom", "dateTo"})
        assert result == {
            "updatedSince": "2026-05-01T00:00:00Z",
            "dateFrom": "2022-11-20",
            "dateTo": "2022-12-19",
        }

    def test_invalid_updated_since_returns_400(self) -> None:
        from shared import parse_query_filters

        event = {"queryStringParameters": {"updatedSince": "not-a-date"}}
        result = parse_query_filters(event, allowed={"updatedSince"})
        assert isinstance(result, dict)
        assert result["statusCode"] == 400
        assert "updatedSince" in json.loads(result["body"])["error"]

    def test_invalid_date_from_returns_400(self) -> None:
        from shared import parse_query_filters

        event = {"queryStringParameters": {"dateFrom": "2022-13-45"}}
        result = parse_query_filters(event, allowed={"dateFrom", "dateTo"})
        assert isinstance(result, dict)
        assert result["statusCode"] == 400
        assert "dateFrom" in json.loads(result["body"])["error"]

    def test_invalid_date_to_returns_400(self) -> None:
        from shared import parse_query_filters

        event = {"queryStringParameters": {"dateTo": "yesterday"}}
        result = parse_query_filters(event, allowed={"dateFrom", "dateTo"})
        assert isinstance(result, dict)
        assert result["statusCode"] == 400
        assert "dateTo" in json.loads(result["body"])["error"]

    def test_disallowed_param_returns_400(self) -> None:
        from shared import parse_query_filters

        event = {"queryStringParameters": {"dateFrom": "2022-11-20"}}
        result = parse_query_filters(event, allowed={"updatedSince"})
        assert isinstance(result, dict)
        assert result["statusCode"] == 400
        assert "dateFrom" in json.loads(result["body"])["error"]

    def test_unrecognised_params_silently_ignored(self) -> None:
        from shared import parse_query_filters

        event = {"queryStringParameters": {"randomJunk": "hello", "updatedSince": "2026-05-01T00:00:00Z"}}
        result = parse_query_filters(event, allowed={"updatedSince"})
        assert result == {"updatedSince": "2026-05-01T00:00:00Z"}


class TestApplyFilters:
    def test_updated_since_exclusive(self) -> None:
        from shared import apply_filters

        records = [
            {"id": "a", "updated_at": "2026-05-01T00:00:00Z"},
            {"id": "b", "updated_at": "2026-05-02T00:00:00Z"},
            {"id": "c", "updated_at": "2026-05-03T00:00:00Z"},
        ]
        result = apply_filters(records, {"updatedSince": "2026-05-01T00:00:00Z"})
        assert [r["id"] for r in result] == ["b", "c"]

    def test_date_from_inclusive(self) -> None:
        from shared import apply_filters

        records = [
            {"id": "a", "date": "2022-11-20"},
            {"id": "b", "date": "2022-11-21"},
            {"id": "c", "date": "2022-11-19"},
        ]
        result = apply_filters(records, {"dateFrom": "2022-11-20"})
        assert [r["id"] for r in result] == ["a", "b"]

    def test_date_to_exclusive(self) -> None:
        from shared import apply_filters

        records = [
            {"id": "a", "date": "2022-11-20"},
            {"id": "b", "date": "2022-11-21"},
            {"id": "c", "date": "2022-11-22"},
        ]
        result = apply_filters(records, {"dateTo": "2022-11-21"})
        assert [r["id"] for r in result] == ["a"]

    def test_date_from_and_date_to_combined(self) -> None:
        from shared import apply_filters

        records = [
            {"id": "a", "date": "2022-11-19"},
            {"id": "b", "date": "2022-11-20"},
            {"id": "c", "date": "2022-11-21"},
            {"id": "d", "date": "2022-11-22"},
        ]
        result = apply_filters(records, {"dateFrom": "2022-11-20", "dateTo": "2022-11-22"})
        assert [r["id"] for r in result] == ["b", "c"]

    def test_all_three_filters_combined(self) -> None:
        from shared import apply_filters

        records = [
            {"id": "a", "date": "2022-11-20", "updated_at": "2026-04-30T00:00:00Z"},
            {"id": "b", "date": "2022-11-20", "updated_at": "2026-05-02T00:00:00Z"},
            {"id": "c", "date": "2022-11-25", "updated_at": "2026-05-02T00:00:00Z"},
            {"id": "d", "date": "2022-12-01", "updated_at": "2026-05-02T00:00:00Z"},
        ]
        result = apply_filters(
            records,
            {
                "updatedSince": "2026-05-01T00:00:00Z",
                "dateFrom": "2022-11-20",
                "dateTo": "2022-11-30",
            },
        )
        # a: updated_at too old; b: passes all; c: passes all; d: date too late
        assert [r["id"] for r in result] == ["b", "c"]

    def test_null_date_excluded_when_date_from_active(self) -> None:
        from shared import apply_filters

        records = [
            {"id": "a", "date": "2022-11-20"},
            {"id": "b"},  # no date field at all
            {"id": "c", "date": None},
        ]
        result = apply_filters(records, {"dateFrom": "2022-11-01"})
        assert [r["id"] for r in result] == ["a"]

    def test_null_updated_at_excluded_when_updated_since_active(self) -> None:
        from shared import apply_filters

        records = [
            {"id": "a", "updated_at": "2026-05-02T00:00:00Z"},
            {"id": "b"},  # no updated_at
        ]
        result = apply_filters(records, {"updatedSince": "2026-05-01T00:00:00Z"})
        assert [r["id"] for r in result] == ["a"]

    def test_empty_filters_returns_all(self) -> None:
        from shared import apply_filters

        records = [{"id": "a"}, {"id": "b"}]
        result = apply_filters(records, {})
        assert [r["id"] for r in result] == ["a", "b"]


# ----- list_providers (tier-blind, spec §4.2) -----


class TestListProviders:
    def test_public_tier_sees_pff_in_provider_list(self) -> None:
        from list_providers import handler

        mock_s3 = _mock_s3()
        body = json.dumps({"providers": ["skillcorner", "pff"]}).encode()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body))}

        result = handler({"headers": {"authorization": "Bearer pub-tok"}}, None)
        assert result["statusCode"] == 200
        assert "pff" in json.loads(result["body"])["providers"]

    def test_owner_tier_sees_same_provider_list(self) -> None:
        from list_providers import handler

        mock_s3 = _mock_s3()
        body = json.dumps({"providers": ["skillcorner", "pff"]}).encode()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body))}

        result = handler({"headers": {"authorization": "Bearer own-tok"}}, None)
        assert result["statusCode"] == 200
        assert json.loads(result["body"])["providers"] == ["skillcorner", "pff"]

    def test_rejects_no_auth(self) -> None:
        from list_providers import handler

        result = handler({"headers": {}}, None)
        assert result["statusCode"] == 401


# ----- list_matches (tier filter) -----


class TestListMatches:
    def _payload(self):
        return {
            "provider": "sc",
            "matches": [
                {"id": "pub1", "artifacts": {"match": "match.json"}, "visibility": "public"},
                {"id": "priv1", "artifacts": {"match": "match.json"}, "visibility": "private"},
                {"id": "legacy", "artifacts": {"match": "match.json"}},  # missing visibility = public
            ],
        }

    def test_public_tier_sees_public_only(self) -> None:
        from list_matches import handler

        mock_s3 = _mock_s3()
        body = json.dumps(self._payload()).encode()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body))}

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        ids = [m["id"] for m in json.loads(result["body"])["matches"]]
        assert "pub1" in ids
        assert "legacy" in ids
        assert "priv1" not in ids

    def test_owner_tier_sees_all(self) -> None:
        from list_matches import handler

        mock_s3 = _mock_s3()
        body = json.dumps(self._payload()).encode()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body))}

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "sc"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        ids = [m["id"] for m in json.loads(result["body"])["matches"]]
        assert set(ids) == {"pub1", "priv1", "legacy"}

    def test_empty_after_filter_returns_200_not_404(self) -> None:
        from list_matches import handler

        mock_s3 = _mock_s3()
        all_private = {
            "provider": "pff",
            "matches": [
                {"id": "m-001", "artifacts": {"m": "m.json"}, "visibility": "private"},
            ],
        }
        body = json.dumps(all_private).encode()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body))}

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "pff"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        assert json.loads(result["body"])["matches"] == []

    def test_unknown_provider_returns_404(self) -> None:
        from list_matches import handler

        mock_s3 = _mock_s3()
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey()

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "unknown"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 404


# ----- list_matches filtering (query parameters) -----


class TestListMatchesFiltering:
    def _payload(self):
        return {
            "provider": "sc",
            "matches": [
                {
                    "id": "m1",
                    "date": "2024-11-30",
                    "updated_at": "2026-05-01T00:00:00Z",
                    "artifacts": {"match": "match.json"},
                    "visibility": "public",
                },
                {
                    "id": "m2",
                    "date": "2024-12-07",
                    "updated_at": "2026-05-02T00:00:00Z",
                    "artifacts": {"match": "match.json"},
                    "visibility": "public",
                },
                {
                    "id": "m3",
                    "date": "2024-12-21",
                    "updated_at": "2026-05-03T00:00:00Z",
                    "artifacts": {"match": "match.json"},
                    "visibility": "public",
                },
                {
                    "id": "m4",
                    "date": "2024-12-21",
                    "updated_at": "2026-05-03T00:00:00Z",
                    "artifacts": {"match": "match.json"},
                    "visibility": "private",
                },
            ],
        }

    def test_updated_since_filters_matches(self) -> None:
        from list_matches import handler

        mock_s3 = _mock_s3()
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=json.dumps(self._payload()).encode()))
        }

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "sc"},
            "queryStringParameters": {"updatedSince": "2026-05-01T00:00:00Z"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        ids = [m["id"] for m in json.loads(result["body"])["matches"]]
        assert ids == ["m2", "m3", "m4"]

    def test_date_from_filters_matches(self) -> None:
        from list_matches import handler

        mock_s3 = _mock_s3()
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=json.dumps(self._payload()).encode()))
        }

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "sc"},
            "queryStringParameters": {"dateFrom": "2024-12-07"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        ids = [m["id"] for m in json.loads(result["body"])["matches"]]
        assert ids == ["m2", "m3", "m4"]

    def test_date_to_filters_matches(self) -> None:
        from list_matches import handler

        mock_s3 = _mock_s3()
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=json.dumps(self._payload()).encode()))
        }

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "sc"},
            "queryStringParameters": {"dateTo": "2024-12-07"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        ids = [m["id"] for m in json.loads(result["body"])["matches"]]
        assert ids == ["m1"]

    def test_no_filters_returns_all_backwards_compat(self) -> None:
        from list_matches import handler

        mock_s3 = _mock_s3()
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=json.dumps(self._payload()).encode()))
        }

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "sc"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        assert len(json.loads(result["body"])["matches"]) == 4

    def test_visibility_filter_applied_before_query_filters(self) -> None:
        from list_matches import handler

        mock_s3 = _mock_s3()
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=json.dumps(self._payload()).encode()))
        }

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc"},
            "queryStringParameters": {"dateFrom": "2024-12-21"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        ids = [m["id"] for m in json.loads(result["body"])["matches"]]
        # m3 is public + matches date; m4 is private, filtered out by visibility
        assert ids == ["m3"]

    def test_invalid_updated_since_returns_400(self) -> None:
        from list_matches import handler

        _mock_s3()  # inject mock; return value unused — handler rejects before S3 call

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc"},
            "queryStringParameters": {"updatedSince": "not-a-date"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "updatedSince" in json.loads(result["body"])["error"]


# ----- get_artifact (object-form artifacts, no list_objects) -----


class TestGetArtifact:
    def _wire_matches(self, mock_s3, matches_payload):
        body = json.dumps(matches_payload).encode()
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})

        def get_obj(Bucket, Key):
            if Key.endswith("matches.json"):
                return {"Body": MagicMock(read=MagicMock(return_value=body))}
            raise mock_s3.exceptions.NoSuchKey()

        mock_s3.get_object.side_effect = get_obj
        mock_s3.generate_presigned_url.return_value = "https://s3.example.com/presigned"

    def test_public_match_public_tier_returns_302(self) -> None:
        from get_artifact import handler

        mock_s3 = _mock_s3()
        self._wire_matches(
            mock_s3,
            {
                "provider": "sc",
                "matches": [
                    {
                        "id": "g1",
                        "artifacts": {"match": "match.json"},
                        "visibility": "public",
                        "updated_at": "2026-05-02T14:00:00Z",
                    },
                ],
            },
        )

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc", "id": "g1", "artifact": "match"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 302
        assert mock_s3.generate_presigned_url.call_args.kwargs["Params"]["Key"] == "sc/g1/match.json"
        mock_s3.list_objects_v2.assert_not_called()

    def test_private_match_owner_tier_returns_302_with_private_prefix(self) -> None:
        from get_artifact import handler

        mock_s3 = _mock_s3()
        self._wire_matches(
            mock_s3,
            {
                "provider": "pff",
                "matches": [
                    {
                        "id": "m-001",
                        "artifacts": {"metadata": "metadata.json"},
                        "visibility": "private",
                        "updated_at": "2026-05-02T14:00:00Z",
                    },
                ],
            },
        )

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "pff", "id": "m-001", "artifact": "metadata"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 302
        assert mock_s3.generate_presigned_url.call_args.kwargs["Params"]["Key"] == "pff/_private/m-001/metadata.json"
        mock_s3.list_objects_v2.assert_not_called()

    def test_private_match_public_tier_returns_404(self) -> None:
        from get_artifact import handler

        mock_s3 = _mock_s3()
        self._wire_matches(
            mock_s3,
            {
                "provider": "pff",
                "matches": [
                    {
                        "id": "m-001",
                        "artifacts": {"metadata": "metadata.json"},
                        "visibility": "private",
                        "updated_at": "2026-05-02T14:00:00Z",
                    },
                ],
            },
        )

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "pff", "id": "m-001", "artifact": "metadata"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 404
        mock_s3.generate_presigned_url.assert_not_called()

    def test_unknown_match_returns_404(self) -> None:
        from get_artifact import handler

        mock_s3 = _mock_s3()
        self._wire_matches(mock_s3, {"provider": "sc", "matches": []})

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "sc", "id": "missing", "artifact": "match"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 404

    def test_artifact_not_in_whitelist_returns_404(self) -> None:
        from get_artifact import handler

        mock_s3 = _mock_s3()
        self._wire_matches(
            mock_s3,
            {
                "provider": "sc",
                "matches": [
                    {
                        "id": "g1",
                        "artifacts": {"match": "match.json"},
                        "visibility": "public",
                        "updated_at": "2026-05-02T14:00:00Z",
                    },
                ],
            },
        )

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc", "id": "g1", "artifact": "tracking"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 404
        mock_s3.generate_presigned_url.assert_not_called()
        mock_s3.list_objects_v2.assert_not_called()

    def test_artifact_filename_resolves_via_object_lookup(self) -> None:
        from get_artifact import handler

        mock_s3 = _mock_s3()
        self._wire_matches(
            mock_s3,
            {
                "provider": "sc",
                "matches": [
                    {
                        "id": "g1",
                        "artifacts": {"match": "match.json", "tracking": "tracking.jsonl.bz2"},
                        "visibility": "public",
                        "updated_at": "2026-05-02T14:00:00Z",
                    },
                ],
            },
        )

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc", "id": "g1", "artifact": "tracking"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 302
        assert mock_s3.generate_presigned_url.call_args.kwargs["Params"]["Key"] == "sc/g1/tracking.jsonl.bz2"
        mock_s3.list_objects_v2.assert_not_called()

    def test_legacy_match_without_visibility_treated_as_public(self) -> None:
        from get_artifact import handler

        mock_s3 = _mock_s3()
        self._wire_matches(
            mock_s3,
            {
                "provider": "sc",
                "matches": [
                    {"id": "g1", "artifacts": {"match": "match.json"}},
                ],
            },
        )

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc", "id": "g1", "artifact": "match"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 302

    def test_missing_path_parameters(self) -> None:
        from get_artifact import handler

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 400


# ----- list_players + get_player (providers.json gate, private-precedence) -----


class _PlayersWiring:
    def _wire(self, mock_s3, providers=("sc", "pff"), public_payload=None, private_payload=None):
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        providers_body = json.dumps({"providers": list(providers)}).encode()

        def get_obj(Bucket, Key):
            if Key == "providers.json":
                return {"Body": MagicMock(read=MagicMock(return_value=providers_body))}
            if Key.endswith("/_private/players.json"):
                if private_payload is None:
                    raise mock_s3.exceptions.NoSuchKey()
                return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(private_payload).encode()))}
            if Key.endswith("/players.json"):
                if public_payload is None:
                    raise mock_s3.exceptions.NoSuchKey()
                return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(public_payload).encode()))}
            raise mock_s3.exceptions.NoSuchKey()

        mock_s3.get_object.side_effect = get_obj


class TestListPlayers(_PlayersWiring):
    def test_public_tier_sees_only_public_players(self) -> None:
        from list_players import handler

        mock_s3 = _mock_s3()
        self._wire(
            mock_s3,
            public_payload={"provider": "sc", "players": [{"id": "p1", "nickname": "Pub"}]},
            private_payload={"provider": "sc", "players": [{"id": "p2", "nickname": "Priv"}]},
        )

        event = {"headers": {"authorization": "Bearer pub-tok"}, "pathParameters": {"provider": "sc"}}
        result = handler(event, None)
        assert result["statusCode"] == 200
        assert [p["id"] for p in json.loads(result["body"])["players"]] == ["p1"]

    def test_owner_tier_sees_merged_players(self) -> None:
        from list_players import handler

        mock_s3 = _mock_s3()
        self._wire(
            mock_s3,
            public_payload={"provider": "sc", "players": [{"id": "p1", "nickname": "Pub"}]},
            private_payload={"provider": "sc", "players": [{"id": "p2", "nickname": "Priv"}]},
        )

        event = {"headers": {"authorization": "Bearer own-tok"}, "pathParameters": {"provider": "sc"}}
        result = handler(event, None)
        assert result["statusCode"] == 200
        ids = sorted(p["id"] for p in json.loads(result["body"])["players"])
        assert ids == ["p1", "p2"]

    def test_no_indexes_returns_empty_list(self) -> None:
        from list_players import handler

        mock_s3 = _mock_s3()
        self._wire(mock_s3)

        event = {"headers": {"authorization": "Bearer own-tok"}, "pathParameters": {"provider": "sc"}}
        result = handler(event, None)
        assert result["statusCode"] == 200
        assert json.loads(result["body"])["players"] == []

    def test_unknown_provider_returns_404(self) -> None:
        from list_players import handler

        mock_s3 = _mock_s3()
        self._wire(mock_s3, providers=("sc", "pff"))

        event = {"headers": {"authorization": "Bearer pub-tok"}, "pathParameters": {"provider": "made-up"}}
        result = handler(event, None)
        assert result["statusCode"] == 404

    def test_owner_tier_cross_tier_collision_private_wins(self) -> None:
        from list_players import handler

        mock_s3 = _mock_s3()
        self._wire(
            mock_s3,
            public_payload={"provider": "sc", "players": [{"id": "p1", "nickname": "Public Mask"}]},
            private_payload={"provider": "sc", "players": [{"id": "p1", "nickname": "Private Real"}]},
        )

        event = {"headers": {"authorization": "Bearer own-tok"}, "pathParameters": {"provider": "sc"}}
        result = handler(event, None)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert len(body["players"]) == 1
        assert body["players"][0]["nickname"] == "Private Real"


class TestListPlayersFiltering(_PlayersWiring):
    def test_updated_since_filters_players(self) -> None:
        from list_players import handler

        mock_s3 = _mock_s3()
        self._wire(
            mock_s3,
            public_payload={
                "provider": "sc",
                "players": [
                    {"id": "p1", "nickname": "Old", "updated_at": "2026-05-01T00:00:00Z"},
                    {"id": "p2", "nickname": "New", "updated_at": "2026-05-03T00:00:00Z"},
                ],
            },
        )

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc"},
            "queryStringParameters": {"updatedSince": "2026-05-02T00:00:00Z"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        ids = [p["id"] for p in json.loads(result["body"])["players"]]
        assert ids == ["p2"]

    def test_date_from_on_players_returns_400(self) -> None:
        from list_players import handler

        mock_s3 = _mock_s3()
        self._wire(mock_s3)

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc"},
            "queryStringParameters": {"dateFrom": "2022-11-20"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "dateFrom" in json.loads(result["body"])["error"]

    def test_date_to_on_players_returns_400(self) -> None:
        from list_players import handler

        mock_s3 = _mock_s3()
        self._wire(mock_s3)

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc"},
            "queryStringParameters": {"dateTo": "2022-12-19"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "dateTo" in json.loads(result["body"])["error"]

    def test_no_filters_returns_all_backwards_compat(self) -> None:
        from list_players import handler

        mock_s3 = _mock_s3()
        self._wire(
            mock_s3,
            public_payload={
                "provider": "sc",
                "players": [
                    {"id": "p1", "nickname": "A"},
                    {"id": "p2", "nickname": "B"},
                ],
            },
        )

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        assert len(json.loads(result["body"])["players"]) == 2

    def test_invalid_updated_since_returns_400(self) -> None:
        from list_players import handler

        mock_s3 = _mock_s3()
        self._wire(mock_s3)

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc"},
            "queryStringParameters": {"updatedSince": "garbage"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 400


class TestGetPlayer(_PlayersWiring):
    def test_public_tier_can_read_public_player(self) -> None:
        from get_player import handler

        mock_s3 = _mock_s3()
        self._wire(mock_s3, public_payload={"provider": "sc", "players": [{"id": "p1", "nickname": "Pub"}]})

        event = {"headers": {"authorization": "Bearer pub-tok"}, "pathParameters": {"provider": "sc", "id": "p1"}}
        result = handler(event, None)
        assert result["statusCode"] == 200
        assert json.loads(result["body"])["nickname"] == "Pub"

    def test_public_tier_gets_404_for_private_player(self) -> None:
        from get_player import handler

        mock_s3 = _mock_s3()
        self._wire(
            mock_s3,
            public_payload={"provider": "sc", "players": []},
            private_payload={"provider": "sc", "players": [{"id": "p2", "nickname": "Priv"}]},
        )

        event = {"headers": {"authorization": "Bearer pub-tok"}, "pathParameters": {"provider": "sc", "id": "p2"}}
        result = handler(event, None)
        assert result["statusCode"] == 404

    def test_owner_tier_can_read_private_player(self) -> None:
        from get_player import handler

        mock_s3 = _mock_s3()
        self._wire(
            mock_s3,
            public_payload={"provider": "sc", "players": []},
            private_payload={"provider": "sc", "players": [{"id": "p2", "nickname": "Priv"}]},
        )

        event = {"headers": {"authorization": "Bearer own-tok"}, "pathParameters": {"provider": "sc", "id": "p2"}}
        result = handler(event, None)
        assert result["statusCode"] == 200
        assert json.loads(result["body"])["nickname"] == "Priv"

    def test_unknown_player_returns_404(self) -> None:
        from get_player import handler

        mock_s3 = _mock_s3()
        self._wire(mock_s3, public_payload={"provider": "sc", "players": []})

        event = {"headers": {"authorization": "Bearer own-tok"}, "pathParameters": {"provider": "sc", "id": "missing"}}
        result = handler(event, None)
        assert result["statusCode"] == 404

    def test_unknown_provider_returns_404(self) -> None:
        from get_player import handler

        mock_s3 = _mock_s3()
        self._wire(mock_s3, providers=("sc", "pff"))

        event = {"headers": {"authorization": "Bearer own-tok"}, "pathParameters": {"provider": "made-up", "id": "p1"}}
        result = handler(event, None)
        assert result["statusCode"] == 404

    def test_owner_tier_cross_tier_collision_private_wins(self) -> None:
        from get_player import handler

        mock_s3 = _mock_s3()
        self._wire(
            mock_s3,
            public_payload={"provider": "sc", "players": [{"id": "p1", "nickname": "Public Mask"}]},
            private_payload={"provider": "sc", "players": [{"id": "p1", "nickname": "Private Real"}]},
        )

        event = {"headers": {"authorization": "Bearer own-tok"}, "pathParameters": {"provider": "sc", "id": "p1"}}
        result = handler(event, None)
        assert result["statusCode"] == 200
        assert json.loads(result["body"])["nickname"] == "Private Real"


# ----- Pydantic models -----


class TestPydanticModels:
    def test_match_entry_minimal_valid(self):
        from canonical.models import MatchEntry

        m = MatchEntry(
            id="m-001",
            artifacts={"metadata": "metadata.json"},
            visibility="private",
            updated_at="2026-05-02T14:23:11Z",
        )
        assert m.id == "m-001"
        assert m.artifacts == {"metadata": "metadata.json"}

    def test_match_entry_rejects_missing_required(self):
        from pydantic import ValidationError

        from canonical.models import MatchEntry

        with pytest.raises(ValidationError):
            MatchEntry(id="m-001")  # missing artifacts, visibility, updated_at

    def test_match_entry_rejects_artifact_key_with_path_traversal(self):
        from pydantic import ValidationError

        from canonical.models import MatchEntry

        with pytest.raises(ValidationError, match="artifact name"):
            MatchEntry(
                id="m-001",
                artifacts={"../etc/passwd": "evil.txt"},
                visibility="public",
                updated_at="2026-05-02T14:23:11Z",
            )

    def test_match_entry_rejects_artifact_key_with_leading_underscore(self):
        from pydantic import ValidationError

        from canonical.models import MatchEntry

        with pytest.raises(ValidationError, match="artifact name"):
            MatchEntry(
                id="m-001",
                artifacts={"_private": "secret.json"},
                visibility="public",
                updated_at="2026-05-02T14:23:11Z",
            )

    def test_player_record_requires_id(self):
        from pydantic import ValidationError

        from canonical.models import PlayerRecord

        with pytest.raises(ValidationError):
            PlayerRecord(nickname="No ID", visibility="public", updated_at="2026-05-02T14:23:11Z")

    def test_player_record_requires_a_name(self):
        from pydantic import ValidationError

        from canonical.models import PlayerRecord

        with pytest.raises(ValidationError):
            PlayerRecord(id="x", visibility="public", updated_at="2026-05-02T14:23:11Z")

    def test_player_record_accepts_nickname_only(self):
        from canonical.models import PlayerRecord

        p = PlayerRecord(id="x", nickname="Pelé", visibility="public", updated_at="2026-05-02T14:23:11Z")
        assert p.nickname == "Pelé"

    def test_player_record_accepts_firstname_lastname(self):
        from canonical.models import PlayerRecord

        p = PlayerRecord(
            id="x",
            firstName="Test",
            lastName="Player",
            visibility="private",
            updated_at="2026-05-02T14:23:11Z",
        )
        assert p.firstName == "Test"

    def test_player_record_round_trips_unknown_fields(self):
        from canonical.models import PlayerRecord

        p = PlayerRecord.model_validate(
            {
                "id": "x",
                "nickname": "Test",
                "visibility": "public",
                "updated_at": "2026-05-02T14:23:11Z",
                "providerSpecificField": 42,
            }
        )
        dumped = p.model_dump()
        assert dumped["providerSpecificField"] == 42
