"""Upload game artifacts to S3 and update provider indexes.

Uploads tracking data files to the mock provider API's S3 bucket and
maintains the discovery indexes (providers.json, matches.json) that
the Lambda handlers read.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import boto3

from canonical.models import MatchEntry

# Same rule as the API-side validator: no leading underscore (reserved namespace).
_SAFE_PARAM = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


def _validate_param(value: str, name: str) -> None:
    """Raise ValueError if param is empty, too long, or contains unsafe characters.

    Leading underscore is rejected — reserved for internal namespace markers (`_private`).
    """
    if not value or len(value) > 128 or not _SAFE_PARAM.match(value):
        raise ValueError(
            f"Invalid {name}: must be 1-128 characters, start with alphanumeric, "
            f"and contain only alphanumeric, hyphens, or underscores"
        )


def _utc_now_iso() -> str:
    """Current UTC time, ISO 8601 with trailing Z (no microseconds)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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
) -> list[str]:
    """Upload all files in game_dir to S3 and update indexes.

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

    Returns
    -------
    list[str]
        List of uploaded artifact names.
    """
    if visibility not in ("public", "private"):
        raise ValueError(f"Invalid visibility: {visibility!r} (must be 'public' or 'private')")

    _validate_param(provider, "provider")
    _validate_param(game_id, "game_id")

    s3 = boto3.client("s3")

    # Reject tier mixing: a re-upload cannot flip an existing match's tier.
    _check_no_tier_mixing(s3, bucket, provider, game_id, visibility)

    prefix = f"{provider}/_private/{game_id}" if visibility == "private" else f"{provider}/{game_id}"

    # Build artifacts as {name: filename} object form (spec §4.1).
    artifacts: dict[str, str] = {}
    for file_path in sorted(game_dir.iterdir()):
        if file_path.is_file() and not file_path.name.startswith("."):
            key = f"{prefix}/{file_path.name}"
            s3.upload_file(str(file_path), bucket, key)
            # Strip ALL extensions so tracking.jsonl.bz2 -> tracking.
            artifact_name = file_path.name.split(".", 1)[0]
            artifacts[artifact_name] = file_path.name
            print(f"  Uploaded {file_path.name} -> s3://{bucket}/{key}")

    if not artifacts:
        print(f"  No files found in {game_dir}")
        return list(artifacts)

    # Build and validate the canonical entry BEFORE any S3 index write.
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
    )
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
) -> dict:
    """Assemble and Pydantic-validate a MatchEntry. Raises on validation error."""
    payload: dict = {
        "id": game_id,
        "artifacts": artifacts,
        "visibility": visibility,
        "updated_at": _utc_now_iso(),
    }
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
            f"Re-tiering requires an explicit move (manual procedure documented in spec §11.4; "
            f"not supported by tooling in v1)."
        )


def _update_matches_json(s3, bucket: str, provider: str, entry: dict) -> None:
    """Read-modify-write the matches.json index for a provider.

    `entry` MUST already be a validated MatchEntry dict (see _build_match_entry).
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
        data["matches"][idx] = entry
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
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
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
    args = parser.parse_args()

    if not args.game_dir.is_dir():
        parser.error(f"Not a directory: {args.game_dir}")

    print(f"Uploading {args.game_id} ({args.provider}, {args.visibility}) to s3://{args.bucket}/")
    artifacts = upload_game(
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
    )
    print(f"Done — {len(artifacts)} artifact(s) uploaded.")
