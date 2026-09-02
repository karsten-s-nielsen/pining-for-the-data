"""Post-run verification for the canonical owner-tier SkillCorner load.

For each owner-tier match, confirm the canonical shape against the live API: tracking artifact
is ``.parquet``, ``freeze_frames`` is gone (404), the entry carries ``format_version: 2``, and
the required artifacts fetch. The pure check (``check_match_canonical``) is unit-tested with an
injected artifact-status callable; ``main`` wires it to the live API. Sample-from-live-response
only — no licensed ids are committed.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from _verify_http import NoFollow, get_json  # noqa: E402

PROVIDER = "skillcorner"
_REQUIRED = ("tracking", "events", "metadata")


def check_match_canonical(entry: dict, artifact_status: Callable[[str], int]) -> list[str]:
    """Return a list of problems (empty == canonical) for one match entry.

    ``artifact_status(role)`` returns the HTTP status of fetching that artifact: a present
    artifact redirects (302) or streams (200); an absent one is 404.
    """
    problems: list[str] = []
    mid = entry.get("id")
    arts = entry.get("artifacts", {})

    if entry.get("format_version") != 2:
        problems.append(f"{mid}: format_version != 2 ({entry.get('format_version')!r})")
    if not str(arts.get("tracking", "")).endswith(".parquet"):
        problems.append(f"{mid}: tracking artifact is not .parquet ({arts.get('tracking')!r})")
    if "freeze_frames" in arts:
        problems.append(f"{mid}: freeze_frames still listed in artifacts")
    if artifact_status("freeze_frames") != 404:
        problems.append(f"{mid}: freeze_frames does not 404 (should be gone)")
    for role in _REQUIRED:
        status = artifact_status(role)
        if status not in (200, 302):
            problems.append(f"{mid}: {role} fetch returned {status}")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify the canonical owner-tier SkillCorner load")
    ap.add_argument("--api", default=os.environ.get("PINING_API"), help="API base URL (or $PINING_API)")
    ap.add_argument(
        "--owner-token",
        default=os.environ.get("PINING_FOR_THE_DATA_TOKEN"),
        help="Owner bearer token (or $PINING_FOR_THE_DATA_TOKEN)",
    )
    args = ap.parse_args()
    if not args.api or not args.owner_token:
        ap.error("--api and --owner-token required (or set $PINING_API / $PINING_FOR_THE_DATA_TOKEN)")

    opener = urllib.request.build_opener(NoFollow)

    def artifact_status_for(match_id: str) -> Callable[[str], int]:
        def _status(role: str) -> int:
            req = urllib.request.Request(
                f"{args.api}/{PROVIDER}/matches/{match_id}/{role}",
                headers={"Authorization": f"Bearer {args.owner_token}"},
            )
            try:
                return opener.open(req, timeout=30).status
            except urllib.error.HTTPError as exc:
                return exc.code

        return _status

    listing = get_json(args.api, f"/{PROVIDER}/matches", args.owner_token)
    private = [m for m in listing.get("matches", []) if m.get("visibility") == "private"]
    all_problems: list[str] = []
    for entry in private:
        all_problems.extend(check_match_canonical(entry, artifact_status_for(entry["id"])))

    if all_problems:
        print(f"FAIL — {len(all_problems)} problem(s):")
        for p in all_problems:
            print(f"  {p}")
        sys.exit(1)
    print(f"OK — {len(private)} owner-tier match(es) canonical (tracking.parquet, freeze gone, format_version=2).")


if __name__ == "__main__":
    main()
