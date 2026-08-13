"""Upload restricted commercial StatsBomb 360 data to the mock provider API (OWNER tier).

Owner-tier (visibility=private) ingest of a club file-drop delivery. NOT
redistributable — served only to the owner bearer token. See
docs/superpowers/specs/2026-08-12-statsbomb-commercial-360-owner-tier-design.md.

Source root is read from $STATSBOMB_RESTRICTED_DIR (an operator-local path that is
never committed).
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Make src/ importable when run directly from a checkout.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from canonical.models import PlayerRecord  # noqa: E402
from formats.statsbomb import (  # noqa: E402
    ARTIFACT_SPECS,
    STAGED_METADATA_FILENAME,
    Bundle,
    assert_delivery_coherent,
    build_metadata,
    match_info,
    players_from_lineups,
    read_bundle,
    resolve_team_ids,
    team_gender,
)
from mock_api.upload import upload_game  # noqa: E402
from mock_api.upload_players import upload_players  # noqa: E402

PROVIDER = "statsbomb"
SOURCE_NAME = "StatsBomb"
SOURCE_LICENCE = "Restricted; redistribution not permitted"

# Probe value only — upload_players stamps the real timestamp on write.
_VALIDATION_PROBE_TIMESTAMP = "1970-01-01T00:00:00Z"


def _gzip_file(src: Path, dest: Path) -> None:
    """Stream-gzip src -> dest in 1 MiB chunks (never loads the body into memory)."""
    with src.open("rb") as f_in, gzip.open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out, length=1 << 20)


def _validate_players(players: list[dict]) -> None:
    """Validate every record BEFORE upload_game writes anything.

    upload_players validates too, but it runs AFTER upload_game has written the
    artifacts, matches.json and providers.json — a failure there would leave an
    indexed match with no player catalogue.
    """
    for record in players:
        PlayerRecord.model_validate({**record, "visibility": "private", "updated_at": _VALIDATION_PROBE_TIMESTAMP})


def stage_bundle(bundle: Bundle, staging: Path, metadata: dict) -> None:
    """Stage the four role-aligned artifacts.

    Compression rule (spec §3): gzip the multi-megabyte bodies (events, freeze
    frames), stage the kilobyte ones plain. `metadata` is passed in already built —
    every fallible step runs before staging opens.
    """
    for _role, source_name, staged_name in ARTIFACT_SPECS:
        src = bundle.root / source_name
        dest = staging / staged_name
        if staged_name.endswith(".gz"):
            _gzip_file(src, dest)
        else:
            shutil.copyfile(src, dest)

    (staging / STAGED_METADATA_FILENAME).write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def upload_bundle(root: Path, bucket: str) -> tuple[str, int]:
    """Pre-flight, stage and upload one delivery. Returns (match_id, players_uploaded).

    Ordering is load-bearing: every check that can fail runs before the first byte
    is staged and before upload_game writes any index (spec §7.1).
    """
    bundle = read_bundle(root)
    assert_delivery_coherent(bundle.events, bundle.frames, bundle.lineups)

    home_id, away_id = resolve_team_ids(bundle.events, bundle.match)
    metadata = build_metadata(
        bundle.match,
        bundle.competition,
        home_id,
        away_id,
        team_gender(bundle.lineups, home_id),
        team_gender(bundle.lineups, away_id),
    )
    info = match_info(metadata)
    players = players_from_lineups(bundle.lineups)
    _validate_players(players)

    with tempfile.TemporaryDirectory(prefix="sb-club-") as tmp:
        staging = Path(tmp)
        stage_bundle(bundle, staging, metadata)
        upload_game(
            game_dir=staging,
            provider=PROVIDER,
            game_id=info.match_id,
            bucket=bucket,
            visibility="private",
            provenance="original",
            date=info.date,
            home=info.home,
            away=info.away,
            source_name=SOURCE_NAME,
            source_licence=SOURCE_LICENCE,
        )

    if players:
        with tempfile.TemporaryDirectory(prefix="sb-club-players-") as tmp:
            players_file = Path(tmp) / "players.json"
            players_file.write_text(json.dumps({"players": players}, indent=2, ensure_ascii=False), encoding="utf-8")
            upload_players(
                input_file=players_file,
                provider=PROVIDER,
                bucket=bucket,
                visibility="private",
                source_name=SOURCE_NAME,
                source_licence=SOURCE_LICENCE,
            )

    return info.match_id, len(players)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload restricted commercial StatsBomb 360 data to the mock provider API (owner tier)"
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("PINING_BUCKET"),
        help="S3 bucket name (default: $PINING_BUCKET env var)",
    )
    parser.add_argument(
        "--source-dir",
        default=os.environ.get("STATSBOMB_RESTRICTED_DIR"),
        help="Bundle root containing events.json, frames.json, lineups.json, matches.json, "
        "competitions.json (default: $STATSBOMB_RESTRICTED_DIR env var)",
    )
    args = parser.parse_args()

    if not args.bucket:
        parser.error("--bucket is required (or set PINING_BUCKET)")
    if not args.source_dir:
        parser.error("--source-dir is required (or set STATSBOMB_RESTRICTED_DIR)")

    root = Path(args.source_dir)
    if not root.is_dir():
        parser.error(f"not a directory: {root}")

    print(f"Uploading restricted StatsBomb data to s3://{args.bucket}/{PROVIDER}/ (OWNER tier)")
    match_id, n_players = upload_bundle(root, args.bucket)
    print(f"Done — match {match_id}, {n_players} player(s) uploaded.")


if __name__ == "__main__":
    main()
