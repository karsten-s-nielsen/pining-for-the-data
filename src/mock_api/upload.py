"""Upload game artifacts to S3 and update provider indexes.

Uploads tracking data files to the mock provider API's S3 bucket and
maintains the discovery indexes (providers.json, matches.json) that
the Lambda handlers read.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import boto3

from canonical.models import MatchEntry
from formats.competition_season import native_id, native_name
from mock_api._cli_common import handle_cli_errors, utc_now_iso, validate_param
from mock_api.artifact_meta import artifact_name, staged_artifact_meta, staged_files
from mock_api.index_records import carry_updated_at


def upload_game(
    game_dir: Path,
    provider: str,
    game_id: str,
    bucket: str,
    visibility: str = "public",
    date: str | None = None,
    home: str | None = None,
    away: str | None = None,
    provenance: str | None = None,
    source_name: str | None = None,
    source_url: str | None = None,
    source_licence: str | None = None,
    format_version: int | None = None,
    competition_id: str | None = None,
    competition_name: str | None = None,
    season_id: str | None = None,
    season_name: str | None = None,
) -> list[str]:
    """Upload all files in game_dir to S3 and update indexes.

    The entry is built and validated BEFORE any S3 write, so a refused value leaves the stored
    objects and the index unchanged. ``updated_at`` changes only when the entry changes (ADR 0013).

    Parameters
    ----------
    game_dir : Path
        Directory containing artifact files (tracking.txt, metadata.xml, etc.)
    provider : str
        Provider name (e.g., "skillcorner", "respovision").
    game_id : str
        Game identifier (e.g., "game_03").
    bucket : str
        S3 bucket name.
    visibility : str
        ``"public"`` (default) or ``"private"``. Private content is written under
        ``{provider}/_private/{game_id}/...`` and recorded with
        ``visibility: "private"`` in matches.json.
    date : str | None
        Game date for the matches index (YYYY-MM-DD).
    home : str | None
        Home team name for the matches index.
    away : str | None
        Away team name for the matches index.
    provenance : str | None
        How the data reached the platform ("redistributed", "deidentified", "original").
    source_name : str | None
        Name of the original data source.
    source_url : str | None
        URL of the original data source.
    source_licence : str | None
        Licence of the original data source. British spelling canonical;
        ``--source-license`` is accepted as a quiet alias by the argparse layer
        (see ``main()``) and forwarded into this parameter.
    format_version : int | None
        Artifact-format generation to record (e.g. 2 for the canonical Parquet/zstd set).
    competition_id, competition_name, season_id, season_name : str | None
        Competition/season identity, verbatim from the match's own metadata artifact. ``None``
        or ``""`` means the source gives no value; the field is then absent from the entry.

    Returns
    -------
    list[str]
        List of uploaded artifact names.
    """
    if visibility not in ("public", "private"):
        raise ValueError(f"Invalid visibility: {visibility!r} (must be 'public' or 'private')")

    validate_param(provider, "provider")
    validate_param(game_id, "game_id")

    # 1. Build artifacts as {name: filename} object form (spec §4.1), hash the staged bytes, and
    #    build and validate the canonical entry before any S3 write.
    files = staged_files(game_dir)
    artifacts = {artifact_name(p.name): p.name for p in files}
    entry: dict | None = None
    if files:
        entry = _build_match_entry(
            game_id,
            artifacts,
            visibility,
            date,
            home,
            away,
            provenance,
            source_name,
            source_url,
            source_licence,
            format_version,
            competition={
                "competition_id": native_id(competition_id, "competition_id"),
                "competition_name": native_name(competition_name, "competition_name"),
                "season_id": native_id(season_id, "season_id"),
                "season_name": native_name(season_name, "season_name"),
            },
            artifact_meta=staged_artifact_meta(game_dir),
        )

    s3 = boto3.client("s3")

    # 2. Reject tier mixing: a re-upload cannot flip an existing match's tier.
    _check_no_tier_mixing(s3, bucket, provider, game_id, visibility)

    if entry is None:
        print(f"  No files found in {game_dir}")
        return list(artifacts)

    # 3. Upload the objects.
    prefix = f"{provider}/_private/{game_id}" if visibility == "private" else f"{provider}/{game_id}"
    for file_path in files:
        key = f"{prefix}/{file_path.name}"
        s3.upload_file(str(file_path), bucket, key)
        print(f"  Uploaded {file_path.name} -> s3://{bucket}/{key}")

    # 4. Write the index; an unchanged entry keeps its stored updated_at.
    _update_matches_json(s3, bucket, provider, entry)
    _update_providers_json(s3, bucket, provider)

    return list(artifacts)


def _build_match_entry(
    game_id: str,
    artifacts: dict[str, str],
    visibility: str,
    date: str | None,
    home: str | None,
    away: str | None,
    provenance: str | None,
    source_name: str | None,
    source_url: str | None,
    source_licence: str | None,
    format_version: int | None,
    competition: dict[str, str | None],
    artifact_meta: dict[str, dict[str, str | int]],
) -> dict:
    """Assemble and Pydantic-validate a MatchEntry. Raises on validation error."""
    payload: dict = {
        "id": game_id,
        "artifacts": artifacts,
        "visibility": visibility,
        "updated_at": utc_now_iso(),
    }
    if format_version is not None:
        payload["format_version"] = format_version
    if date:
        payload["date"] = date
    if home:
        payload["home"] = home
    if away:
        payload["away"] = away
    if provenance:
        payload["provenance"] = provenance
    if source_name:
        payload["source"] = {
            "name": source_name,
            "url": source_url or "",
            "licence": source_licence or "",
        }
    # Absent values stay absent (ADR 0010): only a value the source gives is written.
    payload.update({k: v for k, v in competition.items() if v is not None})
    payload["artifact_meta"] = artifact_meta
    # Validation will raise pydantic.ValidationError before any S3 write.
    return MatchEntry.model_validate(payload).model_dump(exclude_none=True)


def _check_no_tier_mixing(s3, bucket: str, provider: str, game_id: str, new_visibility: str) -> None:
    """Raise ValueError if `game_id` exists in matches.json with a different tier."""
    key = f"{provider}/matches.json"
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        data = json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        return  # No existing index — nothing to conflict with.

    existing = next((m for m in data.get("matches", []) if m.get("id") == game_id), None)
    if existing is None:
        return
    existing_visibility = existing.get("visibility", "public")
    if existing_visibility != new_visibility:
        raise ValueError(
            f"Cannot mix tiers for game_id {game_id!r}: existing entry is "
            f"{existing_visibility!r}, requested {new_visibility!r}. "
            f"Re-tiering is not supported by the upload tool. To change a game's visibility tier, "
            f"manually delete the existing entry from matches.json and re-upload with the desired visibility."
        )


def _update_matches_json(s3, bucket: str, provider: str, entry: dict) -> None:
    """Read-modify-write the matches.json index for a provider.

    `entry` MUST already be a validated MatchEntry dict (see _build_match_entry). When the stored
    entry equals `entry` apart from `updated_at`, the stored `updated_at` is kept (ADR 0013).
    """
    key = f"{provider}/matches.json"

    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        data = json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        data = {"provider": provider, "matches": []}

    game_id = entry["id"]
    existing = next((m for m in data["matches"] if m["id"] == game_id), None)
    if existing:
        idx = data["matches"].index(existing)
        data["matches"][idx] = carry_updated_at(entry, existing)
    else:
        data["matches"].append(entry)

    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(data, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    print(f"  Updated {key}")


def _update_providers_json(s3, bucket: str, provider: str) -> None:
    """Add provider to providers.json if not already present."""
    key = "providers.json"

    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        data = json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        data = {"providers": []}

    if provider not in data["providers"]:
        data["providers"].append(provider)
        data["providers"].sort()
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(data, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        print(f"  Added '{provider}' to providers.json")
    else:
        print(f"  Provider '{provider}' already in providers.json")


def main() -> None:
    """CLI entry point for uploading game data to S3."""
    parser = argparse.ArgumentParser(description="Upload game artifacts to the mock provider API's S3 bucket")
    parser.add_argument("game_dir", type=Path, help="Directory containing game artifacts")
    parser.add_argument("--provider", required=True, help="Provider name (e.g., skillcorner)")
    parser.add_argument("--game-id", required=True, help="Game identifier (e.g., game_03)")
    parser.add_argument(
        "--bucket",
        required=False,
        default=os.environ.get("PINING_BUCKET"),
        help="S3 bucket name (default: $PINING_BUCKET env var)",
    )
    parser.add_argument(
        "--visibility",
        default="public",
        choices=["public", "private"],
        help="Visibility tier (default: public; private writes under _private/ prefix)",
    )
    parser.add_argument("--date", default=None, help="Game date (YYYY-MM-DD)")
    parser.add_argument("--home", default=None, help="Home team name")
    parser.add_argument("--away", default=None, help="Away team name")
    parser.add_argument(
        "--provenance",
        default=None,
        choices=["redistributed", "deidentified", "original"],
        help="How the data reached the platform",
    )
    parser.add_argument("--source-name", default=None, help="Name of the original data source")
    parser.add_argument("--source-url", default=None, help="URL of the original data source")
    # Both spellings populate `source_licence`. British is canonical; American
    # is a quiet alias (no deprecation warning). Spec §8.2.1.
    parser.add_argument(
        "--source-licence",
        "--source-license",
        dest="source_licence",
        default=None,
        help="Source licence text (British spelling canonical; --source-license also accepted)",
    )
    parser.add_argument(
        "--format-version",
        type=int,
        default=None,
        help="Artifact-format generation to record (e.g. 2 for the canonical Parquet/zstd set)",
    )
    # Competition/season identity, verbatim from the match's own metadata (ADR 0013). Omit a flag
    # when the source gives no value; never infer one.
    parser.add_argument("--competition-id", default=None, help="Provider's competition id, verbatim")
    parser.add_argument("--competition-name", default=None, help="Provider's competition name, verbatim")
    parser.add_argument("--season-id", default=None, help="Provider's season id, verbatim")
    parser.add_argument("--season-name", default=None, help="Provider's season value, verbatim")
    args = parser.parse_args()

    if not args.bucket:
        parser.error("--bucket is required (or set PINING_BUCKET environment variable)")

    if not args.game_dir.is_dir():
        parser.error(f"Not a directory: {args.game_dir}")

    print(f"Uploading {args.game_id} ({args.provider}, {args.visibility}) to s3://{args.bucket}/")
    artifacts = handle_cli_errors(
        parser,
        upload_game,
        game_dir=args.game_dir,
        provider=args.provider,
        game_id=args.game_id,
        bucket=args.bucket,
        visibility=args.visibility,
        date=args.date,
        home=args.home,
        away=args.away,
        provenance=args.provenance,
        source_name=args.source_name,
        source_url=args.source_url,
        source_licence=args.source_licence,
        format_version=args.format_version,
        competition_id=args.competition_id,
        competition_name=args.competition_name,
        season_id=args.season_id,
        season_name=args.season_name,
    )
    print(f"Done — {len(artifacts)} artifact(s) uploaded.")
