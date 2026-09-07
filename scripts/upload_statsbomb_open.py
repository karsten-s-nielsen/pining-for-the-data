"""Upload StatsBomb OPEN-DATA tournament 360 to the mock provider API (OWNER tier).

Owner-tier (visibility=private, provenance=redistributed) ingest of StatsBomb's public
open-data corpus for the six complete-tournament 360 competitions. The open-data licence
forbids third-party redistribution; owner-tier single-user hosting is the compliant use.
See docs/superpowers/specs/2026-09-06-statsbomb-open-tournaments-360-owner-tier-design.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import boto3
from pydantic import ValidationError

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from canonical.models import PlayerRecord  # noqa: E402
from formats.statsbomb import (  # noqa: E402
    MatchInfo,
    assert_delivery_coherent,
    match_info,
    players_from_lineups,
)
from formats.statsbomb_open import (  # noqa: E402
    OPEN_ARTIFACT_SPECS,
    TOURNAMENTS,
    build_metadata_open,
    partition_new_players,
    select_ingestible_matches,
)
from mock_api.staging import stage_artifacts  # noqa: E402
from mock_api.upload import upload_game  # noqa: E402
from mock_api.upload_players import upload_players  # noqa: E402

PROVIDER = "statsbomb"
SOURCE_NAME = "StatsBomb"
SOURCE_LICENCE = "StatsBomb Public Data User Agreement (open data); redistribution to third parties not permitted"
BASE_URL = "https://raw.githubusercontent.com/statsbomb/open-data/master/data/"
_VALIDATION_PROBE_TIMESTAMP = "1970-01-01T00:00:00Z"


def fetch_json(rel_path: str, cache_dir: Path, source_dir: Path | None) -> Any:
    """Resolve one open-data file: local clone -> cache -> HTTP GET (cached on the way)."""
    if source_dir is not None:
        local = source_dir / rel_path
        if local.is_file():
            return json.loads(local.read_text(encoding="utf-8"))
    cached = cache_dir / rel_path
    if cached.is_file():
        return json.loads(cached.read_text(encoding="utf-8"))
    url = BASE_URL + rel_path
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            raw = resp.read()
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"failed to fetch {url}: {e}") from e
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(raw)
    return json.loads(raw.decode("utf-8"))


def assemble_bundle(events: list, frames: list, lineups: list, dest: Path) -> None:
    """Write the three passthrough source files under the OPEN_ARTIFACT_SPECS names."""
    (dest / "events.json").write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    (dest / "three-sixty.json").write_text(json.dumps(frames, ensure_ascii=False), encoding="utf-8")
    (dest / "lineups.json").write_text(json.dumps(lineups, ensure_ascii=False), encoding="utf-8")


def validate_open_match(match: dict, events: list, frames: list, lineups: list) -> tuple[dict, MatchInfo, list[dict]]:
    """All fallible, S3-free per-match validation. Raises on a defective match; returns
    (metadata, match_info, player records). No mutation, no I/O."""
    assert_delivery_coherent(events, frames, lineups)
    metadata = build_metadata_open(match)
    info = match_info(metadata)
    players = players_from_lineups(lineups)
    for record in players:
        PlayerRecord.model_validate({**record, "visibility": "private", "updated_at": _VALIDATION_PROBE_TIMESTAMP})
    return metadata, info, players


def _stage_and_upload_open_match(
    metadata: dict,
    info: MatchInfo,
    players: list[dict],
    events: list,
    frames: list,
    lineups: list,
    *,
    bucket: str,
    known_ids: set[str],
    dry_run: bool,
) -> tuple[str, int, int]:
    """Partition players, then (unless dry_run) stage + upload. Assumes VALIDATED inputs —
    the only thing that raises here is the S3 layer, so run() calls this OUTSIDE its
    defect-catch and an upload failure propagates rather than being mis-reported as a defect."""
    new_players, skipped = partition_new_players(players, known_ids)
    known_ids.update(p["id"] for p in new_players)
    if dry_run:
        return info.match_id, len(new_players), len(skipped)
    with tempfile.TemporaryDirectory(prefix="sb-open-") as tmp:
        bundle_dir = Path(tmp) / "bundle"
        staging = Path(tmp) / "stage"
        bundle_dir.mkdir()
        staging.mkdir()
        assemble_bundle(events, frames, lineups, bundle_dir)
        stage_artifacts(bundle_dir, staging, OPEN_ARTIFACT_SPECS, metadata)
        upload_game(
            game_dir=staging,
            provider=PROVIDER,
            game_id=info.match_id,
            bucket=bucket,
            visibility="private",
            provenance="redistributed",
            date=info.date,
            home=info.home,
            away=info.away,
            source_name=SOURCE_NAME,
            source_licence=SOURCE_LICENCE,
        )
    if new_players:
        with tempfile.TemporaryDirectory(prefix="sb-open-players-") as tmp:
            players_file = Path(tmp) / "players.json"
            players_file.write_text(json.dumps({"players": new_players}, ensure_ascii=False), encoding="utf-8")
            upload_players(
                input_file=players_file,
                provider=PROVIDER,
                bucket=bucket,
                visibility="private",
                source_name=SOURCE_NAME,
                source_licence=SOURCE_LICENCE,
            )
    return info.match_id, len(new_players), len(skipped)


def upload_open_match(
    match: dict,
    events: list,
    frames: list,
    lineups: list,
    *,
    bucket: str,
    known_ids: set[str],
    dry_run: bool,
) -> tuple[str, int, int]:
    """Validate then stage+upload one open match. run() calls the two phases separately so an
    upload failure is never mis-classified as a defective match; this composed form is kept
    for direct callers/tests."""
    metadata, info, players = validate_open_match(match, events, frames, lineups)
    return _stage_and_upload_open_match(
        metadata,
        info,
        players,
        events,
        frames,
        lineups,
        bucket=bucket,
        known_ids=known_ids,
        dry_run=dry_run,
    )


def load_known_player_ids(bucket: str) -> set[str]:
    """Ids already in statsbomb/_private/players.json (empty if the index is absent)."""
    s3 = boto3.client("s3")
    try:
        obj = s3.get_object(Bucket=bucket, Key=f"{PROVIDER}/_private/players.json")
    except s3.exceptions.NoSuchKey:
        return set()
    return {p["id"] for p in json.loads(obj["Body"].read().decode("utf-8")).get("players", [])}


def run(tournaments, *, bucket: str, cache_dir: Path, source_dir: Path | None, dry_run: bool) -> list[tuple[str, str]]:
    """Ingest each tournament's 360-available matches, SKIPPING any defective match
    (an unparseable / missing / incoherent upstream artifact) with a reported reason
    rather than aborting the whole run. Every per-match check runs before that match's
    first upload, so a skip never leaves a partial load. Returns the (match_id, reason)
    list of skipped matches. See spec §6.4 / ADR 0012.
    """
    known_ids: set[str] = set() if dry_run else load_known_player_ids(bucket)
    total = new_total = skip_total = 0
    defective: list[tuple[str, str]] = []
    for cid, sid in tournaments:
        season = fetch_json(f"matches/{cid}/{sid}.json", cache_dir, source_dir)
        matches = select_ingestible_matches(season, cid)
        print(f"competition {cid} season {sid}: {len(matches)} match(es) with 360")
        for match in matches:
            mid = str(match["match_id"])
            try:
                events = fetch_json(f"events/{mid}.json", cache_dir, source_dir)
                frames = fetch_json(f"three-sixty/{mid}.json", cache_dir, source_dir)
                lineups = fetch_json(f"lineups/{mid}.json", cache_dir, source_dir)
                metadata, info, players = validate_open_match(match, events, frames, lineups)
            except (json.JSONDecodeError, RuntimeError, ValueError, ValidationError) as e:
                # Defective upstream artifact — reported, never silently dropped. Only the
                # S3-free validation is inside this catch, so an upload failure (below) can never
                # be mis-reported as a defect, and a skip never leaves a partial load.
                defective.append((mid, f"{type(e).__name__}: {str(e)[:140]}"))
                continue
            _mid, n_new, n_skip = _stage_and_upload_open_match(
                metadata,
                info,
                players,
                events,
                frames,
                lineups,
                bucket=bucket,
                known_ids=known_ids,
                dry_run=dry_run,
            )
            total += 1
            new_total += n_new
            skip_total += n_skip
    verb = "validated" if dry_run else "uploaded"
    est = " (estimate — run-relative)" if dry_run else ""
    print(f"\n{verb} {total} match(es); {new_total} new player(s){est}, {skip_total} skipped (already known).")
    if defective:
        print(f"\nSKIPPED {len(defective)} defective match(es) (unparseable/missing/incoherent upstream artifact):")
        for mid, reason in defective:
            print(f"  match {mid}: {reason}")
    return defective


def _parse_pairs(values: list[str] | None):
    if not values:
        return TOURNAMENTS
    pairs = []
    for v in values:
        cid, sid = v.split(":")
        pairs.append((int(cid), int(sid)))
    return tuple(pairs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload StatsBomb open-data tournament 360 (owner tier)")
    parser.add_argument("--bucket", default=os.environ.get("PINING_BUCKET"))
    # Default cache lives OUTSIDE the repo tree — the fetched raw JSON is licensed
    # StatsBomb data that must never be committed (spec §1.1). $SB_OPEN_CACHE overrides.
    default_cache = (
        Path(os.environ["SB_OPEN_CACHE"])
        if os.environ.get("SB_OPEN_CACHE")
        else Path.home() / ".cache" / "pining-sb-open"
    )
    parser.add_argument("--cache-dir", type=Path, default=default_cache)
    parser.add_argument("--source-dir", type=Path, default=None, help="Local statsbomb/open-data clone (skips network)")
    parser.add_argument(
        "--competition-season",
        action="append",
        metavar="CID:SID",
        help="Override the built-in six tournaments (repeatable)",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Fetch + validate, no S3 writes")
    group.add_argument("--execute", action="store_true", help="Upload to S3")
    args = parser.parse_args()
    if not args.bucket and args.execute:
        parser.error("--bucket is required with --execute (or set PINING_BUCKET)")
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    run(
        _parse_pairs(args.competition_season),
        bucket=args.bucket,
        cache_dir=args.cache_dir,
        source_dir=args.source_dir,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
