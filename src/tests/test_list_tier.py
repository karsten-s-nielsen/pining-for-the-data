"""LIST token tier, the tier policy table and the SSM token guard (spec 2026-10-27-list-token-tier-design.md).

No AWS: S3 and SSM are fakes. Every token value here is invented.
"""

from __future__ import annotations

import ast
import importlib
import json
import logging
import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

LAMBDA_SRC = Path(__file__).parent.parent.parent / "terraform" / "modules" / "functions" / "src"
sys.path.insert(0, str(LAMBDA_SRC))

os.environ.setdefault("API_TOKEN", "pub-tok")
os.environ.setdefault("DATA_BUCKET", "test-bucket")
os.environ.setdefault("OWNER_TOKEN_PARAM", "/test/api_token_owner")
os.environ.setdefault("LIST_TOKEN_PARAM", "/test/api_token_list")

import shared  # noqa: E402
from shared import Tier, TierPolicy, policy_for  # noqa: E402

# ----- Tier policy table (spec §5.1) -----


def test_tier_values() -> None:
    assert [t.value for t in Tier] == ["public", "list", "owner"]


def test_policy_table_matches_spec() -> None:
    assert policy_for(Tier.PUBLIC) == TierPolicy(False, True, False)
    assert policy_for(Tier.LIST) == TierPolicy(True, False, False)
    assert policy_for(Tier.OWNER) == TierPolicy(True, True, True)


def test_policy_keys_cover_every_tier() -> None:
    assert set(shared._TIER_POLICIES) == set(Tier)


def test_missing_tier_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(shared._TIER_POLICIES, Tier.LIST)
    assert policy_for(Tier.LIST) == TierPolicy(False, False, False)


# ----- Token classification and the unset guard (spec §5.3, §5.4) -----

PUB = "pub-tok"  # pragma: allowlist secret
OWN = "owner-token-invented-0123456789abcdefXYZ"  # pragma: allowlist secret
LST = "list-token-invented-0123456789abcdefXYZ"  # pragma: allowlist secret
_OWNER_PARAM = "/test/api_token_owner"
_LIST_PARAM = "/test/api_token_list"
_PLACEHOLDER = "placeholder-set-via-cli-after-first-apply"


def _event(token: str, header: str = "authorization") -> dict:
    return {"headers": {header: f"Bearer {token}"}}


def _raw_event(auth: str) -> dict:
    return {"headers": {"authorization": auth}}


def _status(result: object) -> int | None:
    return result["statusCode"] if isinstance(result, dict) else None


def _records(caplog: pytest.LogCaptureFixture, message: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == message]


class _FakeSSM:
    """SSM double: each parameter name maps to a value, an exception, or a list of those (consumed in order)."""

    def __init__(self, values: dict[str, object]) -> None:
        self.values = values
        self.calls: list[str] = []

    def get_parameter(self, Name: str, WithDecryption: bool) -> dict:
        self.calls.append(Name)
        assert WithDecryption is True
        outcome = self.values[Name]
        if isinstance(outcome, list):
            outcome = outcome.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return {"Parameter": {"Value": outcome}}


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both SSM fetchers stubbed with invented, usable tokens."""
    monkeypatch.setenv("API_TOKEN", PUB)
    monkeypatch.setattr(shared, "_get_owner_token", lambda: OWN)
    monkeypatch.setattr(shared, "_get_list_token", lambda: LST)


@pytest.fixture
def real_fetchers(monkeypatch: pytest.MonkeyPatch):
    """The real guarded fetchers over a fake SSM client. Call the returned function with the stored values."""
    monkeypatch.setenv("API_TOKEN", PUB)
    monkeypatch.setenv("OWNER_TOKEN_PARAM", _OWNER_PARAM)
    monkeypatch.setenv("LIST_TOKEN_PARAM", _LIST_PARAM)
    shared._get_owner_token.cache_clear()
    shared._get_list_token.cache_clear()

    def install(owner: object = OWN, listed: object = LST) -> _FakeSSM:
        fake = _FakeSSM({_OWNER_PARAM: owner, _LIST_PARAM: listed})
        monkeypatch.setattr(shared, "_ssm_client", fake)
        return fake

    yield install
    shared._get_owner_token.cache_clear()
    shared._get_list_token.cache_clear()


@pytest.mark.parametrize("header", ["authorization", "Authorization"])
@pytest.mark.usefixtures("stubbed")
def test_list_token_gives_list(header: str) -> None:
    assert shared.validate_token(_event(LST, header)) == Tier.LIST


def test_list_equals_public_gives_public_and_logs_collision(
    stubbed: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(shared, "_get_list_token", lambda: PUB)
    assert shared.validate_token(_event(PUB)) == Tier.PUBLIC
    assert len(_records(caplog, "token_collision")) == 1


def test_list_equals_owner_gives_public_and_logs_collision(
    stubbed: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(shared, "_get_list_token", lambda: OWN)
    assert shared.validate_token(_event(OWN)) == Tier.PUBLIC
    assert len(_records(caplog, "token_collision")) == 1


@pytest.mark.usefixtures("stubbed")
def test_non_ascii_presented_token_gets_401() -> None:
    assert _status(shared.validate_token(_event("tést"))) == 401


@pytest.mark.usefixtures("stubbed")
def test_lone_surrogate_presented_token_gets_401() -> None:
    assert _status(shared.validate_token(_event("\ud800abc"))) == 401


def test_empty_bearer_never_matches_disabled_tier(stubbed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shared, "_get_list_token", lambda: None)
    assert _status(shared.validate_token(_raw_event("Bearer "))) == 401


@pytest.mark.parametrize("tok", [PUB, LST, OWN], ids=["public", "list", "owner"])
@pytest.mark.usefixtures("stubbed")
def test_whitespace_padded_token_gets_401(tok: str) -> None:
    assert _status(shared.validate_token(_raw_event(f"Bearer {tok} "))) == 401
    assert _status(shared.validate_token(_raw_event(f"Bearer  {tok}"))) == 401


def test_list_placeholder_disables_tier_and_logs_unset(real_fetchers, caplog: pytest.LogCaptureFixture) -> None:
    real_fetchers(listed=_PLACEHOLDER)
    assert _status(shared.validate_token(_event(_PLACEHOLDER))) == 401
    assert len(_records(caplog, "list_token_unset")) == 1


def test_list_length_boundary(real_fetchers) -> None:
    real_fetchers(listed="a" * 31)
    assert _status(shared.validate_token(_event("a" * 31))) == 401
    shared._get_list_token.cache_clear()
    real_fetchers(listed="a" * 32)
    assert shared.validate_token(_event("a" * 32)) == Tier.LIST


_BAD_SYNTAX = ["x" * 32 + "é", "x" * 16 + " " + "x" * 16, "x" * 32 + "\n"]


@pytest.mark.parametrize("stored", _BAD_SYNTAX, ids=["non-ascii", "space", "trailing-newline"])
def test_list_bad_syntax_disables_tier_others_unaffected(real_fetchers, stored: str) -> None:
    real_fetchers(listed=stored)
    assert _status(shared.validate_token(_event(stored))) == 401
    assert shared.validate_token(_event(PUB)) == Tier.PUBLIC
    assert shared.validate_token(_event(OWN)) == Tier.OWNER


def test_list_unset_logged_once_per_container(real_fetchers, caplog: pytest.LogCaptureFixture) -> None:
    fake = real_fetchers(listed=_PLACEHOLDER)
    shared.validate_token(_event(PUB))
    shared.validate_token(_event(PUB))
    assert len(_records(caplog, "list_token_unset")) == 1
    assert fake.calls.count(_LIST_PARAM) == 1


def test_list_fetch_failure_isolated(real_fetchers, caplog: pytest.LogCaptureFixture) -> None:
    real_fetchers(listed=[RuntimeError("ssm unavailable")] * 3)
    assert _status(shared.validate_token(_event(LST))) == 401
    assert shared.validate_token(_event(PUB)) == Tier.PUBLIC
    assert shared.validate_token(_event(OWN)) == Tier.OWNER
    assert len(_records(caplog, "list_token_fetch_failed")) == 3


def test_list_fetch_retried_after_failure(real_fetchers) -> None:
    fake = real_fetchers(listed=[RuntimeError("ssm unavailable"), LST])
    assert _status(shared.validate_token(_event(LST))) == 401
    assert shared.validate_token(_event(LST)) == Tier.LIST
    assert fake.calls.count(_LIST_PARAM) == 2


def test_list_param_env_missing_isolated(
    real_fetchers, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    real_fetchers()
    monkeypatch.delenv("LIST_TOKEN_PARAM")
    assert _status(shared.validate_token(_event(LST))) == 401
    assert shared.validate_token(_event(PUB)) == Tier.PUBLIC
    assert shared.validate_token(_event(OWN)) == Tier.OWNER
    assert _records(caplog, "list_token_fetch_failed")


_OWNER_UNUSABLE = [_PLACEHOLDER, "a" * 31, "x" * 32 + "é", "x" * 32 + "\n"]


@pytest.mark.parametrize("stored", _OWNER_UNUSABLE, ids=["placeholder", "31-chars", "non-ascii", "trailing-newline"])
def test_owner_unusable_disables_owner(real_fetchers, caplog: pytest.LogCaptureFixture, stored: str) -> None:
    real_fetchers(owner=stored)
    assert _status(shared.validate_token(_event(stored))) == 401
    assert shared.validate_token(_event(PUB)) == Tier.PUBLIC
    assert shared.validate_token(_event(LST)) == Tier.LIST
    assert len(_records(caplog, "owner_token_unset")) == 1


def test_owner_fetch_failure_gives_500(real_fetchers) -> None:
    real_fetchers(owner=[RuntimeError("ssm unavailable")] * 3)
    for tok in (PUB, LST, OWN):
        assert _status(shared.validate_token(_event(tok))) == 500


def test_no_token_value_in_logs(real_fetchers, caplog: pytest.LogCaptureFixture) -> None:
    secrets = [PUB, OWN, LST, _PLACEHOLDER, *_BAD_SYNTAX, *_OWNER_UNUSABLE]
    scenarios: list[tuple[object, object, str]] = [
        (OWN, PUB, PUB),  # LIST == PUBLIC collision
        (OWN, OWN, OWN),  # LIST == OWNER collision
        (OWN, _PLACEHOLDER, _PLACEHOLDER),  # LIST guard
        *[(OWN, bad, bad) for bad in _BAD_SYNTAX],
        (OWN, [RuntimeError("ssm unavailable")], LST),  # LIST isolation
        *[(bad, LST, bad) for bad in _OWNER_UNUSABLE],  # owner guard
    ]
    for owner, listed, presented in scenarios:
        shared._get_owner_token.cache_clear()
        shared._get_list_token.cache_clear()
        real_fetchers(owner=owner, listed=listed)
        shared.validate_token(_event(presented))
    formatter = logging.Formatter()
    assert caplog.records
    for record in caplog.records:
        text = formatter.format(record) + repr(record.args)
        for secret in secrets:
            assert secret not in text, f"{record.getMessage()} leaks a token value"


# ----- Every endpoint, every tier (spec §5.2, §5.6) -----

_TOKENS = {Tier.PUBLIC: PUB, Tier.LIST: LST, Tier.OWNER: OWN}
_HANDLERS = ["list_providers", "list_matches", "get_artifact", "list_players", "get_player", "health"]
_SYN_MATCHES = {
    "provider": "synthp",
    "matches": [
        {
            "id": "SYN-PUB-001",
            "artifacts": {"tracking": "tracking.jsonl"},
            "visibility": "public",
            "date": "2099-01-10",
            "updated_at": "2099-01-10T00:00:00Z",
        },
        {
            "id": "SYN-PRIV-001",
            "artifacts": {"tracking": "tracking.jsonl"},
            "visibility": "private",
            "date": "2099-02-10",
            "updated_at": "2099-02-10T00:00:00Z",
        },
    ],
}
_PUBLIC_PLAYERS = {"players": [{"id": "SYN-PL-PUB", "name": "Public Person"}, {"id": "SYN-PL-BOTH", "name": "Twin A"}]}
_PRIVATE_PLAYERS = {
    "players": [{"id": "SYN-PL-PRIV", "name": "Private Person"}, {"id": "SYN-PL-BOTH", "name": "Twin B"}]
}
_DENIED = {"error": "Match not found"}


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """A mocked S3 client with one invented provider: public and private matches and players."""
    mock = MagicMock()
    mock.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
    objects = {
        "providers.json": {"providers": ["synthp"]},
        "synthp/matches.json": _SYN_MATCHES,
        "synthp/players.json": _PUBLIC_PLAYERS,
        "synthp/_private/players.json": _PRIVATE_PLAYERS,
    }

    def get_obj(Bucket: str, Key: str) -> dict:
        if Key not in objects:
            raise mock.exceptions.NoSuchKey()
        return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(objects[Key]).encode()))}

    mock.get_object.side_effect = get_obj
    mock.generate_presigned_url.return_value = "https://s3.example.com/presigned"
    monkeypatch.setattr(shared, "_s3_client", mock)
    return mock


def _call(module: str, tier: Tier, query: dict | None = None, **params: str) -> dict:
    handler = importlib.import_module(module).handler
    event = {"headers": {"authorization": f"Bearer {_TOKENS[tier]}"}, "pathParameters": params}
    if query is not None:
        event["queryStringParameters"] = query
    return handler(event, None)


def _private_index_reads(s3_mock: MagicMock) -> list[str]:
    keys = [c.kwargs.get("Key", "") for c in s3_mock.get_object.call_args_list]
    return [k for k in keys if k.endswith("/_private/players.json")]


@pytest.mark.parametrize("tier", list(Tier))
@pytest.mark.usefixtures("stubbed")
def test_list_providers_same_for_every_tier(s3: MagicMock, tier: Tier) -> None:
    result = _call("list_providers", tier)
    assert result["statusCode"] == 200
    assert result["body"] == _call("list_providers", Tier.PUBLIC)["body"]


@pytest.mark.parametrize(
    "query",
    [None, {"updatedSince": "2099-01-15T00:00:00Z"}, {"dateFrom": "2099-01-15"}, {"dateTo": "2099-03-01"}],
    ids=["none", "updatedSince", "dateFrom", "dateTo"],
)
@pytest.mark.usefixtures("stubbed")
def test_list_matches_list_equals_owner(s3: MagicMock, query: dict | None) -> None:
    owner = _call("list_matches", Tier.OWNER, query, provider="synthp")
    listed = _call("list_matches", Tier.LIST, query, provider="synthp")
    assert owner["statusCode"] == listed["statusCode"] == 200
    assert "SYN-PRIV-001" in owner["body"]
    assert listed["body"] == owner["body"]


@pytest.mark.usefixtures("stubbed")
def test_list_matches_public_has_no_private(s3: MagicMock) -> None:
    ids = [m["id"] for m in json.loads(_call("list_matches", Tier.PUBLIC, provider="synthp")["body"])["matches"]]
    assert ids == ["SYN-PUB-001"]


@pytest.mark.usefixtures("stubbed")
def test_list_matches_unknown_provider_same_for_list_and_owner(s3: MagicMock) -> None:
    for tier in (Tier.LIST, Tier.OWNER):
        result = _call("list_matches", tier, provider="nosuch")
        assert result["statusCode"] == 404
        assert json.loads(result["body"]) == {"error": "Provider not found"}


@pytest.mark.parametrize(
    ("provider", "match_id", "artifact"),
    [
        ("synthp", "SYN-PUB-001", "tracking"),
        ("synthp", "SYN-PRIV-001", "tracking"),
        ("synthp", "SYN-NONE-001", "tracking"),
        ("synthp", "SYN-PUB-001", "events"),
        ("nosuch", "SYN-PUB-001", "tracking"),
    ],
    ids=["public", "private", "unknown-match", "unknown-artifact", "unknown-provider"],
)
@pytest.mark.usefixtures("stubbed")
def test_get_artifact_list_gets_uniform_404_without_s3(
    s3: MagicMock, caplog: pytest.LogCaptureFixture, provider: str, match_id: str, artifact: str
) -> None:
    caplog.set_level(logging.INFO)
    result = _call("get_artifact", Tier.LIST, provider=provider, id=match_id, artifact=artifact)
    assert result["statusCode"] == 404
    assert json.loads(result["body"]) == _DENIED
    s3.get_object.assert_not_called()
    s3.generate_presigned_url.assert_not_called()
    denied = _records(caplog, "list_tier_artifact_denied")
    assert len(denied) == 1
    assert getattr(denied[0], "handler", None) == "get_artifact"


@pytest.mark.usefixtures("stubbed")
def test_get_artifact_list_invalid_param_gets_400(s3: MagicMock) -> None:
    assert _call("get_artifact", Tier.LIST, provider="synthp", id="_bad", artifact="tracking")["statusCode"] == 400


@pytest.mark.usefixtures("stubbed")
def test_get_artifact_public_and_owner_unchanged(s3: MagicMock) -> None:
    assert (
        _call("get_artifact", Tier.PUBLIC, provider="synthp", id="SYN-PUB-001", artifact="tracking")["statusCode"]
        == 302
    )
    private = _call("get_artifact", Tier.PUBLIC, provider="synthp", id="SYN-PRIV-001", artifact="tracking")
    assert private["statusCode"] == 404
    assert json.loads(private["body"]) == _DENIED
    for match_id, key in [
        ("SYN-PUB-001", "synthp/SYN-PUB-001/tracking.jsonl"),
        ("SYN-PRIV-001", "synthp/_private/SYN-PRIV-001/tracking.jsonl"),
    ]:
        assert (
            _call("get_artifact", Tier.OWNER, provider="synthp", id=match_id, artifact="tracking")["statusCode"] == 302
        )
        assert s3.generate_presigned_url.call_args.kwargs["Params"]["Key"] == key


@pytest.mark.usefixtures("stubbed")
def test_list_players_list_equals_public_and_no_private_read(s3: MagicMock) -> None:
    public = _call("list_players", Tier.PUBLIC, provider="synthp")
    s3.get_object.reset_mock()
    listed = _call("list_players", Tier.LIST, provider="synthp")
    assert listed["statusCode"] == 200
    assert listed["body"] == public["body"]
    assert _private_index_reads(s3) == []


@pytest.mark.parametrize(
    ("player_id", "status"),
    [("SYN-PL-PRIV", 404), ("SYN-PL-PUB", 200), ("SYN-PL-BOTH", 200)],
    ids=["private-only", "public", "both"],
)
@pytest.mark.usefixtures("stubbed")
def test_get_player_list_behaves_like_public(s3: MagicMock, player_id: str, status: int) -> None:
    public = _call("get_player", Tier.PUBLIC, provider="synthp", id=player_id)
    s3.get_object.reset_mock()
    listed = _call("get_player", Tier.LIST, provider="synthp", id=player_id)
    assert listed["statusCode"] == public["statusCode"] == status
    assert listed["body"] == public["body"]
    assert _private_index_reads(s3) == []
    if player_id == "SYN-PL-BOTH":
        assert json.loads(listed["body"])["name"] == "Twin A"


def test_health_needs_no_token() -> None:
    assert importlib.import_module("health").handler({"headers": {}}, None)["statusCode"] == 200


def test_handlers_never_compare_tier() -> None:
    for name in _HANDLERS:
        tree = ast.parse((LAMBDA_SRC / f"{name}.py").read_text(encoding="utf-8"))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        assert "Tier" not in names | attrs | imported, f"{name} refers to Tier"
        bound = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "validate_token"
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                assert not any(isinstance(o, ast.Name) and o.id in bound for o in operands), f"{name} compares the tier"


# ----- Terraform wiring (spec §5.5); text checks, no Terraform run -----

_REPO = Path(__file__).parent.parent.parent
_DEV_MAIN = _REPO / "terraform" / "environments" / "dev" / "main.tf"
_FUNCTIONS_MAIN = _REPO / "terraform" / "modules" / "functions" / "main.tf"


def _ssm_placeholder(text: str, name: str) -> str:
    block = re.search(rf'resource "aws_ssm_parameter" "{name}" \{{(.*?)\n\}}', text, flags=re.DOTALL)
    assert block, f"no aws_ssm_parameter.{name}"
    value = re.search(r'^\s*value\s*=\s*"([^"]*)"', block.group(1), flags=re.MULTILINE)
    assert value, f"aws_ssm_parameter.{name} has no value"
    return value.group(1)


def _lambda_blocks(text: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    for part in re.split(r'(?m)^resource "', text)[1:]:
        header, _, body = part.partition("\n")
        if header.startswith('aws_lambda_function" '):
            blocks[header.split('"')[2]] = body
    return blocks


def test_terraform_placeholders_are_rejected_by_guard() -> None:
    text = _DEV_MAIN.read_text(encoding="utf-8")
    for name in ("api_token_owner", "api_token_list"):
        value = _ssm_placeholder(text, name)
        assert value == shared._PLACEHOLDER_TOKEN
        assert shared._usable_token(value) is False


def test_five_authed_lambdas_set_both_token_params() -> None:
    blocks = _lambda_blocks(_FUNCTIONS_MAIN.read_text(encoding="utf-8"))
    authed = {name: body for name, body in blocks.items() if "OWNER_TOKEN_PARAM" in body}
    assert len(authed) == 5
    for name, body in authed.items():
        assert re.search(r"LIST_TOKEN_PARAM\s*=\s*var\.list_token_param_name", body), name
    assert "OWNER_TOKEN_PARAM" not in blocks["health"]
    assert "LIST_TOKEN_PARAM" not in blocks["health"]


def test_lambda_role_may_read_list_param() -> None:
    text = _FUNCTIONS_MAIN.read_text(encoding="utf-8")
    statement = re.search(r'Action\s*=\s*"ssm:GetParameter"\s*\n\s*Resource\s*=\s*(\[[^\]]*\]|[^\n]+)', text)
    assert statement, "no ssm:GetParameter statement"
    assert "var.list_token_param_arn" in statement.group(1)
