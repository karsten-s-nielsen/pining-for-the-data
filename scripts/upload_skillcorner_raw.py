"""Ingest a raw-JSON-family SkillCorner dataset into the canonical owner-tier format.

Reads a raw-layout dataset from the Hugging Face Hub (source repo id from
``$SKILLCORNER_RAW_HF_REPO`` or ``--hf-repo`` — never hard-coded), transforms each
manifest match into the canonical artifact set, and uploads it owner-tier
(``visibility="private"``, ``format_version=2``) via the existing ``upload_game``.

This cycle's target is English Premier League 2025/26. The pure per-match transform
(``ingest_match``) is unit-tested; ``main`` performs the HF/S3 I/O and is exercised at the
gated ops step. See docs/superpowers/specs/2026-09-01-skillcorner-canonical-parquet-format-design.md §7.2.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from formats.skillcorner_bundle import match_info  # noqa: E402
from formats.skillcorner_canonical import (  # noqa: E402
    events_csv_to_parquet,
    physical_json_to_per_match_parquet,
    tracking_to_parquet,
)
from formats.skillcorner_raw import discover_manifest_matches, raw_role_files  # noqa: E402

PROVIDER = "skillcorner"
SOURCE_NAME = "SkillCorner"
SOURCE_LICENCE = "Restricted; redistribution not permitted"


def ingest_match(
    fetch: Callable[[str], bytes],
    staging_dir: Path,
    match_id: str,
    physical_rows: list[dict],
) -> Path:
    """Transform one raw match into a canonical staging dir; return the dir path.

    ``fetch(role_path)`` returns the raw bytes for a source-relative path (injected so tests
    avoid HF/network). ``physical_rows`` are this match's rows from the combined physical file.
    """
    dest = Path(staging_dir) / match_id
    dest.mkdir(parents=True, exist_ok=True)
    roles = raw_role_files(match_id)
    (dest / "metadata.json").write_bytes(fetch(roles["metadata"]))
    (dest / "tracking.parquet").write_bytes(tracking_to_parquet(json.loads(fetch(roles["tracking"]))))
    (dest / "events.parquet").write_bytes(events_csv_to_parquet(fetch(roles["events"])))
    if physical_rows:
        per = physical_json_to_per_match_parquet(physical_rows)
        if match_id in per:
            (dest / "physical.parquet").write_bytes(per[match_id])
    return dest


def main() -> None:
    import boto3
    from huggingface_hub import hf_hub_download  # local import: only needed for the live ingest
    from upload_skillcorner_realmadrid import derive_players, public_player_ids

    from mock_api.upload import upload_game
    from mock_api.upload_players import upload_players

    ap = argparse.ArgumentParser(description="Ingest a raw-JSON SkillCorner dataset to the canonical owner tier")
    ap.add_argument("--bucket", default=os.environ.get("PINING_BUCKET"))
    ap.add_argument(
        "--hf-repo",
        default=os.environ.get("SKILLCORNER_RAW_HF_REPO"),
        help="Source HF dataset repo id (or set $SKILLCORNER_RAW_HF_REPO)",
    )
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--apply", action="store_true", help="apply changes (default is dry-run)")
    args = ap.parse_args()
    if not args.bucket:
        ap.error("--bucket required (or set PINING_BUCKET)")
    if not args.hf_repo:
        ap.error("--hf-repo required (or set SKILLCORNER_RAW_HF_REPO)")

    def hf_get(rel: str) -> bytes:
        path = hf_hub_download(repo_id=args.hf_repo, repo_type="dataset", filename=rel)
        return Path(path).read_bytes()

    manifest = hf_get("metadata/available_dynamic_event_match_ids.csv")
    match_ids = discover_manifest_matches(manifest)
    if args.limit:
        match_ids = match_ids[: args.limit]
    physical_index: dict[str, list[dict]] = {}
    for row in json.loads(hf_get("physical/physical.json"))["results"]:
        physical_index.setdefault(str(row["match_id"]), []).append(row)

    print(f"Ingesting {len(match_ids)} match(es) from {args.hf_repo} (apply={args.apply})")
    s3 = boto3.client("s3")
    metas: list[dict] = []
    uploaded = 0
    for match_id in match_ids:
        meta = json.loads(hf_get(raw_role_files(match_id)["metadata"]))
        info = match_info(meta)
        metas.append(meta)
        if not args.apply:
            print(f"  DRY-RUN {match_id}: {info.home} v {info.away} ({info.date})")
            continue
        with tempfile.TemporaryDirectory(prefix=f"sc-raw-{match_id}-") as tmp:
            staging = ingest_match(hf_get, Path(tmp), match_id, physical_index.get(match_id, []))
            upload_game(
                game_dir=staging,
                provider=PROVIDER,
                game_id=info.match_id,
                bucket=args.bucket,
                visibility="private",
                provenance="original",
                date=info.date,
                home=info.home,
                away=info.away,
                source_name=SOURCE_NAME,
                source_licence=SOURCE_LICENCE,
                format_version=2,
            )
        uploaded += 1

    if args.apply and metas:
        skip_ids = public_player_ids(s3, args.bucket)
        players, skipped = derive_players(metas, skip_ids)
        if skipped:
            print(f"NOTE: {len(skipped)} player id(s) already public — skipped")
        if players:
            with tempfile.TemporaryDirectory(prefix="sc-raw-players-") as tmp:
                players_file = Path(tmp) / "players.json"
                players_file.write_text(json.dumps({"players": players}, indent=2), encoding="utf-8")
                upload_players(
                    input_file=players_file,
                    provider=PROVIDER,
                    bucket=args.bucket,
                    visibility="private",
                    source_name=SOURCE_NAME,
                    source_licence=SOURCE_LICENCE,
                )
    print(f"Done — {uploaded} match(es) uploaded.")


if __name__ == "__main__":
    main()
