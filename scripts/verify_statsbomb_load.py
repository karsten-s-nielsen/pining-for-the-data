"""Post-load verification for the restricted StatsBomb commercial 360 owner-tier dataset.

Asserts (sampling ids from the live OWNER response — no licensed ids hardcoded):
  - owner /statsbomb/matches contains private (restricted) entries
  - those private ids are ABSENT from the public /statsbomb/matches list
  - every entry carries a date (a dateless entry is invisible to dateFrom/dateTo)
  - the artifact key set is exactly the role vocabulary for this provider
  - owner can fetch each artifact (large ones via a Range GET); public gets 404
  - the metadata artifact is a single object in feed shape
  - owner /statsbomb/players is non-empty and every record is private

Mirrors scripts/verify_skillcorner_realmadrid_load.py — including the shared
NoFollow (scripts/_verify_http.py), which exists because urllib would otherwise
forward the bearer token to presigned S3.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# scripts/ is not a package — make the sibling helper module importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _verify_http import NoFollow, get_json, parse_content_range_total

PROVIDER = "statsbomb"
EXPECTED_ARTIFACTS = {"events", "freeze_frames", "roster", "metadata"}
# Multi-megabyte bodies — validate via Range GET, no full download.
LARGE_ARTIFACTS = {"events", "freeze_frames"}


def _status_or_presigned(api: str, path: str, token: str) -> tuple[int, str | None]:
    """Return (status, location). For 302 returns the presigned URL; otherwise None."""
    req = urllib.request.Request(f"{api}{path}", headers={"Authorization": f"Bearer {token}"})
    opener = urllib.request.build_opener(NoFollow)
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.status, None
    except urllib.error.HTTPError as e:
        if e.code == 302:
            return 302, e.headers.get("Location")
        return e.code, None


def _fetch_presigned(location: str, large: bool) -> tuple[int, int, bytes | None]:
    """Fetch the presigned URL with a CLEAN request — no Authorization header.

    Returns (status, total_bytes, body). `body` is None for a Range GET, where only
    the size is known; small artifacts return their body so a caller that needs to
    inspect the content does not have to fetch it twice.
    """
    if large:
        s3_req = urllib.request.Request(location, headers={"Range": "bytes=0-0"})
        with urllib.request.urlopen(s3_req, timeout=60) as resp:
            return resp.status, parse_content_range_total(resp.headers.get("Content-Range", "")), None
    with urllib.request.urlopen(urllib.request.Request(location), timeout=60) as resp:
        body = resp.read()
        return resp.status, len(body), body


def _nested_field_present(md: dict, section: str, field: str, failures: list[str]) -> bool:
    """True if `md[section]` is an object carrying `field`; otherwise record why.

    The isinstance check is the load-bearing part. The obvious spelling —
    `field not in md.get(section, {})` — reaches into a value whose type the artifact
    controls, so a malformed feed gets two ways to defeat the check: a scalar section
    (`"competition": 7`) raises `TypeError`, and a string section silently degrades the
    membership test into a SUBSTRING match. Resolving the type first turns both into a
    recorded failure.

    Same class of defect as an uncaught decode, and the same rule applies: nothing in
    this function may raise, because an exception escaping the `failures` accumulator
    aborts the artifact loop before the remaining artifacts' public-token 404 leak
    checks run — a malformed artifact must not be able to suppress a licence-boundary
    assertion.
    """
    value = md.get(section)
    if not isinstance(value, dict):
        failures.append(
            f"metadata.{section} is {type(value).__name__}, expected a JSON object — not nested in feed shape"
        )
        return False
    if field not in value:
        failures.append(f"metadata.{section} lacks {field!r} — not nested in feed shape")
        return False
    return True


def _check_metadata_shape(body: bytes, failures: list[str]) -> None:
    """Assert the metadata artifact is a single object in feed shape.

    The decode is caught rather than allowed to propagate, for the reason given in
    `_nested_field_present`: every other check in this script appends to `failures` and
    lets the loop continue, so an uncaught `JSONDecodeError` here would abort
    mid-artifact and take the remaining checks down with it.
    """
    try:
        md = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        failures.append(f"metadata artifact is not decodable JSON: {e}")
        return
    if not isinstance(md, dict):
        failures.append(f"metadata artifact is {type(md).__name__}, expected a JSON object")
        return
    # `and` short-circuits, preserving the original elif chain's first-failure-wins
    # reporting: one malformed artifact yields one failure line, not a cascade.
    if _nested_field_present(md, "competition", "competition_id", failures) and _nested_field_present(
        md, "home_team", "home_team_id", failures
    ):
        print("OK: metadata is a single object in feed shape")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the restricted StatsBomb owner-tier load")
    parser.add_argument("--api", required=True, help="API base URL (no trailing slash)")
    parser.add_argument("--owner-token", required=True)
    parser.add_argument("--public-token", required=True)
    args = parser.parse_args()

    failures: list[str] = []

    owner_matches = get_json(args.api, f"/{PROVIDER}/matches", args.owner_token).get("matches", [])
    public_matches = get_json(args.api, f"/{PROVIDER}/matches", args.public_token).get("matches", [])
    public_ids = {m["id"] for m in public_matches}

    restricted = [m for m in owner_matches if m.get("visibility") == "private"]
    if not restricted:
        failures.append(f"owner /{PROVIDER}/matches: no private (restricted) entries found")
    else:
        print(f"OK: owner sees {len(restricted)} restricted match(es)")

    leaked = [m["id"] for m in restricted if m["id"] in public_ids]
    if leaked:
        failures.append(f"restricted ids visible to public token: {leaked[:5]}")
    elif restricted:
        print("OK: restricted ids absent from public match list")

    undated = [m["id"] for m in restricted if not m.get("date")]
    if undated:
        failures.append(f"entries with no date (invisible to dateFrom/dateTo): {undated[:5]}")
    elif restricted:
        print("OK: every restricted entry carries a date")

    if restricted:
        sample = restricted[0]
        mid = sample["id"]

        artifacts = set(sample.get("artifacts", {}))
        if artifacts != EXPECTED_ARTIFACTS:
            failures.append(f"artifact keys {sorted(artifacts)} != {sorted(EXPECTED_ARTIFACTS)}")
        else:
            print(f"OK: artifact keys = {sorted(artifacts)}")

        for artifact in sorted(artifacts):
            o_status, location = _status_or_presigned(
                args.api, f"/{PROVIDER}/matches/{mid}/{artifact}", args.owner_token
            )
            if o_status == 302 and location:
                a_status, total, body = _fetch_presigned(location, artifact in LARGE_ARTIFACTS)
                if a_status in (200, 206) and total > 0:
                    print(f"OK: owner {artifact} -> {a_status}, {total}B")
                else:
                    failures.append(f"owner {mid}/{artifact}: status={a_status}, total={total}")

                if artifact == "metadata":
                    # A Range GET returns no body, so the feed-shape assertions below
                    # would be SILENTLY skipped if `metadata` ever joined LARGE_ARTIFACTS.
                    # Fail instead: an unrelated constant change must not be able to
                    # disable a check.
                    if body is None:
                        failures.append(
                            "metadata body absent — the feed-shape check needs a full GET "
                            "(is 'metadata' in LARGE_ARTIFACTS?)"
                        )
                    else:
                        _check_metadata_shape(body, failures)
            else:
                failures.append(f"owner {mid}/{artifact}: expected 302, got {o_status}")

            p_status, _ = _status_or_presigned(args.api, f"/{PROVIDER}/matches/{mid}/{artifact}", args.public_token)
            if p_status == 404:
                print(f"OK: public {artifact} -> 404 (no existence leak)")
            else:
                failures.append(f"public {mid}/{artifact}: expected 404, got {p_status}")

    owner_players = get_json(args.api, f"/{PROVIDER}/players", args.owner_token).get("players", [])
    if not owner_players:
        failures.append(f"owner /{PROVIDER}/players: empty (derived catalogue missing)")
    else:
        print(f"OK: owner /{PROVIDER}/players = {len(owner_players)}")
        non_private = [p["id"] for p in owner_players if p.get("visibility") != "private"]
        if non_private:
            failures.append(f"player(s) not private: {non_private[:5]}")
        else:
            print("OK: every player record is private")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nAll post-conditions pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
