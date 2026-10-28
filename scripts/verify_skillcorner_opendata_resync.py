"""Post-resync verification for the public SkillCorner opendata tier (ADR 0015).

Proves, against the LIVE API and store, that the re-sync brought every public opendata entry to the
pinned upstream commit — not only the re-sent artifacts (stamping an entry whose bytes do not match
the pin is the failure this guards):

  - the public listing still returns all twenty public opendata entries, every expected id present;
  - every public entry carries ``source.revision == UPSTREAM_COMMIT``;
  - for all four artifacts of every entry, the STORED object's sha256 and size equal both (a) the
    pinned upstream file and (b) the entry's ``artifact_meta`` — so the store matches the pin and
    ``artifact_meta`` matches the stored bytes (a partial write is caught). The three small roles and
    the ~90 MB ``tracking_extrapolated`` are all hashed from the downloaded stored bytes; the pin
    identity for ``tracking`` is the upstream Git-LFS pointer oid/size (no upstream blob download).

Downloading every stored object also proves servability; a still-unresolved Git-LFS pointer served in
place of the tracking blob is caught by the identity check (its bytes hash does not equal the pin oid).

The pure checks take injected digest callables and are unit-tested; ``main`` wires them to the live
API and the pinned upstream. No licensed id->entity tuple is committed.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from _verify_http import NoFollow, get_json  # noqa: E402
from upload_skillcorner_opendata import UPSTREAM_COMMIT  # noqa: E402
from upload_skillcorner_opendata import http_get as fetch_raw  # noqa: E402
from verify_skillcorner_opendata_load import NEW_MATCH_IDS  # noqa: E402

from formats.skillcorner_opendata import is_lfs_pointer, opendata_files, parse_lfs_pointer  # noqa: E402
from mock_api.artifact_meta import hash_stream  # noqa: E402

PROVIDER = "skillcorner"
Digest = dict  # {"sha256": str, "size": int}

# The ten re-synced public opendata ids (spec §6.5). With NEW_MATCH_IDS (the ten byte-equal ids,
# verify_skillcorner_opendata_load) they are the twenty public opendata entries the listing must still
# return. Bare public MIT ids (precedent: verify_skillcorner_opendata_load.py); no id->entity tuple.
_RESYNCED_IDS: tuple[str, ...] = (
    "1886347",
    "1899585",
    "1925299",
    "1953632",
    "1996435",
    "2006229",
    "2011166",
    "2013725",
    "2015213",
    "2017461",
)
EXPECTED_PUBLIC_IDS: tuple[str, ...] = tuple(sorted((*NEW_MATCH_IDS, *_RESYNCED_IDS), key=int))


def check_listing(matches: list[dict], expected_ids: tuple[str, ...]) -> list[str]:
    """Problems with the public listing: a wrong entry count or a missing expected id (empty == ok).

    An entry that silently drops out of the index passes every artifact check (there is nothing left
    to check), so this count + id-presence gate is the only guard against a drop-out (spec §6.6 item 1).
    """
    problems: list[str] = []
    present = {str(m.get("id")) for m in matches}
    if len(matches) != len(expected_ids):
        problems.append(f"public /{PROVIDER}/matches: expected {len(expected_ids)} entries, got {len(matches)}")
    problems.extend(f"expected public id {mid} missing from listing" for mid in expected_ids if mid not in present)
    return problems


def check_revision_stamped(matches: list[dict], commit: str) -> list[str]:
    """Problems when a public opendata entry does not carry source.revision == commit (empty == ok)."""
    problems: list[str] = []
    for entry in matches:
        if entry.get("visibility") != "public":
            continue
        revision = (entry.get("source") or {}).get("revision")
        if revision != commit:
            problems.append(f"{entry.get('id')}: source.revision is {revision!r}, expected {commit!r}")
    return problems


def check_artifact_identity(
    entry: dict,
    stored_digest: Callable[[str], Digest],
    pin_digest: Callable[[str], Digest],
) -> list[str]:
    """Problems between stored bytes, the pin and artifact_meta for one entry's artifacts (empty == ok).

    For each artifact key, the stored object's sha256 and size must equal both the pinned upstream
    file (``pin_digest``) and the entry's ``artifact_meta`` value.
    """
    problems: list[str] = []
    mid = entry.get("id")
    meta = entry.get("artifact_meta") or {}
    for key in sorted(entry.get("artifacts", {})):
        stored = stored_digest(key)
        pin = pin_digest(key)
        if (stored["sha256"], stored["size"]) != (pin["sha256"], pin["size"]):
            problems.append(f"{mid} {key}: stored {stored} != pin {pin}")
        recorded = meta.get(key)
        if recorded is None:
            problems.append(f"{mid} {key}: no artifact_meta entry")
        elif (stored["sha256"], stored["size"]) != (recorded.get("sha256"), recorded.get("size")):
            problems.append(f"{mid} {key}: stored {stored} != artifact_meta {recorded}")
    return problems


def pin_digest_for(mid: str) -> Callable[[str], Digest]:
    """A pin-digest callable: upstream file identity at the pin. tracking uses the LFS pointer oid."""
    roles = {f"{mid}_{role}": rel for role, rel in opendata_files(mid).items()}

    def digest(key: str) -> Digest:
        raw = fetch_raw(roles[key])
        if is_lfs_pointer(raw):
            oid, size = parse_lfs_pointer(raw)
            return {"sha256": oid, "size": size}
        return {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}

    return digest


def _presigned_location(api: str, token: str, opener, mid: str, key: str) -> str | None:
    """The presigned S3 URL the API redirects an artifact GET to, or None when the body streams (200)."""
    req = urllib.request.Request(f"{api}/{PROVIDER}/matches/{mid}/{key}", headers={"Authorization": f"Bearer {token}"})
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.headers.get("Location")  # 200 -> None (body streamed below)
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            return exc.headers.get("Location")
        raise


def stored_digest_for(api: str, token: str, opener) -> Callable[[str, str], Digest]:
    """A stored-digest callable factory: download the live object and hash it (streamed)."""

    def digest(mid: str, key: str) -> Digest:
        location = _presigned_location(api, token, opener, mid, key)
        if location is None:  # 200: stream the body straight off the API response
            req = urllib.request.Request(
                f"{api}/{PROVIDER}/matches/{mid}/{key}", headers={"Authorization": f"Bearer {token}"}
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                d = hash_stream(resp)
        else:
            with urllib.request.urlopen(location, timeout=300) as resp:
                d = hash_stream(resp)
        return {"sha256": d.sha256, "size": d.size}

    return digest


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify the public SkillCorner opendata re-sync against the pin")
    ap.add_argument("--api", default=os.environ.get("PINING_API"), help="API base URL (or $PINING_API)")
    ap.add_argument("--public-token", default=os.environ.get("PINING_PUBLIC_TOKEN"), help="public bearer token")
    args = ap.parse_args()
    if not args.api or not args.public_token:
        ap.error("--api and --public-token required (or set $PINING_API / $PINING_PUBLIC_TOKEN)")

    token = args.public_token
    opener = urllib.request.build_opener(NoFollow)
    stored_for = stored_digest_for(args.api, token, opener)

    matches = get_json(args.api, f"/{PROVIDER}/matches", token).get("matches", [])
    public = [m for m in matches if m.get("visibility") == "public"]

    problems: list[str] = check_listing(public, EXPECTED_PUBLIC_IDS)
    problems += check_revision_stamped(public, UPSTREAM_COMMIT)
    for entry in public:
        mid = str(entry["id"])
        problems += check_artifact_identity(
            entry,
            stored_digest=lambda key, _mid=mid: stored_for(_mid, key),
            pin_digest=pin_digest_for(mid),
        )

    if problems:
        print(f"FAIL — {len(problems)} problem(s):")
        for p in problems:
            print(f"  {p}")
        return 1
    print(f"OK — {len(public)} public opendata entries carry the pin; all {len(public) * 4} artifacts match.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
