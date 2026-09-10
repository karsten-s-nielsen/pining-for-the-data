"""Post-load verification for the public SkillCorner A-League (opendata) dataset.

Asserts (PUBLIC tier — open MIT data, served to the public token):
  - public /skillcorner/matches returns EXPECTED_TOTAL entries and includes every NEW id
  - public /providers includes 'skillcorner'
  - public /skillcorner/players returns a non-empty catalogue
  - a sampled new match: the entry is public / provenance=redistributed / no format_version, lists
    exactly its four id-prefixed artifacts, and each is served (302) — checked without downloading
    the GB-scale bodies.

The pure checks (verify_listing / check_public_match / opendata_artifact_keys) are unit-tested with
injected callables; main wires them to the live API. The 10 new ids are public MIT opendata ids
(no licensed id->entity tuple is committed).
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
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from _verify_http import NoFollow, get_json  # noqa: E402

from formats.skillcorner_opendata import OPENDATA_SUFFIXES  # noqa: E402

PROVIDER = "skillcorner"
DEFAULT_EXPECTED_TOTAL = 20  # 10 original + 10 new A-League Men 2024/25 matches
NEW_MATCH_IDS: tuple[str, ...] = (
    "1874553",
    "1927964",
    "1959846",
    "1986691",
    "1996436",
    "2006363",
    "2007448",
    "2007721",
    "2010085",
    "2016236",
)


def opendata_artifact_keys(match_id: str) -> set[str]:
    """The four id-prefixed artifact keys upload_game derives for one opendata match."""
    return {f"{match_id}{suffix.split('.', 1)[0]}" for _role, suffix in OPENDATA_SUFFIXES}


def verify_listing(matches: list[dict], expected_total: int, required_ids: tuple[str, ...]) -> list[str]:
    """Problems with the public match listing (empty == ok)."""
    problems: list[str] = []
    if len(matches) != expected_total:
        problems.append(f"public /{PROVIDER}/matches: expected {expected_total}, got {len(matches)}")
    present = {str(m.get("id")) for m in matches}
    problems.extend(f"new match {mid} missing from public listing" for mid in required_ids if mid not in present)
    return problems


def check_public_match(entry: dict, artifact_status: Callable[[str], int]) -> list[str]:
    """Problems with one sampled public match entry (empty == ok).

    ``artifact_status(key)`` returns the HTTP status of fetching that artifact: present artifacts
    redirect (302) or stream (200); absent ones are 404.
    """
    problems: list[str] = []
    mid = str(entry.get("id"))
    if entry.get("visibility") != "public":
        problems.append(f"{mid}: visibility != public ({entry.get('visibility')!r})")
    if entry.get("provenance") != "redistributed":
        problems.append(f"{mid}: provenance != redistributed ({entry.get('provenance')!r})")
    if entry.get("format_version") is not None:
        problems.append(f"{mid}: format_version should be absent ({entry.get('format_version')!r})")
    keys = set(entry.get("artifacts", {}))
    expected = opendata_artifact_keys(mid)
    if keys != expected:
        problems.append(f"{mid}: artifacts {sorted(keys)} != expected {sorted(expected)}")
    for key in sorted(keys):
        status = artifact_status(key)
        if status not in (200, 302):
            problems.append(f"{mid}: artifact {key} fetch returned {status}")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify the public SkillCorner A-League (opendata) load")
    ap.add_argument("--api", default=os.environ.get("PINING_API"), help="API base URL (or $PINING_API)")
    ap.add_argument(
        "--public-token",
        default=os.environ.get("PINING_PUBLIC_TOKEN"),
        help="Public bearer token (or $PINING_PUBLIC_TOKEN)",
    )
    ap.add_argument("--expected-total", type=int, default=DEFAULT_EXPECTED_TOTAL)
    args = ap.parse_args()
    if not args.api or not args.public_token:
        ap.error("--api and --public-token required (or set $PINING_API / $PINING_PUBLIC_TOKEN)")

    token = args.public_token
    opener = urllib.request.build_opener(NoFollow)

    def artifact_status_for(match_id: str) -> Callable[[str], int]:
        def _status(key: str) -> int:
            req = urllib.request.Request(
                f"{args.api}/{PROVIDER}/matches/{match_id}/{key}",
                headers={"Authorization": f"Bearer {token}"},
            )
            try:
                return opener.open(req, timeout=30).status
            except urllib.error.HTTPError as exc:
                return exc.code

        return _status

    problems: list[str] = []

    matches = get_json(args.api, f"/{PROVIDER}/matches", token).get("matches", [])
    problems += verify_listing(matches, args.expected_total, NEW_MATCH_IDS)

    providers = get_json(args.api, "/providers", token).get("providers", [])
    if PROVIDER not in providers:
        problems.append(f"public /providers missing '{PROVIDER}'")

    players = get_json(args.api, f"/{PROVIDER}/players", token).get("players", [])
    if not players:
        problems.append(f"public /{PROVIDER}/players is empty")

    present = {str(m.get("id")): m for m in matches}
    sample_id = next((mid for mid in NEW_MATCH_IDS if mid in present), None)
    if sample_id is None:
        problems.append("no new match present to sample artifacts from")
    else:
        problems += check_public_match(present[sample_id], artifact_status_for(sample_id))

    if problems:
        print(f"FAIL — {len(problems)} problem(s):")
        for p in problems:
            print(f"  {p}")
        sys.exit(1)
    print(
        f"OK — public {PROVIDER}: {len(matches)} matches, {len(players)} players; "
        f"sample {sample_id} serves its 4 artifacts."
    )


if __name__ == "__main__":
    main()
