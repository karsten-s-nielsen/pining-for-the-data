"""Post-load verification for the IDSSE Bundesliga public dataset.

Asserts (public tier — this is open redistributable data, visible to everyone):
  - public-tier /idsse/matches returns EXPECTED_MATCH_COUNT entries
  - public /providers includes 'idsse'
  - a dateFrom/dateTo query returns a non-empty, bounded subset
  - each sampled match serves all three artifacts: metadata fully (200 + non-empty),
    events/tracking via a Range GET (206 + positive total) to avoid downloading GBs
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

EXPECTED_MATCH_COUNT = 7
ARTIFACTS_PER_MATCH = ["metadata", "events", "tracking"]
LARGE_ARTIFACTS = {"events", "tracking"}


def parse_content_range_total(header: str) -> int:
    """Return the total size from a `Content-Range: bytes a-b/total` header, or -1."""
    m = re.search(r"/(\d+)\s*$", header or "")
    return int(m.group(1)) if m else -1


def _get_json(api: str, path: str, token: str) -> dict:
    req = urllib.request.Request(f"{api}{path}", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


class _NoFollow(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _presigned_url(api: str, path: str, token: str) -> str:
    """Return the presigned S3 URL from the API's 302 (without following it)."""
    req = urllib.request.Request(f"{api}{path}", headers={"Authorization": f"Bearer {token}"})
    opener = urllib.request.build_opener(_NoFollow)
    try:
        with opener.open(req, timeout=30) as resp:
            raise RuntimeError(f"expected 302 from {path}, got {resp.status}")
    except urllib.error.HTTPError as e:
        if e.code != 302:
            raise
        location = e.headers.get("Location")
        if not location:
            raise RuntimeError(f"302 from {path} but no Location header") from e
        return location


def check_artifact(api: str, path: str, token: str, *, large: bool) -> tuple[int, int]:
    """Return (status, byte_total). Large artifacts use a Range GET (no full download)."""
    location = _presigned_url(api, path, token)
    if large:
        # Range GET — a GET (NOT HEAD): the presigned URL is signed for GET, so HEAD
        # would fail signature validation. bytes=0-0 fetches a single byte; S3 returns
        # 206 with Content-Range: bytes 0-0/<total>.
        s3_req = urllib.request.Request(location, headers={"Range": "bytes=0-0"})
        with urllib.request.urlopen(s3_req, timeout=60) as resp:
            return resp.status, parse_content_range_total(resp.headers.get("Content-Range", ""))
    s3_req = urllib.request.Request(location)
    with urllib.request.urlopen(s3_req, timeout=60) as resp:
        return resp.status, len(resp.read())


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the IDSSE Bundesliga public dataset is loaded correctly")
    parser.add_argument("--api", required=True, help="API base URL (no trailing slash)")
    parser.add_argument("--public-token", required=True)
    args = parser.parse_args()

    failures: list[str] = []

    matches: list[dict] = []
    try:
        body = _get_json(args.api, "/idsse/matches", args.public_token)
        matches = body.get("matches", [])
        if len(matches) != EXPECTED_MATCH_COUNT:
            failures.append(f"public /idsse/matches: expected {EXPECTED_MATCH_COUNT}, got {len(matches)}")
        else:
            print(f"OK: public /idsse/matches = {len(matches)}")
    except Exception as e:
        failures.append(f"public /idsse/matches: request failed: {e}")

    try:
        providers = _get_json(args.api, "/providers", args.public_token).get("providers", [])
        if "idsse" not in providers:
            failures.append("public /providers: 'idsse' missing")
        else:
            print("OK: public /providers contains 'idsse'")
    except Exception as e:
        failures.append(f"public /providers: request failed: {e}")

    # Date filter: every match has a date; an open-ended dateFrom must return them all,
    # and an impossible window must return none.
    if matches:
        dates = sorted(m.get("date", "") for m in matches if m.get("date"))
        if dates:
            lo = dates[0]
            try:
                got = _get_json(args.api, f"/idsse/matches?dateFrom={lo}", args.public_token).get("matches", [])
                if len(got) != len(matches):
                    failures.append(f"dateFrom={lo}: expected {len(matches)}, got {len(got)}")
                else:
                    print(f"OK: dateFrom={lo} returns all {len(got)}")
                none = _get_json(args.api, "/idsse/matches?dateTo=1900-01-01", args.public_token).get("matches", [])
                if none:
                    failures.append(f"dateTo=1900-01-01: expected 0, got {len(none)}")
                else:
                    print("OK: dateTo=1900-01-01 returns 0")
            except Exception as e:
                failures.append(f"date-filter check failed: {e}")
        else:
            failures.append("no match has a 'date' field — date filtering would be broken")

    # Artifact checks on the first match (size-aware).
    if matches:
        mid = matches[0]["id"]
        for artifact in ARTIFACTS_PER_MATCH:
            path = f"/idsse/matches/{mid}/{artifact}"
            try:
                status, total = check_artifact(args.api, path, args.public_token, large=artifact in LARGE_ARTIFACTS)
                ok = (status in (200, 206)) and total > 0
                if ok:
                    print(f"OK: {path} -> {status}, {total}B")
                else:
                    failures.append(f"{path}: status={status}, total={total}")
            except Exception as e:
                failures.append(f"{path}: failed: {e}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nAll post-conditions pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
