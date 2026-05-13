"""Upload provider-level player reference data to S3.

Reads a canonical JSON file (a list of PlayerRecord objects, or
{"players": [...]}) and writes the provider's players index to S3 at one of:

- {provider}/players.json (visibility=public)
- {provider}/_private/players.json (visibility=private)

CSV input is explicitly rejected (spec §6.5) — provider-specific shapes must
be normalised to canonical JSON by a provider-specific adapter; see
scripts/upload_gradient_wc2022.py for a worked example.

Existing players (by id, within the same tier) are updated in place; new
players are appended; updated_at is set on every write.

Tier mixing is rejected at two levels:
- within the same file: re-uploading a public id with --visibility private fails
- across both files: an id present in EITHER tier blocks an upload of the same
  id to the OTHER tier (spec §6.5 step 4)
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import boto3

from canonical.models import PlayerRecord
from mock_api._cli_common import handle_cli_errors, utc_now_iso, validate_param

_CSV_REJECTION_MESSAGE = (
    "pining-upload-players accepts canonical JSON only (a list of PlayerRecord objects, "
    'or {"players": [...]}).\n'
    "CSV input is not supported by this CLI — provider-specific shapes must be normalised "
    "to canonical JSON by a provider-specific adapter. See scripts/upload_gradient_wc2022.py for "
    "a worked example."
)


def upload_players(
    input_file: Path,
    provider: str,
    bucket: str,
    visibility: str = "public",
    source_name: str | None = None,
    source_url: str | None = None,
    source_licence: str | None = None,
) -> int:
    """Upload a canonical-JSON player catalogue to S3. Returns the number of players in the resulting index."""
    if visibility not in ("public", "private"):
        raise ValueError(f"Invalid visibility: {visibility!r}")
    validate_param(provider, "provider")

    if not input_file.is_file():
        raise FileNotFoundError(f"Not a file: {input_file}")

    # CSV (or anything not .json) is explicitly rejected — spec §6.5.
    if input_file.suffix.lower() != ".json":
        raise ValueError(_CSV_REJECTION_MESSAGE)

    raw_records = _read_canonical_json(input_file)

    # Validate every record against the canonical Pydantic model BEFORE any S3 call.
    now = utc_now_iso()
    new_records: list[dict] = []
    for raw in raw_records:
        record = dict(raw)  # avoid mutating caller's data
        record["visibility"] = visibility
        record.setdefault("updated_at", now)
        if source_name and "source" not in record:
            record["source"] = {
                "name": source_name,
                "url": source_url or "",
                "licence": source_licence or "",
            }
        # PlayerRecord.model_validate raises ValidationError with field-level diagnostics.
        validated = PlayerRecord.model_validate(record)
        # Always refresh updated_at on this write.
        dumped = validated.model_dump(exclude_none=True)
        dumped["updated_at"] = now
        new_records.append(dumped)

    s3 = boto3.client("s3")
    target_key = f"{provider}/_private/players.json" if visibility == "private" else f"{provider}/players.json"
    other_key = f"{provider}/players.json" if visibility == "private" else f"{provider}/_private/players.json"

    target_existing = _read_index(s3, bucket, target_key)
    other_existing = _read_index(s3, bucket, other_key)

    # Cross-tier dedup check (spec §6.5 step 4): no incoming id may already
    # exist in the OTHER tier's file.
    other_ids = {p.get("id") for p in other_existing}
    for new in new_records:
        if new["id"] in other_ids:
            raise ValueError(
                f"Cross-tier collision for player id {new['id']!r}: id already exists in the "
                f"other tier ({other_key!r}). Re-tiering is not supported by the upload tool. "
                f"Manually delete the player from the existing tier's index and re-upload with "
                f"the desired visibility."
            )

    # Same-file tier-mixing check (defensive — same-tier merge only here).
    by_id: dict[str, dict] = {p["id"]: p for p in target_existing}
    for new in new_records:
        prior = by_id.get(new["id"])
        if prior is not None and prior.get("visibility", "public") != visibility:
            raise ValueError(
                f"Cannot mix tiers for player id {new['id']!r}: existing "
                f"{prior.get('visibility')!r} vs requested {visibility!r}"
            )
        by_id[new["id"]] = new

    merged = sorted(by_id.values(), key=lambda p: p["id"])
    payload = {"provider": provider, "players": merged}

    s3.put_object(
        Bucket=bucket,
        Key=target_key,
        Body=json.dumps(payload, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    print(f"  Wrote {len(merged)} player(s) to s3://{bucket}/{target_key}")
    return len(merged)


def _read_canonical_json(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "players" in data:
        return data["players"]
    raise ValueError("Canonical JSON input must be a list or {'players': [...]}")


def _read_index(s3, bucket: str, key: str) -> list[dict]:
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(obj["Body"].read().decode("utf-8")).get("players", [])
    except s3.exceptions.NoSuchKey:
        return []


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload a canonical-JSON player catalogue to the mock provider API's S3 bucket"
    )
    parser.add_argument("input_file", type=Path, help="Canonical JSON file with player records")
    parser.add_argument("--provider", required=True, help="Provider name (e.g., gradient-sports)")
    parser.add_argument(
        "--bucket",
        required=False,
        default=os.environ.get("PINING_BUCKET"),
        help="S3 bucket name (default: $PINING_BUCKET env var)",
    )
    parser.add_argument("--visibility", default="public", choices=["public", "private"])
    parser.add_argument("--source-name", default=None, help="Name of the original data source")
    parser.add_argument("--source-url", default=None, help="URL of the original data source")
    # British spelling canonical; American is a quiet alias (spec §8.2.1).
    parser.add_argument(
        "--source-licence",
        "--source-license",
        dest="source_licence",
        default=None,
        help="Source licence text (British spelling canonical; --source-license also accepted)",
    )
    args = parser.parse_args()

    if not args.bucket:
        parser.error("--bucket is required (or set PINING_BUCKET environment variable)")

    print(f"Uploading players ({args.provider}, {args.visibility}) from {args.input_file}")
    handle_cli_errors(
        parser,
        upload_players,
        input_file=args.input_file,
        provider=args.provider,
        bucket=args.bucket,
        visibility=args.visibility,
        source_name=args.source_name,
        source_url=args.source_url,
        source_licence=args.source_licence,
    )
    print("Done.")
