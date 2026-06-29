"""Upload restricted SkillCorner Real Madrid data to the mock provider API (OWNER tier).

Owner-tier (visibility=private) ingest of restricted SkillCorner tracking data
(Soccermatics-course distribution). NOT redistributable — served only to the owner
bearer token. See
docs/superpowers/specs/2026-06-29-skillcorner-restricted-realmadrid-owner-tier-design.md.

Source root is read from $SKILLCORNER_RESTRICTED_DIR (an operator-local path that is
never committed). Reuses the existing `skillcorner` provider at the private tier.
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

import boto3  # noqa: E402

from formats.skillcorner_bundle import (  # noqa: E402
    ARTIFACT_SPECS,
    discover_matches,
    load_meta,
    match_info,
    missing_artifacts,
    players_from_meta,
)
from mock_api.upload import upload_game  # noqa: E402
from mock_api.upload_players import upload_players  # noqa: E402

PROVIDER = "skillcorner"
SOURCE_NAME = "SkillCorner"
SOURCE_LICENCE = "Restricted; redistribution not permitted"


def _gzip_file(src: Path, dest: Path) -> None:
    """Stream-gzip src -> dest in 1 MiB chunks (never loads the body into memory)."""
    with src.open("rb") as f_in, gzip.open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out, length=1 << 20)


def stage_match(root: Path, match_id: str, staging: Path) -> None:
    """Copy/gzip the 5 ingested artifacts for one match into `staging` under role-aligned names."""
    for _role, subdir, ext, staged in ARTIFACT_SPECS:
        src = root / subdir / f"{match_id}{ext}"
        dest = staging / staged
        if staged.endswith(".gz"):
            _gzip_file(src, dest)
        else:
            shutil.copyfile(src, dest)


def public_player_ids(s3, bucket: str) -> set[str]:
    """Return the player ids already present in the PUBLIC skillcorner players.json.

    These are the ids that would make `upload_players` raise (cross-tier guard:
    when uploading private, the "other tier" is the public index). Private-tier
    ids are NOT collected — re-uploading them updates in place, which is fine.
    """
    key = f"{PROVIDER}/players.json"
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except s3.exceptions.NoSuchKey:
        return set()
    data = json.loads(obj["Body"].read().decode("utf-8"))
    return {p.get("id") for p in data.get("players", []) if p.get("id") is not None}


def derive_players(metas: list[dict], skip_ids: set[str]) -> tuple[list[dict], list[str]]:
    """Derive deduped owner-tier player records from many meta dicts.

    Returns (kept, skipped_ids). Ids in `skip_ids` (already public) are dropped and
    reported rather than passed to upload_players, which would abort on the first
    cross-tier collision. The colliding player is already public; only the tracking
    is restricted, and the restricted matches still reference them by id.
    """
    by_id: dict[str, dict] = {}
    for meta in metas:
        for record in players_from_meta(meta):
            by_id[record["id"]] = record  # dedup by id; entries for the same id are identical
    kept = sorted((r for r in by_id.values() if r["id"] not in skip_ids), key=lambda r: r["id"])
    skipped = sorted(pid for pid in by_id if pid in skip_ids)
    return kept, skipped


def upload_all(root: Path, bucket: str, limit: int | None = None) -> tuple[int, int, int]:
    """Stage + upload all complete matches, then derive + upload the owner-tier players.

    Returns (matches_uploaded, players_uploaded, players_skipped).
    """
    match_ids = discover_matches(root)
    if limit:
        match_ids = match_ids[:limit]
    print(f"Found {len(match_ids)} match(es) under {root}")

    s3 = boto3.client("s3")
    metas: list[dict] = []
    uploaded = 0

    for match_id in match_ids:
        missing = missing_artifacts(root, match_id)
        if missing:
            print(f"WARN: match {match_id} missing {missing} — skipping")
            continue
        meta = load_meta(root / "meta" / f"{match_id}.json")
        try:
            info = match_info(meta)
        except ValueError as e:
            print(f"WARN: match {match_id} meta unparseable ({e}) — skipping")
            continue
        metas.append(meta)
        with tempfile.TemporaryDirectory(prefix=f"sc-rm-{match_id}-") as tmp:
            staging = Path(tmp)
            stage_match(root, match_id, staging)
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
        uploaded += 1

    # Owner-tier player catalogue (skip ids already public — spec §6.1).
    skip_ids = public_player_ids(s3, bucket)
    players, skipped = derive_players(metas, skip_ids)
    if skipped:
        print(f"NOTE: {len(skipped)} player id(s) already in the public tier — skipped: {skipped}")
    if players:
        with tempfile.TemporaryDirectory(prefix="sc-rm-players-") as tmp:
            players_file = Path(tmp) / "players.json"
            players_file.write_text(json.dumps({"players": players}, indent=2), encoding="utf-8")
            upload_players(
                input_file=players_file,
                provider=PROVIDER,
                bucket=bucket,
                visibility="private",
                source_name=SOURCE_NAME,
                source_licence=SOURCE_LICENCE,
            )

    return uploaded, len(players), len(skipped)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload restricted SkillCorner Real Madrid data to the mock provider API (owner tier)"
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("PINING_BUCKET"),
        help="S3 bucket name (default: $PINING_BUCKET env var)",
    )
    parser.add_argument(
        "--source-dir",
        default=os.environ.get("SKILLCORNER_RESTRICTED_DIR"),
        help="Bundle root containing meta/ tracking/ dynamic/ freeze/ physical/ "
        "(default: $SKILLCORNER_RESTRICTED_DIR env var)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Upload only the first N matches (smoke test)")
    args = parser.parse_args()

    if not args.bucket:
        parser.error("--bucket is required (or set PINING_BUCKET)")
    if not args.source_dir:
        parser.error("--source-dir is required (or set SKILLCORNER_RESTRICTED_DIR)")

    root = Path(args.source_dir)
    if not (root / "meta").is_dir():
        parser.error(f"no meta/ directory under {root} — is this a SkillCorner bundle root?")

    print(f"Uploading restricted SkillCorner RM data to s3://{args.bucket}/{PROVIDER}/ (OWNER tier)")
    uploaded, n_players, n_skipped = upload_all(root, args.bucket, limit=args.limit)
    print(f"Done — {uploaded} match(es), {n_players} player(s) uploaded, {n_skipped} player(s) skipped.")


if __name__ == "__main__":
    main()
