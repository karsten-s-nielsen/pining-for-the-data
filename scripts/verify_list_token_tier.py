"""Live verify of the LIST token tier (spec 2026-10-27-list-token-tier-design.md §5.7).

Checks the deployed API with the public, LIST and owner tokens:

  1. the Terraform placeholder presented as a token gets 401;
  2. PUBLIC /matches lists no private entry;
  3. LIST /matches equals OWNER /matches, without and with each filter;
  4. LIST gets 404 on every sampled artifact; the public and owner controls get 302;
  5. LIST /players equals PUBLIC /players;
  6. LIST gets 404 on a private player record and the PUBLIC record on a public one;
  7. LIST /providers equals PUBLIC /providers;
  8. with --compare: every PUBLIC and OWNER body hash and control status equals the snapshot.

Modes: --snapshot PATH (before the merge), --before-token (checks 1, 2, 8), default (1-7, and 8 with
--compare). Output holds counts, provider names and PASS/FAIL only: never a token, a private id or a
private name. Never follows a redirect, so no token reaches a presigned URL.

Usage (owner token from $PINING_FOR_THE_DATA_TOKEN; LIST token from $PINING_LIST_TOKEN or SSM):
    uv run --no-sync python scripts/verify_list_token_tier.py --api "$API_URL" --snapshot snap.json
    uv run --no-sync python scripts/verify_list_token_tier.py --api "$API_URL" --list-token-from-ssm --compare snap.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _verify_http import NoFollow, RateLimiter, with_retry

PUBLIC_TOKEN = "test-token-pining-for-the-data"  # noqa: S105  # pragma: allowlist secret
PLACEHOLDER = "placeholder-set-via-cli-after-first-apply"  # pragma: allowlist secret
LIST_PARAM = "/pining-for-the-data/api_token_list"
FILTERS = (("updatedSince", "2026-10-01T00:00:00Z"), ("dateFrom", "2022-01-01"), ("dateTo", "2023-01-01"))
_DENIED = {"error": "Match not found"}


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    location: str | None


Fetch = Callable[[str, str], Response]
Note = Callable[[str], None]


def make_fetch(api: str, *, limiter: RateLimiter | None = None, opener: Any = None) -> Fetch:
    """A rate-limited GET that never follows a redirect. 429 and 5xx are retried; any other status is returned."""
    base = api.rstrip("/")
    pacer = limiter or RateLimiter(8.0)
    client = opener or urllib.request.build_opener(NoFollow)

    def fetch(path: str, token: str) -> Response:
        def call() -> Response:
            pacer.wait()
            req = urllib.request.Request(f"{base}{path}", headers={"Authorization": f"Bearer {token}"})
            try:
                with client.open(req, timeout=30) as resp:
                    return Response(resp.status, resp.read(), None)
            except urllib.error.HTTPError as e:
                if e.code == 429 or 500 <= e.code < 600:
                    raise
                return Response(e.code, e.read(), e.headers.get("Location") if e.headers else None)

        return with_retry(call)

    return fetch


def _json(response: Response) -> Any:
    return json.loads(response.body.decode("utf-8"))


def _is_private(entry: dict) -> bool:
    return entry.get("visibility", "public") == "private"


def sample_entries(matches: list[dict]) -> tuple[dict | None, dict | None]:
    """The first public and the first private entry, in index order of the OWNER listing."""
    public = next((m for m in matches if not _is_private(m)), None)
    private = next((m for m in matches if _is_private(m)), None)
    return public, private


def _owner_matches(fetch: Fetch, provider: str, owner_token: str) -> list[dict]:
    response = fetch(f"/v1/{provider}/matches", owner_token)
    return _json(response)["matches"] if response.status == 200 else []


def check_placeholder(fetch: Fetch) -> list[str]:
    status = fetch("/v1/providers", PLACEHOLDER).status
    return [] if status == 401 else [f"the placeholder token got {status}, expected 401"]


def check_public_has_no_private(fetch: Fetch, providers: list[str]) -> list[str]:
    problems: list[str] = []
    for provider in providers:
        response = fetch(f"/v1/{provider}/matches", PUBLIC_TOKEN)
        if response.status != 200:
            problems.append(f"{provider}: PUBLIC /matches got {response.status}")
            continue
        private = sum(1 for m in _json(response)["matches"] if _is_private(m))
        if private:
            problems.append(f"{provider}: PUBLIC /matches lists {private} private entries")
    return problems


def check_list_equals_owner(fetch: Fetch, providers: list[str], list_token: str, owner_token: str) -> list[str]:
    problems: list[str] = []
    private_total = 0
    queries = [("no filter", "")] + [(name, "?" + urllib.parse.urlencode({name: value})) for name, value in FILTERS]
    for provider in providers:
        for label, query in queries:
            owner = fetch(f"/v1/{provider}/matches{query}", owner_token)
            listed = fetch(f"/v1/{provider}/matches{query}", list_token)
            if owner.status != 200 or listed.status != 200:
                problems.append(f"{provider} ({label}): OWNER got {owner.status}, LIST got {listed.status}")
            elif listed.body != owner.body:
                problems.append(f"{provider} ({label}): the LIST body differs from the OWNER body")
            if not query and owner.status == 200:
                private_total += sum(1 for m in _json(owner)["matches"] if _is_private(m))
    if private_total == 0:
        problems.append("no private entry in any provider, so this check proves nothing")
    return problems


def check_artifacts(
    fetch: Fetch, providers: list[str], list_token: str, owner_token: str, *, note: Note = print
) -> list[str]:
    problems: list[str] = []
    for provider in providers:
        public, private = sample_entries(_owner_matches(fetch, provider, owner_token))
        for label, entry, control_token in (("public", public, PUBLIC_TOKEN), ("private", private, owner_token)):
            if entry is None:
                note(f"NOTE {provider}: no {label} entry; the {label} artifact part is skipped")
                continue
            for key in sorted(entry.get("artifacts") or {}):
                path = f"/v1/{provider}/matches/{entry['id']}/{key}"
                listed = fetch(path, list_token)
                if listed.status != 404:
                    problems.append(f"{provider}: LIST got {listed.status} on a {label} artifact, expected 404")
                elif _json(listed) != _DENIED:
                    problems.append(f"{provider}: the LIST 404 body on a {label} artifact is not the uniform body")
                control = fetch(path, control_token)
                if control.status != 302:
                    problems.append(f"{provider}: the {label} control got {control.status}, expected 302")
    return problems


def check_players(fetch: Fetch, providers: list[str], list_token: str) -> list[str]:
    problems: list[str] = []
    for provider in providers:
        listed = fetch(f"/v1/{provider}/players", list_token)
        public = fetch(f"/v1/{provider}/players", PUBLIC_TOKEN)
        if (listed.status, listed.body) != (public.status, public.body):
            problems.append(f"{provider}: LIST /players differs from PUBLIC /players")
    return problems


def _player_ids(response: Response) -> list[str]:
    return [p["id"] for p in _json(response)["players"]] if response.status == 200 else []


def check_player_records(
    fetch: Fetch, providers: list[str], list_token: str, owner_token: str, *, note: Note = print
) -> list[str]:
    problems: list[str] = []
    for provider in providers:
        public_ids = _player_ids(fetch(f"/v1/{provider}/players", PUBLIC_TOKEN))
        owner_ids = _player_ids(fetch(f"/v1/{provider}/players", owner_token))
        private_ids = [pid for pid in owner_ids if pid not in set(public_ids)]
        if private_ids:
            status = fetch(f"/v1/{provider}/players/{private_ids[0]}", list_token).status
            if status != 404:
                problems.append(f"{provider}: LIST got {status} on a private player record, expected 404")
        else:
            note(f"NOTE {provider}: no private player; the private player part is skipped")
        if public_ids:
            path = f"/v1/{provider}/players/{sorted(public_ids)[0]}"
            listed, public = fetch(path, list_token), fetch(path, PUBLIC_TOKEN)
            if (listed.status, listed.body) != (public.status, public.body):
                problems.append(f"{provider}: the LIST player record differs from the PUBLIC record")
        else:
            note(f"NOTE {provider}: no public player; the public player part is skipped")
    return problems


def check_providers(fetch: Fetch, list_token: str) -> list[str]:
    listed, public = fetch("/v1/providers", list_token), fetch("/v1/providers", PUBLIC_TOKEN)
    if listed.status != 200 or listed.body != public.body:
        return [f"LIST /providers (status {listed.status}) differs from PUBLIC /providers"]
    return []


def take_snapshot(fetch: Fetch, providers: list[str], owner_token: str) -> dict:
    """Hashes of the PUBLIC and OWNER list bodies and the status of the artifact controls. No ids, no tokens."""
    paths = ["/v1/providers"]
    paths += [f"/v1/{p}/matches" for p in providers] + [f"/v1/{p}/players" for p in providers]
    bodies: dict[str, str] = {}
    for tier, token in (("public", PUBLIC_TOKEN), ("owner", owner_token)):
        for path in paths:
            response = fetch(path, token)
            bodies[f"{tier} {path}"] = f"{response.status} {hashlib.sha256(response.body).hexdigest()}"
    controls: dict[str, int] = {}
    for provider in providers:
        public, private = sample_entries(_owner_matches(fetch, provider, owner_token))
        for tier, entry, token in (("public", public, PUBLIC_TOKEN), ("owner", private, owner_token)):
            if entry is None:
                continue
            # Every key that check 4 controls; named by its index, because a key can hold a match id.
            for index, key in enumerate(sorted(entry.get("artifacts") or {})):
                path = f"/v1/{provider}/matches/{entry['id']}/{key}"
                controls[f"{tier} artifact {provider} #{index}"] = fetch(path, token).status
    return {"version": 1, "bodies": bodies, "controls": controls}


def compare_snapshot(current: dict, saved: dict) -> list[str]:
    problems: list[str] = []
    if saved.get("version") != 1:
        problems.append(f"unknown snapshot version {saved.get('version')!r}")
    for section in ("bodies", "controls"):
        now, then = current.get(section) or {}, saved.get(section) or {}
        problems += [f"{key}: in the snapshot, missing now" for key in sorted(then.keys() - now.keys())]
        problems += [f"{key}: new, not in the snapshot" for key in sorted(now.keys() - then.keys())]
        problems += [
            f"{key}: differs from the snapshot" for key in sorted(now.keys() & then.keys()) if now[key] != then[key]
        ]
    return problems


def run(args: argparse.Namespace, fetch: Fetch, owner_token: str, list_token: str | None) -> int:
    providers_response = fetch("/v1/providers", PUBLIC_TOKEN)
    if providers_response.status != 200:
        print(f"FAIL: PUBLIC /v1/providers got {providers_response.status}")
        return 1
    providers: list[str] = _json(providers_response)["providers"]

    if args.snapshot:
        snapshot = take_snapshot(fetch, providers, owner_token)
        Path(args.snapshot).write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
        print(f"snapshot written: {len(snapshot['bodies'])} body hashes, {len(snapshot['controls'])} control statuses")
        return 0

    checks: list[tuple[int, str, Callable[[], list[str]]]] = [
        (1, "placeholder", lambda: check_placeholder(fetch)),
        (2, "public-has-no-private", lambda: check_public_has_no_private(fetch, providers)),
    ]
    if not args.before_token:
        if not list_token:
            raise ValueError("LIST token required for checks 3-7")
        checks += [
            (3, "list-equals-owner", lambda: check_list_equals_owner(fetch, providers, list_token, owner_token)),
            (4, "artifacts", lambda: check_artifacts(fetch, providers, list_token, owner_token)),
            (5, "players", lambda: check_players(fetch, providers, list_token)),
            (6, "player-records", lambda: check_player_records(fetch, providers, list_token, owner_token)),
            (7, "providers", lambda: check_providers(fetch, list_token)),
        ]
    if args.compare:
        saved = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        checks.append((8, "snapshot", lambda: compare_snapshot(take_snapshot(fetch, providers, owner_token), saved)))

    total = 0
    for number, name, check in checks:
        try:
            problems = check()
        except Exception as e:  # report and continue; the type name only, because a message can hold an id
            problems = [f"{type(e).__name__} during the check"]
        total += len(problems)
        print(f"check {number} {name}: {'PASS' if not problems else 'FAIL'}")
        for problem in problems:
            print(f"  PROBLEM {problem}")
    print("PASS" if total == 0 else f"FAIL: {total} problem(s)")
    return 0 if total == 0 else 1


def read_list_token_from_ssm(client: Any = None) -> str:
    """Read the LIST token from its SSM SecureString (the caller's AWS profile). The value is never printed."""
    ssm = client or boto3.client("ssm")
    return ssm.get_parameter(Name=LIST_PARAM, WithDecryption=True)["Parameter"]["Value"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify the LIST token tier through the live API")
    ap.add_argument("--api", required=True, help="API base URL (the Terraform output api_url)")
    ap.add_argument("--owner-token", default=os.environ.get("PINING_FOR_THE_DATA_TOKEN"))
    ap.add_argument("--list-token-from-ssm", action="store_true", help=f"read the LIST token from {LIST_PARAM}")
    ap.add_argument("--snapshot", metavar="PATH", help="write the PUBLIC/OWNER snapshot and stop (before the merge)")
    ap.add_argument("--compare", metavar="PATH", help="compare with a snapshot (check 8)")
    ap.add_argument("--before-token", action="store_true", help="checks 1, 2 and 8 only (needs --compare)")
    args = ap.parse_args()

    if not args.owner_token:
        ap.error("--owner-token required (or set PINING_FOR_THE_DATA_TOKEN)")
    if args.before_token and not args.compare:
        ap.error("--before-token needs --compare PATH")
    list_token: str | None = None
    if not args.snapshot and not args.before_token:
        list_token = read_list_token_from_ssm() if args.list_token_from_ssm else os.environ.get("PINING_LIST_TOKEN")
        if not list_token:
            ap.error("LIST token required: set PINING_LIST_TOKEN or pass --list-token-from-ssm")

    return run(args, make_fetch(args.api), args.owner_token, list_token)


if __name__ == "__main__":
    sys.exit(main())
