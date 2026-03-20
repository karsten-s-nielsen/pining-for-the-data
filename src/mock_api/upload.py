"""Upload game artifacts to S3 and update provider indexes.

Uploads tracking data files to the mock provider API's S3 bucket and
maintains the discovery indexes (providers.json, matches.json) that
the Lambda handlers read.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import boto3


def upload_game(
    game_dir: Path,
    provider: str,
    game_id: str,
    bucket: str,
    date: str | None = None,
    home: str | None = None,
    away: str | None = None,
) -> list[str]:
    """Upload all files in game_dir to S3 and update indexes.

    Parameters
    ----------
    game_dir : Path
        Directory containing artifact files (tracking.txt, metadata.xml, etc.)
    provider : str
        Provider name (e.g., "metrica", "respovision").
    game_id : str
        Game identifier (e.g., "game_03").
    bucket : str
        S3 bucket name.
    date : str | None
        Game date for the matches index (YYYY-MM-DD).
    home : str | None
        Home team name for the matches index.
    away : str | None
        Away team name for the matches index.

    Returns
    -------
    list[str]
        List of uploaded artifact names.
    """
    s3 = boto3.client("s3")

    # Upload all files in the directory
    artifacts: list[str] = []
    for file_path in sorted(game_dir.iterdir()):
        if file_path.is_file() and not file_path.name.startswith("."):
            key = f"{provider}/{game_id}/{file_path.name}"
            s3.upload_file(str(file_path), bucket, key)
            artifact_name = file_path.stem
            artifacts.append(artifact_name)
            print(f"  Uploaded {file_path.name} -> s3://{bucket}/{key}")

    if not artifacts:
        print(f"  No files found in {game_dir}")
        return artifacts

    # Update indexes
    _update_matches_json(s3, bucket, provider, game_id, artifacts, date, home, away)
    _update_providers_json(s3, bucket, provider)

    return artifacts


def _update_matches_json(
    s3,
    bucket: str,
    provider: str,
    game_id: str,
    artifacts: list[str],
    date: str | None,
    home: str | None,
    away: str | None,
) -> None:
    """Read-modify-write the matches.json index for a provider."""
    key = f"{provider}/matches.json"

    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        data = json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        data = {"provider": provider, "matches": []}

    # Find existing entry or create new one
    existing = next((m for m in data["matches"] if m["id"] == game_id), None)
    entry = {
        "id": game_id,
        "artifacts": artifacts,
    }
    if date:
        entry["date"] = date
    if home:
        entry["home"] = home
    if away:
        entry["away"] = away

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
    parser = argparse.ArgumentParser(
        description="Upload game artifacts to the mock provider API's S3 bucket"
    )
    parser.add_argument("game_dir", type=Path, help="Directory containing game artifacts")
    parser.add_argument("--provider", required=True, help="Provider name (e.g., metrica)")
    parser.add_argument("--game-id", required=True, help="Game identifier (e.g., game_03)")
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument("--date", default=None, help="Game date (YYYY-MM-DD)")
    parser.add_argument("--home", default=None, help="Home team name")
    parser.add_argument("--away", default=None, help="Away team name")
    args = parser.parse_args()

    if not args.game_dir.is_dir():
        parser.error(f"Not a directory: {args.game_dir}")

    print(f"Uploading {args.game_id} ({args.provider}) to s3://{args.bucket}/")
    artifacts = upload_game(
        game_dir=args.game_dir,
        provider=args.provider,
        game_id=args.game_id,
        bucket=args.bucket,
        date=args.date,
        home=args.home,
        away=args.away,
    )
    print(f"Done — {len(artifacts)} artifact(s) uploaded.")
