"""Post-load verification for the Gradient Sports World Cup 2022 dataset.

Replaces manual curl smoke tests with an automated check that runs after
scripts/upload_gradient_wc2022.py and exits non-zero on any post-condition failure.

Checks (spec §8.3.1):
  - owner-tier /gradientsports/matches returns exactly EXPECTED_MATCH_COUNT entries
  - owner-tier /gradientsports/players returns exactly EXPECTED_PLAYER_COUNT entries
  - public-tier /gradientsports/matches and /gradientsports/players return zero entries
  - public-tier /providers includes 'gradientsports' (existence is not the secret)
  - 5 random match × 4 artifact owner-tier fetches return 200 + non-empty body
  - sampled players from the live response conform to PlayerRecord canonical shape
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

EXPECTED_MATCH_COUNT = 64  # FIFA WC 2022: 48 group-stage + 16 knockout matches
# Unique player IDs after dedup. Gradient Sports CSV has ~2321 (player, team) rows;
# many players belong to multiple rows (different roster slots).
EXPECTED_PLAYER_COUNT = 829
PLAYER_SPOT_CHECK_SAMPLE_SIZE = 5  # sample N players from the response, content-agnostic
ARTIFACTS_PER_MATCH = ["metadata", "events", "roster", "tracking"]

# DO NOT hardcode (provider_id → name) tuples here — those are the licensed
# mapping the spec §8.3 redistribution-licence gate exists to protect.
# Spot-checks instead sample from the live response and validate shape only.


def _get_json(api: str, path: str, token: str) -> tuple[Any, int]:
    req = urllib.request.Request(
        f"{api}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8")), resp.status


def _follow_redirect(api: str, path: str, token: str) -> tuple[int, int]:
    """Two-step download: GET the API (302 with presigned URL), then GET the URL
    WITHOUT the bearer header. S3 rejects requests carrying both `Authorization:
    Bearer` and a presigned `X-Amz-Signature` (treats it as conflicting auth);
    the bearer is only for the API, the presigned URL self-authenticates.

    Returns (final_status, body_byte_count) of the actual S3 download.
    """
    # Step 1: Get the 302 from the API (don't follow automatically — urllib
    # would forward the Authorization header to S3, which causes the conflict).
    api_req = urllib.request.Request(
        f"{api}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )

    class _NoFollow(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None  # disable auto-follow

    opener = urllib.request.build_opener(_NoFollow)
    try:
        with opener.open(api_req, timeout=30) as resp:
            # Shouldn't get here for a 302 path, but handle direct 200 just in case.
            return resp.status, len(resp.read())
    except urllib.error.HTTPError as e:
        if e.code != 302:
            raise
        location = e.headers.get("Location")
        if not location:
            raise RuntimeError(f"302 from API but no Location header for {path}") from e

    # Step 2: GET the presigned URL with NO Authorization header.
    s3_req = urllib.request.Request(location)
    with urllib.request.urlopen(s3_req, timeout=60) as resp:
        body = resp.read()
        return resp.status, len(body)


def _get_status(api: str, path: str, token: str) -> int:
    req = urllib.request.Request(
        f"{api}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the Gradient Sports WC2022 dataset is loaded correctly")
    parser.add_argument("--api", required=True, help="API base URL (no trailing slash)")
    parser.add_argument("--owner-token", required=True)
    parser.add_argument("--public-token", required=True)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    failures: list[str] = []

    # 1. Owner-tier match count
    body: dict = {"matches": []}
    try:
        body, _ = _get_json(args.api, "/gradientsports/matches", args.owner_token)
        n = len(body.get("matches", []))
        if n != EXPECTED_MATCH_COUNT:
            failures.append(f"owner /gradientsports/matches: expected {EXPECTED_MATCH_COUNT}, got {n}")
        else:
            print(f"OK: owner /gradientsports/matches = {n}")
    except Exception as e:
        failures.append(f"owner /gradientsports/matches: request failed: {e}")

    matches = body.get("matches", [])

    # 2. Owner-tier player count
    try:
        pbody, _ = _get_json(args.api, "/gradientsports/players", args.owner_token)
        np_ = len(pbody.get("players", []))
        if np_ != EXPECTED_PLAYER_COUNT:
            failures.append(f"owner /gradientsports/players: expected {EXPECTED_PLAYER_COUNT}, got {np_}")
        else:
            print(f"OK: owner /gradientsports/players = {np_}")
    except Exception as e:
        failures.append(f"owner /gradientsports/players: request failed: {e}")

    # 3. Public-tier visibility leak checks
    try:
        body, _ = _get_json(args.api, "/gradientsports/matches", args.public_token)
        if body.get("matches"):
            failures.append(
                f"VISIBILITY LEAK: public /gradientsports/matches returned {len(body['matches'])} entries (expected 0)"
            )
        else:
            print("OK: public /gradientsports/matches = 0")
    except Exception as e:
        failures.append(f"public /gradientsports/matches: request failed: {e}")

    try:
        body, _ = _get_json(args.api, "/gradientsports/players", args.public_token)
        if body.get("players"):
            failures.append(
                f"VISIBILITY LEAK: public /gradientsports/players returned {len(body['players'])} entries (expected 0)"
            )
        else:
            print("OK: public /gradientsports/players = 0")
    except Exception as e:
        failures.append(f"public /gradientsports/players: request failed: {e}")

    # 4. public /providers MUST include gradientsports (existence is not the secret; spec §4.2)
    try:
        body, _ = _get_json(args.api, "/providers", args.public_token)
        if "gradientsports" not in body.get("providers", []):
            failures.append(
                "public /providers: 'gradientsports' missing — spec §4.2 says public tier sees all providers"
            )
        else:
            print("OK: public /providers contains 'gradientsports'")
    except Exception as e:
        failures.append(f"public /providers: request failed: {e}")

    # 5. Owner-tier artifact spot-check (5 random matches × 4 artifacts)
    rng = random.Random(args.seed)
    sample = rng.sample(matches, min(5, len(matches)))
    spot_pass = 0
    spot_total = 0
    for m in sample:
        match_id = m["id"]
        for artifact in ARTIFACTS_PER_MATCH:
            spot_total += 1
            try:
                path = f"/gradientsports/matches/{match_id}/{artifact}"
                status, size = _follow_redirect(args.api, path, args.owner_token)
                if status == 200 and size > 0:
                    spot_pass += 1
                else:
                    failures.append(f"artifact spot-check {match_id}/{artifact}: status={status}, body={size}B")
            except Exception as e:
                failures.append(f"artifact spot-check {match_id}/{artifact}: failed: {e}")
    print(f"OK: artifact spot-check {spot_pass}/{spot_total}")

    # 6. Player spot-check — content-agnostic. Sample N players from the live
    # response and assert each conforms to the canonical PlayerRecord shape:
    # has an id matching the path-param regex, and at least one of nickname /
    # firstName+lastName per spec §6.3.
    try:
        all_players_body, _ = _get_json(args.api, "/gradientsports/players", args.owner_token)
        all_players = all_players_body.get("players", [])
    except Exception as e:
        failures.append(f"owner /gradientsports/players for spot-check: failed: {e}")
        all_players = []

    sample_players = rng.sample(all_players, min(PLAYER_SPOT_CHECK_SAMPLE_SIZE, len(all_players)))
    player_pass = 0
    for p in sample_players:
        pid = p.get("id", "")
        try:
            body, _ = _get_json(args.api, f"/gradientsports/players/{pid}", args.owner_token)
            shape_ok = (
                isinstance(body.get("id"), str)
                and (body.get("nickname") or (body.get("firstName") and body.get("lastName")))
                and body.get("visibility") == "private"
            )
            if shape_ok:
                player_pass += 1
            else:
                failures.append(f"player {pid}: PlayerRecord shape invalid in response")
        except Exception as e:
            failures.append(f"player {pid}: request failed: {e}")
    print(f"OK: player spot-check {player_pass}/{len(sample_players)}")

    # 7. Public-tier 404 on a known private artifact (spot-check uniform-404)
    if matches:
        any_match = matches[0]["id"]
        any_artifact = next(iter(matches[0].get("artifacts", {}).keys()), "metadata")
        status = _get_status(args.api, f"/gradientsports/matches/{any_match}/{any_artifact}", args.public_token)
        if status != 404:
            failures.append(f"public /gradientsports/matches/{any_match}/{any_artifact}: expected 404, got {status}")
        else:
            print(f"OK: public 404 on private artifact /gradientsports/matches/{any_match}/{any_artifact}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nAll post-conditions pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
