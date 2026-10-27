"""Live verify script for the LIST token tier (spec 2026-10-27-list-token-tier-design.md §5.7, §7.2).

Invented data and a fake API; no network. Every token value here is invented.
"""

from __future__ import annotations

import argparse
import email.message
import io
import json
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

OWN = "owner-token-invented-0123456789abcdefXYZ"  # pragma: allowlist secret
LST = "list-token-invented-0123456789abcdefXYZ"  # pragma: allowlist secret
_PUB_ENTRY = {"id": "SYN-PUB-001", "artifacts": {"meta": "meta.json", "tracking": "tracking.jsonl"}}
_PRIV_ENTRY = {"id": "SYN-PRIV-001", "artifacts": {"tracking": "tracking.jsonl"}, "visibility": "private"}
_DENIED = {"error": "Match not found"}


@pytest.fixture
def vl(load_script):
    return load_script("verify_list_token_tier")


class FakeApi:
    """A fake of the deployed API with one invented provider. ``faults`` switch on one wrong behaviour each."""

    def __init__(self, vl, faults: tuple[str, ...] = ()) -> None:
        self.vl = vl
        self.faults = set(faults)
        self.calls: list[tuple[str, str]] = []
        private = [] if "no_private" in self.faults else [_PRIV_ENTRY]
        self.matches = [_PUB_ENTRY, *private]
        self.public_players = [{"id": "SYN-PL-PUB", "name": "Public Person"}]
        self.private_players = [{"id": "SYN-PL-PRIV", "name": "Private Person"}]

    def _tier(self, token: str) -> str | None:
        return {self.vl.PUBLIC_TOKEN: "public", OWN: "owner", LST: "list"}.get(token)

    def _ok(self, payload: object, status: int = 200):
        return self.vl.Response(status, json.dumps(payload).encode(), None)

    def __call__(self, path: str, token: str):
        self.calls.append((path, token))
        tier = self._tier(token)
        if token == self.vl.PLACEHOLDER and "placeholder_200" in self.faults:
            return self._ok({"providers": ["synthp"]})
        if tier is None:
            return self._ok({"error": "Invalid token"}, 401)
        route = urllib.parse.urlsplit(path)
        parts = route.path.strip("/").split("/")[1:]  # drop "v1"
        if parts == ["providers"]:
            providers = ["synthp", "extra"] if tier == "list" and "providers_differ" in self.faults else ["synthp"]
            return self._ok({"providers": providers})
        if parts == ["synthp", "matches"]:
            sees_private = tier != "public" or "public_private_entry" in self.faults
            if tier == "list" and route.query and "list_filtered_like_public" in self.faults:
                sees_private = False
            if tier == "owner" and "owner_body_changed" in self.faults:
                return self._ok({"provider": "synthp", "matches": [], "changed": True})
            listed = [m for m in self.matches if sees_private or m.get("visibility") != "private"]
            return self._ok({"provider": "synthp", "matches": listed})
        if len(parts) == 4 and parts[:2] == ["synthp", "matches"]:
            private = parts[2] == "SYN-PRIV-001"
            if tier == "list":
                if "list_302" in self.faults:
                    return self.vl.Response(302, b"", "https://s3.example.com/presigned")
                return self._ok(_DENIED, 404)
            if tier == "owner" and "control_404" in self.faults:
                return self._ok(_DENIED, 404)
            if tier == "public" and private:
                return self._ok(_DENIED, 404)
            return self.vl.Response(302, b"", "https://s3.example.com/presigned")
        if parts == ["synthp", "players"]:
            players = list(self.public_players)
            if tier == "owner" or (tier == "list" and "list_players_differ" in self.faults):
                players += self.private_players
            return self._ok({"provider": "synthp", "players": players})
        if len(parts) == 3 and parts[:2] == ["synthp", "players"]:
            pool = list(self.public_players)
            if tier == "owner" or (tier == "list" and "list_private_player_200" in self.faults):
                pool = self.private_players + pool
            if tier == "list" and "list_public_player_differs" in self.faults:
                return self._ok({"id": parts[2], "name": "Someone Else"})
            found = next((p for p in pool if p["id"] == parts[2]), None)
            return self._ok(found) if found else self._ok({"error": "Player not found"}, 404)
        return self._ok({"error": "Provider not found"}, 404)


def _args(**kw) -> argparse.Namespace:
    base = {"snapshot": None, "compare": None, "before_token": False}
    base.update(kw)
    return argparse.Namespace(**base)


def test_placeholder_check_passes_on_401_and_fails_on_200(vl):
    assert vl.check_placeholder(FakeApi(vl)) == []
    assert vl.check_placeholder(FakeApi(vl, ("placeholder_200",)))


def test_public_private_entry_fails(vl):
    assert vl.check_public_has_no_private(FakeApi(vl), ["synthp"]) == []
    assert vl.check_public_has_no_private(FakeApi(vl, ("public_private_entry",)), ["synthp"])


def test_list_equals_owner_passes(vl):
    assert vl.check_list_equals_owner(FakeApi(vl), ["synthp"], LST, OWN) == []


def test_list_differs_with_one_filter_fails(vl):
    problems = vl.check_list_equals_owner(FakeApi(vl, ("list_filtered_like_public",)), ["synthp"], LST, OWN)
    assert len(problems) == len(vl.FILTERS)


def test_no_private_entry_anywhere_fails(vl):
    problems = vl.check_list_equals_owner(FakeApi(vl, ("no_private",)), ["synthp"], LST, OWN)
    assert any("no private entry" in p for p in problems)


def test_artifacts_404_with_controls_pass(vl):
    api = FakeApi(vl)
    assert vl.check_artifacts(api, ["synthp"], LST, OWN, note=lambda _m: None) == []
    list_paths = [p for p, t in api.calls if t == LST]
    assert len(list_paths) == 3  # meta + tracking of the public entry, tracking of the private entry


def test_list_artifact_302_fails(vl):
    assert vl.check_artifacts(FakeApi(vl, ("list_302",)), ["synthp"], LST, OWN, note=lambda _m: None)


def test_control_not_302_fails(vl):
    problems = vl.check_artifacts(FakeApi(vl, ("control_404",)), ["synthp"], LST, OWN, note=lambda _m: None)
    assert any("control" in p for p in problems)


def test_provider_without_private_entries_skipped_and_reported(vl):
    notes: list[str] = []
    assert vl.check_artifacts(FakeApi(vl, ("no_private",)), ["synthp"], LST, OWN, note=notes.append) == []
    assert any("synthp" in n and "private" in n and "skipped" in n for n in notes)


def test_players_list_differs_fails(vl):
    assert vl.check_players(FakeApi(vl), ["synthp"], LST) == []
    assert vl.check_players(FakeApi(vl, ("list_players_differ",)), ["synthp"], LST)


def test_private_player_200_fails(vl):
    assert vl.check_player_records(FakeApi(vl), ["synthp"], LST, OWN, note=lambda _m: None) == []
    api = FakeApi(vl, ("list_private_player_200",))
    assert vl.check_player_records(api, ["synthp"], LST, OWN, note=lambda _m: None)


def test_public_player_mismatch_fails(vl):
    api = FakeApi(vl, ("list_public_player_differs",))
    assert vl.check_player_records(api, ["synthp"], LST, OWN, note=lambda _m: None)


def test_provider_without_private_players_skipped_and_reported(vl):
    api = FakeApi(vl)
    api.private_players = []
    notes: list[str] = []
    assert vl.check_player_records(api, ["synthp"], LST, OWN, note=notes.append) == []
    assert any("synthp" in n and "private" in n and "skipped" in n for n in notes)


def test_providers_mismatch_fails(vl):
    assert vl.check_providers(FakeApi(vl), LST) == []
    assert vl.check_providers(FakeApi(vl, ("providers_differ",)), LST)


def test_snapshot_round_trip_passes(vl):
    saved = vl.take_snapshot(FakeApi(vl), ["synthp"], OWN)
    assert vl.compare_snapshot(vl.take_snapshot(FakeApi(vl), ["synthp"], OWN), saved) == []
    # One control per artifact key, named by its index in sorted key order, never by id (B-IMPL-03).
    assert saved["controls"] == {
        "public artifact synthp #0": 302,
        "public artifact synthp #1": 302,
        "owner artifact synthp #0": 302,
    }


def test_snapshot_hash_differs_fails(vl):
    saved = vl.take_snapshot(FakeApi(vl), ["synthp"], OWN)
    problems = vl.compare_snapshot(vl.take_snapshot(FakeApi(vl, ("owner_body_changed",)), ["synthp"], OWN), saved)
    assert "owner /v1/synthp/matches: differs from the snapshot" in problems
    assert not any("public /v1/synthp/matches" in p for p in problems)


def test_compare_snapshot_key_mismatch_reported(vl):
    current = vl.take_snapshot(FakeApi(vl), ["synthp"], OWN)
    saved = json.loads(json.dumps(current))
    del saved["bodies"]["public /v1/synthp/players"]
    saved["bodies"]["public /v1/gone/matches"] = "200 " + "0" * 64
    problems = vl.compare_snapshot(current, saved)
    assert any("public /v1/gone/matches" in p for p in problems)
    assert any("public /v1/synthp/players" in p for p in problems)


def test_snapshot_file_has_only_hashes_and_statuses(vl, tmp_path: Path):
    path = tmp_path / "snap.json"
    assert vl.run(_args(snapshot=str(path)), FakeApi(vl), OWN, None) == 0
    text = path.read_text(encoding="utf-8")
    snapshot = json.loads(text)
    assert set(snapshot) == {"version", "bodies", "controls"}
    for secret in (OWN, LST, vl.PUBLIC_TOKEN, "SYN-"):
        assert secret not in text


def _http_error(code: int, location: str | None = None) -> urllib.error.HTTPError:
    headers = email.message.Message()
    if location:
        headers["Location"] = location
    return urllib.error.HTTPError("https://api.example.com/v1/x", code, "err", headers, io.BytesIO(b"{}"))


class _Opener:
    def __init__(self, error: urllib.error.HTTPError) -> None:
        self.error = error
        self.calls = 0

    def open(self, req, timeout: float):
        self.calls += 1
        raise self.error


def test_404_not_retried(vl):
    opener = _Opener(_http_error(404))
    fetch = vl.make_fetch("https://api.example.com", limiter=vl.RateLimiter(1e9), opener=opener)
    assert fetch("/v1/x", LST).status == 404
    assert opener.calls == 1


def test_302_returns_location_without_following(vl):
    opener = _Opener(_http_error(302, "https://s3.example.com/presigned"))
    fetch = vl.make_fetch("https://api.example.com", limiter=vl.RateLimiter(1e9), opener=opener)
    response = fetch("/v1/x", OWN)
    assert (response.status, response.location) == (302, "https://s3.example.com/presigned")


def test_list_token_from_ssm_reads_named_parameter(vl):
    seen: dict[str, object] = {}

    class _Ssm:
        def get_parameter(self, Name: str, WithDecryption: bool) -> dict:
            seen.update(Name=Name, WithDecryption=WithDecryption)
            return {"Parameter": {"Value": LST}}

    assert vl.read_list_token_from_ssm(_Ssm()) == LST
    assert seen == {"Name": "/pining-for-the-data/api_token_list", "WithDecryption": True}


def test_before_token_runs_only_checks_1_2_8(vl, tmp_path: Path, capsys):
    path = tmp_path / "snap.json"
    vl.run(_args(snapshot=str(path)), FakeApi(vl), OWN, None)
    capsys.readouterr()
    assert vl.run(_args(before_token=True, compare=str(path)), FakeApi(vl), OWN, None) == 0
    checks = [line.split(":")[0] for line in capsys.readouterr().out.splitlines() if line.startswith("check ")]
    assert [c.split()[1] for c in checks] == ["1", "2", "8"]


def test_full_run_passes(vl, tmp_path: Path, capsys):
    path = tmp_path / "snap.json"
    vl.run(_args(snapshot=str(path)), FakeApi(vl), OWN, None)
    assert vl.run(_args(compare=str(path)), FakeApi(vl), OWN, LST) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "PASS"


def test_empty_list_token_rejected(vl, monkeypatch: pytest.MonkeyPatch, capsys):
    made: list[str] = []
    monkeypatch.setattr(vl, "make_fetch", lambda api, **_kw: made.append(api))
    monkeypatch.setenv("PINING_FOR_THE_DATA_TOKEN", OWN)
    monkeypatch.setenv("PINING_LIST_TOKEN", "")
    monkeypatch.setattr("sys.argv", ["verify_list_token_tier.py", "--api", "https://api.example.com"])
    with pytest.raises(SystemExit) as exc:
        vl.main()
    assert exc.value.code == 2
    assert "LIST token required" in capsys.readouterr().err
    assert made == []


def test_output_never_contains_tokens_or_ids(vl, tmp_path: Path, capsys):
    path = tmp_path / "snap.json"
    vl.run(_args(snapshot=str(path)), FakeApi(vl), OWN, None)
    # No fault may empty the OWNER listing: check 4 samples its entries from it, and it is the
    # only check that builds request paths from private match ids (B-IMPL-01).
    faults = (
        "placeholder_200",
        "public_private_entry",
        "list_filtered_like_public",
        "list_302",
        "control_404",
        "list_players_differ",
        "list_private_player_200",
        "providers_differ",
    )
    assert vl.run(_args(compare=str(path)), FakeApi(vl, faults), OWN, LST) == 1
    out = capsys.readouterr().out
    failed = [line.split()[1] for line in out.splitlines() if line.startswith("check ") and line.endswith(": FAIL")]
    assert failed == ["1", "2", "3", "4", "5", "6", "7", "8"]
    assert "LIST got 302" in out
    assert "control got 404" in out
    for secret in (OWN, LST, vl.PUBLIC_TOKEN, vl.PLACEHOLDER, "SYN-"):
        assert secret not in out
