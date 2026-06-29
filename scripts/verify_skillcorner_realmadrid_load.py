"""Post-load verification for the restricted SkillCorner Real Madrid owner-tier dataset.

Asserts (sampling ids from the live OWNER response — no licensed ids hardcoded):
  - owner /skillcorner/matches contains private (restricted) entries
  - those private ids are ABSENT from the public /skillcorner/matches list
  - owner can fetch a restricted match's artifacts (tracking via a Range GET to
    avoid downloading the full body); public gets 404 on the same restricted id
  - owner /skillcorner/players is non-empty (derived catalogue present)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

# tracking is the large artifact (validate via Range GET, no full download).
LARGE_ARTIFACTS = {"tracking"}


def parse_content_range_total(header: str) -> int:
    m = re.search(r"/(\d+)\s*$", header or "")
    return int(m.group(1)) if m else -1


def _get_json(api: str, path: str, token: str) -> dict:
    req = urllib.request.Request(f"{api}{path}", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


class _NoFollow(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _status_or_presigned(api: str, path: str, token: str) -> tuple[int, str | None]:
    """Return (status, location). For 302 returns the presigned URL; otherwise location is None."""
    req = urllib.request.Request(f"{api}{path}", headers={"Authorization": f"Bearer {token}"})
    opener = urllib.request.build_opener(_NoFollow)
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.status, None
    except urllib.error.HTTPError as e:
        if e.code == 302:
            return 302, e.headers.get("Location")
        return e.code, None


def _artifact_ok_owner(location: str, large: bool) -> tuple[int, int]:
    if large:
        s3_req = urllib.request.Request(location, headers={"Range": "bytes=0-0"})
        with urllib.request.urlopen(s3_req, timeout=60) as resp:
            return resp.status, parse_content_range_total(resp.headers.get("Content-Range", ""))
    with urllib.request.urlopen(urllib.request.Request(location), timeout=60) as resp:
        return resp.status, len(resp.read())


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the restricted SkillCorner RM owner-tier load")
    parser.add_argument("--api", required=True, help="API base URL (no trailing slash)")
    parser.add_argument("--owner-token", required=True)
    parser.add_argument("--public-token", required=True)
    args = parser.parse_args()

    failures: list[str] = []

    owner_matches = _get_json(args.api, "/skillcorner/matches", args.owner_token).get("matches", [])
    public_matches = _get_json(args.api, "/skillcorner/matches", args.public_token).get("matches", [])
    public_ids = {m["id"] for m in public_matches}

    restricted = [m for m in owner_matches if m.get("visibility") == "private"]
    if not restricted:
        failures.append("owner /skillcorner/matches: no private (restricted) entries found")
    else:
        print(f"OK: owner sees {len(restricted)} restricted match(es)")

    leaked = [m["id"] for m in restricted if m["id"] in public_ids]
    if leaked:
        failures.append(f"restricted ids visible to public token: {leaked[:5]}")
    elif restricted:
        print("OK: restricted ids absent from public match list")

    if restricted:
        sample = restricted[0]
        mid = sample["id"]
        for artifact in sample.get("artifacts", {}):
            # Owner must get the artifact (200/206); public must get 404.
            o_status, location = _status_or_presigned(
                args.api, f"/skillcorner/matches/{mid}/{artifact}", args.owner_token
            )
            if o_status == 302 and location:
                a_status, total = _artifact_ok_owner(location, artifact in LARGE_ARTIFACTS)
                if a_status in (200, 206) and total > 0:
                    print(f"OK: owner {artifact} -> {a_status}, {total}B")
                else:
                    failures.append(f"owner {mid}/{artifact}: status={a_status}, total={total}")
            else:
                failures.append(f"owner {mid}/{artifact}: expected 302, got {o_status}")

            p_status, _ = _status_or_presigned(args.api, f"/skillcorner/matches/{mid}/{artifact}", args.public_token)
            if p_status == 404:
                print(f"OK: public {artifact} -> 404 (no existence leak)")
            else:
                failures.append(f"public {mid}/{artifact}: expected 404, got {p_status}")

    owner_players = _get_json(args.api, "/skillcorner/players", args.owner_token).get("players", [])
    if owner_players:
        print(f"OK: owner /skillcorner/players = {len(owner_players)}")
    else:
        failures.append("owner /skillcorner/players: empty (derived catalogue missing)")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nAll post-conditions pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
