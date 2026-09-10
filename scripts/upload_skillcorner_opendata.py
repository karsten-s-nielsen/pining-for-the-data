"""Ingest the public SkillCorner Open Data into the PUBLIC tier of the mock provider API.

github.com/SkillCorner/opendata is MIT-licensed and publicly fetchable over anonymous HTTP (no
competition signup is needed at fetch time). Each match ships four artifacts, redistributed
AS-IS: ``{id}_match.json`` (V3 metadata), ``{id}_tracking_extrapolated.jsonl``,
``{id}_dynamic_events.csv`` and ``{id}_phases_of_play.csv``. This adapter uploads every opendata
match not already live to the public ``skillcorner`` provider (visibility="public",
provenance="redistributed", MIT), keeping byte- and artifact-key-parity with the original 10
A-League matches. It is idempotent: already-live ids are skipped, so a re-run only adds what is
new — and it is competition-agnostic, so a future opendata drop reuses it unchanged.

It also (re)builds the PUBLIC ``players.json`` across ALL opendata matches, skipping any player id
that also exists in the owner/private players index. Such a cross-tier collision is reported for a
human re-tiering decision — never silently promoted or dropped from the private tier — because
``upload_players`` refuses to re-tier and would otherwise abort the whole run.

Dry-run by default; pass ``--apply`` to write. The pure transforms live in
``formats.skillcorner_opendata`` and are unit-tested; ``main`` performs the HTTP/S3 I/O at the
gated ops step. See ``scripts/upload_skillcorner_raw.py`` (the owner-tier peer) for the same shape.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from formats.skillcorner_opendata import (  # noqa: E402
    discover_match_ids,
    match_info,
    opendata_files,
    select_new_matches,
)

PROVIDER = "skillcorner"
SOURCE_NAME = "SkillCorner Open Data"
SOURCE_URL = "https://github.com/SkillCorner/opendata"
SOURCE_LICENCE = "MIT"
RAW_BASE = "https://raw.githubusercontent.com/SkillCorner/opendata/master/data"


def http_get(rel: str) -> bytes:
    """Fetch a ``data/``-root-relative path from the opendata repo (anonymous)."""
    with urllib.request.urlopen(f"{RAW_BASE}/{rel}", timeout=180) as resp:
        return resp.read()


def stage_match(fetch: Callable[[str], bytes], staging_dir: Path, match_id: str) -> Path:
    """Fetch + stage the four opendata artifacts for one match; return the staging dir.

    The staged filename keeps the id-prefixed opendata basename so ``upload_game`` derives the
    legacy artifact keys. Raises ValueError if any artifact is empty (a defective delivery) so a
    bad match is never half-uploaded. ``fetch(rel)`` returns bytes for a data-root-relative path.
    """
    dest = Path(staging_dir) / match_id
    dest.mkdir(parents=True, exist_ok=True)
    for rel in opendata_files(match_id).values():
        name = rel.rsplit("/", 1)[-1]
        body = fetch(rel)
        if not body:
            raise ValueError(f"match {match_id}: artifact {name} is empty")
        (dest / name).write_bytes(body)
    return dest


def live_match_ids(s3, bucket: str) -> set[str]:
    """All match ids already present in the skillcorner matches index (any tier)."""
    key = f"{PROVIDER}/matches.json"
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except s3.exceptions.NoSuchKey:
        return set()
    data = json.loads(obj["Body"].read().decode("utf-8"))
    return {str(m["id"]) for m in data.get("matches", [])}


def private_player_ids(s3, bucket: str) -> set[str]:
    """Player ids already in the OWNER/private skillcorner players index.

    When uploading PUBLIC players, the private index is the "other tier" whose ids would make
    ``upload_players`` abort on a cross-tier collision; these are pre-skipped and reported.
    """
    key = f"{PROVIDER}/_private/players.json"
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except s3.exceptions.NoSuchKey:
        return set()
    data = json.loads(obj["Body"].read().decode("utf-8"))
    return {p.get("id") for p in data.get("players", []) if p.get("id") is not None}


def main() -> None:
    # Emit UTF-8 so logging a non-ASCII team/player name never crashes on a legacy console codepage.
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

    import boto3
    from upload_skillcorner_realmadrid import derive_players  # dedup + skip-ids (reused)

    from mock_api.upload import upload_game
    from mock_api.upload_players import upload_players

    ap = argparse.ArgumentParser(description="Ingest public SkillCorner Open Data into the public tier")
    ap.add_argument("--bucket", default=os.environ.get("PINING_BUCKET"))
    ap.add_argument("--limit", type=int, default=None, help="only consider the first N opendata matches")
    ap.add_argument("--apply", action="store_true", help="apply changes (default is dry-run)")
    args = ap.parse_args()
    if not args.bucket:
        ap.error("--bucket required (or set PINING_BUCKET)")

    s3 = boto3.client("s3")

    index = json.loads(http_get("matches.json"))
    all_ids = discover_match_ids(index)
    if args.limit:
        all_ids = all_ids[: args.limit]

    live = live_match_ids(s3, args.bucket)
    new_ids = select_new_matches(all_ids, live)

    # The public players catalogue spans ALL opendata matches (the original 10 have no players
    # index today), so fetch every match's small metadata — not just the new ones.
    metas: dict[str, dict] = {mid: json.loads(http_get(opendata_files(mid)["match"])) for mid in all_ids}

    print(
        f"opendata matches: {len(all_ids)}; already live: {len(set(all_ids) & live)}; "
        f"new to upload: {len(new_ids)} (apply={args.apply})"
    )
    for mid in new_ids:
        info = match_info(metas[mid])
        print(f"  {'UPLOAD' if args.apply else 'DRY-RUN'} {mid}: {info.home} v {info.away} ({info.date})")

    # Players across ALL opendata matches, skipping ids already in the private tier.
    skip_ids = private_player_ids(s3, args.bucket)
    players, skipped = derive_players(list(metas.values()), skip_ids)
    print(f"public players derivable: {len(players)}; skipped (already owner/private): {len(skipped)}")
    if skipped:
        print(f"  CROSS-TIER COLLISION (report to human — not promoted/dropped): {skipped}")

    if not args.apply:
        print("Dry-run only — no writes. Re-run with --apply to upload.")
        return

    uploaded = 0
    for mid in new_ids:
        info = match_info(metas[mid])
        with tempfile.TemporaryDirectory(prefix=f"sc-od-{mid}-") as tmp:
            staging = stage_match(http_get, Path(tmp), mid)
            upload_game(
                game_dir=staging,
                provider=PROVIDER,
                game_id=info.match_id,
                bucket=args.bucket,
                visibility="public",
                provenance="redistributed",
                date=info.date,
                home=info.home,
                away=info.away,
                source_name=SOURCE_NAME,
                source_url=SOURCE_URL,
                source_licence=SOURCE_LICENCE,
            )
        uploaded += 1

    if players:
        with tempfile.TemporaryDirectory(prefix="sc-od-players-") as tmp:
            players_file = Path(tmp) / "players.json"
            players_file.write_text(json.dumps({"players": players}, indent=2), encoding="utf-8")
            upload_players(
                input_file=players_file,
                provider=PROVIDER,
                bucket=args.bucket,
                visibility="public",
                source_name=SOURCE_NAME,
                source_url=SOURCE_URL,
                source_licence=SOURCE_LICENCE,
            )

    print(f"Done — {uploaded} match(es) uploaded, {len(players)} public player(s) written, {len(skipped)} skipped.")


if __name__ == "__main__":
    main()
