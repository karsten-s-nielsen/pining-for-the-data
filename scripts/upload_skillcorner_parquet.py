"""Ingest a parquet-processed-family SkillCorner dataset into the canonical owner-tier format.

Reads a parquet-processed-layout dataset (``meta/*.json``, ``tracking/*.json``,
``dynamic/*.parquet`` events, optional ``physical/*.parquet``; ``freeze/`` dropped) from the
Hugging Face Hub (source repo id from ``$SKILLCORNER_PARQUET_HF_REPO`` or ``--hf-repo`` — never
hard-coded), transforms each match into the canonical artifact set, and uploads it owner-tier
(``visibility="private"``, ``format_version=2``) via the existing ``upload_game``.

This cycle's target is English Premier League 2024/25 (``peggy44/PremierLeague24-25``). The pure
per-match transform (``ingest_match``) is unit-tested; ``main`` performs the HF/S3 I/O and is
exercised at the gated ops step. Matches missing a required role (metadata/tracking/events) or
with an empty tracking body are skipped up front via ``partition_ingestible`` (PL 24/25 ships two
events-less matches). Companion of ``upload_skillcorner_raw.py`` (the raw-JSON family). See
docs/superpowers/specs/2026-09-01-skillcorner-canonical-parquet-format-design.md §7.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from formats.skillcorner_bundle import match_info, parquet_role_files, partition_ingestible  # noqa: E402
from formats.skillcorner_canonical import (  # noqa: E402
    events_parquet_to_parquet,
    recompress_parquet_zstd,
    tracking_to_parquet,
)

PROVIDER = "skillcorner"
SOURCE_NAME = "SkillCorner"
SOURCE_LICENCE = "Restricted; redistribution not permitted"


def ingest_match(
    fetch: Callable[[str], bytes],
    staging_dir: Path,
    match_id: str,
    physical_bytes: bytes | None = None,
) -> Path:
    """Transform one parquet-family match into a canonical staging dir; return the dir path.

    ``fetch(role_path)`` returns the raw bytes for a source-relative path (injected so tests avoid
    HF/network). ``physical_bytes`` is this match's ``physical/<id>.parquet`` if the source ships a
    physical layer (PL 24/25 ships none -> None -> physical.parquet omitted). ``freeze`` is never
    fetched — dropped from the canonical output (ADR 0011).
    """
    dest = Path(staging_dir) / match_id
    dest.mkdir(parents=True, exist_ok=True)
    roles = parquet_role_files(match_id)
    (dest / "metadata.json").write_bytes(fetch(roles["metadata"]))
    (dest / "tracking.parquet").write_bytes(tracking_to_parquet(json.loads(fetch(roles["tracking"]))))
    (dest / "events.parquet").write_bytes(events_parquet_to_parquet(fetch(roles["events"])))
    if physical_bytes is not None:
        (dest / "physical.parquet").write_bytes(recompress_parquet_zstd(physical_bytes))
    return dest


def main() -> None:
    # Emit UTF-8 so logging a non-ASCII team name never crashes on a legacy console codepage
    # (cp1252 cannot encode characters such as 'ğ') — see the raw-family adapter for the trigger.
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

    import boto3
    from huggingface_hub import HfApi, hf_hub_download  # local import: only needed for the live ingest
    from upload_skillcorner_realmadrid import derive_players, public_player_ids

    from mock_api.upload import upload_game
    from mock_api.upload_players import upload_players

    ap = argparse.ArgumentParser(
        description="Ingest a parquet-processed SkillCorner dataset to the canonical owner tier"
    )
    ap.add_argument("--bucket", default=os.environ.get("PINING_BUCKET"))
    ap.add_argument(
        "--hf-repo",
        default=os.environ.get("SKILLCORNER_PARQUET_HF_REPO"),
        help="Source HF dataset repo id (or set $SKILLCORNER_PARQUET_HF_REPO)",
    )
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--apply", action="store_true", help="apply changes (default is dry-run)")
    args = ap.parse_args()
    if not args.bucket:
        ap.error("--bucket required (or set PINING_BUCKET)")
    if not args.hf_repo:
        ap.error("--hf-repo required (or set SKILLCORNER_PARQUET_HF_REPO)")

    api = HfApi()
    inventory = {
        f.rfilename: (f.size or 0) for f in (api.dataset_info(args.hf_repo, files_metadata=True).siblings or [])
    }

    match_ids = sorted(
        p[len("meta/") : -len(".json")]
        for p in inventory
        if p.startswith("meta/") and p.endswith(".json") and p[len("meta/") : -len(".json")].isdigit()
    )
    if args.limit:
        match_ids = match_ids[: args.limit]

    def role_size(mid: str, role: str) -> int | None:
        return inventory.get(parquet_role_files(mid)[role])

    good, skipped = partition_ingestible(match_ids, role_size)
    if skipped:
        print(f"Skipping {len(skipped)} defective match(es):")
        for mid, reason in sorted(skipped.items()):
            print(f"  SKIP {mid}: {reason}")

    physical_available = any(p.startswith("physical/") for p in inventory)

    def hf_get(rel: str) -> bytes:
        return Path(hf_hub_download(repo_id=args.hf_repo, repo_type="dataset", filename=rel)).read_bytes()

    print(f"Ingesting {len(good)} match(es) from {args.hf_repo} (apply={args.apply}); {len(skipped)} skipped")
    s3 = boto3.client("s3")
    metas: list[dict] = []
    uploaded = 0
    for match_id in good:
        meta = json.loads(hf_get(parquet_role_files(match_id)["metadata"]))
        info = match_info(meta)
        metas.append(meta)
        if not args.apply:
            print(f"  DRY-RUN {match_id}: {info.home} v {info.away} ({info.date})")
            continue
        physical_bytes = None
        if physical_available and role_size(match_id, "physical") is not None:
            physical_bytes = hf_get(parquet_role_files(match_id)["physical"])
        with tempfile.TemporaryDirectory(prefix=f"sc-pq-{match_id}-") as tmp:
            staging = ingest_match(hf_get, Path(tmp), match_id, physical_bytes=physical_bytes)
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
        players, players_skipped = derive_players(metas, skip_ids)
        if players_skipped:
            print(f"NOTE: {len(players_skipped)} player id(s) already public — skipped")
        if players:
            with tempfile.TemporaryDirectory(prefix="sc-pq-players-") as tmp:
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
    print(f"Done - {uploaded} match(es) uploaded.")


if __name__ == "__main__":
    main()
