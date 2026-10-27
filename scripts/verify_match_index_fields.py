"""Live verification of the competition/season fields and ``artifact_meta`` (spec §5.11, ADR 0013).

Reads through the HTTP API with the owner token, so it checks what consumers see. For every entry
of every provider:

- the metadata match id equals the entry ``id`` (the metadata artifact is fetched through the API);
- the four fields equal what the provider's reader gives for that metadata (absent = absent);
- the ``artifact_meta`` keys equal the ``artifacts`` keys;
- each ``size`` equals the object size from a one-byte Range GET (``Content-Range`` total). S3
  answers a Range GET on a zero-byte object with ``416``: the object is then fetched whole, and
  size 0 is accepted only for an empty body;
- with ``--full-hash``: every artifact is downloaded and its ``sha256`` and size are compared.

API Gateway throttles at 10 requests per second (api-reference.md, "Rate Limits"). The script keeps
its API calls at 8 per second and retries ``429`` and ``5xx`` with exponential backoff. The owner
token is read from ``$PINING_FOR_THE_DATA_TOKEN`` by default and is never printed.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))
# scripts/ is not a package — make the sibling helper module importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _verify_http import (  # noqa: E402
    NoFollow,
    RateLimiter,
    get_json,
    parse_content_range_total,
    with_retry,
)

from formats.provider_metadata import MetadataIdentity, metadata_artifact_key, read_metadata_identity  # noqa: E402
from mock_api.artifact_meta import ArtifactDigest, hash_stream  # noqa: E402

_FIELDS = ("competition_id", "competition_name", "season_id", "season_name")


def check_entry(entry: Mapping[str, Any], identity: MetadataIdentity) -> list[str]:
    """Problems with one index entry against its metadata identity; empty means pass."""
    eid = entry.get("id")
    problems: list[str] = []
    if identity.match_id != str(eid):
        problems.append(f"{eid}: metadata match id is {identity.match_id!r}")
    want = identity.competition.as_kwargs()
    for name in _FIELDS:
        if entry.get(name) != want[name]:
            problems.append(f"{eid}: {name} is {entry.get(name)!r}, metadata gives {want[name]!r}")
    meta = entry.get("artifact_meta")
    if meta is None:
        problems.append(f"{eid}: no artifact_meta")
    elif set(meta) != set(entry.get("artifacts") or {}):
        problems.append(f"{eid}: artifact_meta keys {sorted(meta)} != artifacts keys {sorted(entry['artifacts'])}")
    return problems


def compare_digest(name: str, expected: Mapping[str, Any], actual: ArtifactDigest) -> list[str]:
    """Problems between an ``artifact_meta`` value and the downloaded bytes' digest; empty means pass."""
    problems: list[str] = []
    if expected.get("sha256") != actual.sha256:
        problems.append(f"{name}: sha256 is {expected.get('sha256')} in artifact_meta, {actual.sha256} downloaded")
    if expected.get("size") != actual.size:
        problems.append(f"{name}: size is {expected.get('size')} in artifact_meta, {actual.size} downloaded")
    return problems


def size_from_range(status: int, content_range: str | None, fetch_full: Callable[[], bytes]) -> int:
    """Object size from a one-byte Range GET. Raises ``ValueError`` on any unexpected answer."""
    if status == 206:
        total = parse_content_range_total(content_range or "")
        if total < 0:
            raise ValueError(f"206 without a Content-Range total: {content_range!r}")
        return total
    if status == 416:
        # S3 refuses any Range on a zero-byte object; accept 0 only when the whole body is empty.
        body = fetch_full()
        if body:
            raise ValueError(f"416 on a Range GET, but the object has {len(body)} bytes")
        return 0
    raise ValueError(f"unexpected status {status} for a Range GET")


class _Api:
    """Owner-token API access: rate-limited, retried, and never following the 302 with the token."""

    def __init__(self, api: str, token: str) -> None:
        self.api = api.rstrip("/")
        self._token = token
        self._limiter = RateLimiter(8.0)

    def json(self, path: str) -> Any:
        def call() -> Any:
            self._limiter.wait()
            return get_json(self.api, path, self._token)

        return with_retry(call)

    def presigned(self, path: str) -> str:
        """The presigned URL of an artifact (the API answers 302)."""

        def call() -> str:
            self._limiter.wait()
            req = urllib.request.Request(f"{self.api}{path}", headers={"Authorization": f"Bearer {self._token}"})
            try:
                with urllib.request.build_opener(NoFollow).open(req, timeout=30) as resp:
                    raise ValueError(f"{path}: expected 302, got {resp.status}")
            except urllib.error.HTTPError as e:
                if e.code == 302 and e.headers.get("Location"):
                    return e.headers["Location"]
                raise

        return with_retry(call)


def _get(url: str, headers: dict[str, str] | None = None) -> tuple[int, str | None, bytes]:
    """GET a presigned URL with a clean request (no Authorization header)."""

    def call() -> tuple[int, str | None, bytes]:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=120) as resp:
                return resp.status, resp.headers.get("Content-Range"), resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 416:
                return 416, None, b""
            raise

    return with_retry(call)


def _hash_url(url: str) -> ArtifactDigest:
    def call() -> ArtifactDigest:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=600) as resp:
            return hash_stream(resp)

    return with_retry(call)


def verify_entry(api: _Api, provider: str, entry: Mapping[str, Any], *, full_hash: bool) -> list[str]:
    eid = entry.get("id")
    base = f"/v1/{provider}/matches/{eid}"
    metadata_key = metadata_artifact_key(provider, entry)
    _status, _range, body = _get(api.presigned(f"{base}/{metadata_key}"))
    identity = read_metadata_identity(provider, entry.get("visibility", "public"), body)
    problems = check_entry(entry, identity)
    meta = entry.get("artifact_meta") or {}
    for name in sorted(entry.get("artifacts") or {}):
        expected = meta.get(name)
        if expected is None:
            continue  # already reported by check_entry
        url = api.presigned(f"{base}/{name}")
        if full_hash:
            problems += [f"{eid}: {p}" for p in compare_digest(name, expected, _hash_url(url))]
            continue
        status, content_range, _ = _get(url, {"Range": "bytes=0-0"})
        size = size_from_range(status, content_range, lambda url=url: _get(url)[2])
        if size != expected.get("size"):
            problems.append(f"{eid}: {name} size is {expected.get('size')} in artifact_meta, {size} stored")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify competition/season fields and artifact_meta through the live API")
    ap.add_argument("--api", required=True, help="API base URL (the Terraform output api_url)")
    ap.add_argument("--owner-token", default=os.environ.get("PINING_FOR_THE_DATA_TOKEN"))
    ap.add_argument("--provider", action="append", help="repeatable; default: every provider from /v1/providers")
    ap.add_argument("--full-hash", action="store_true", help="download every artifact and check sha256 (slow)")
    args = ap.parse_args()
    if not args.owner_token:
        ap.error("--owner-token required (or set PINING_FOR_THE_DATA_TOKEN)")

    api = _Api(args.api, args.owner_token)
    providers = args.provider or api.json("/v1/providers")["providers"]
    problems: list[str] = []
    for provider in providers:
        matches = api.json(f"/v1/{provider}/matches")["matches"]
        before = len(problems)
        for entry in matches:
            try:
                problems += [f"{provider} {p}" for p in verify_entry(api, provider, entry, full_hash=args.full_hash)]
            except Exception as e:  # report and continue: one bad entry must not hide the others
                problems.append(f"{provider} {entry.get('id')}: {type(e).__name__}: {e}")
        print(f"{provider}: {len(matches)} entries checked, {len(problems) - before} problem(s)")

    for problem in problems:
        print(f"PROBLEM {problem}")
    print(f"{'FAIL' if problems else 'PASS'}: {len(problems)} problem(s){' (full hash)' if args.full_hash else ''}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
