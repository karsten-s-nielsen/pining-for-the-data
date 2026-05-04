"""One-shot migration: rewrite legacy matches.json entries into the canonical shape.

The 10 SkillCorner matches were uploaded before Task 8 of the private-data-tier
work, so their entries in `skillcorner/matches.json` use the array-form
`artifacts: [...]` and lack `visibility` + `updated_at`. The Lambda handler has
a backwards-compat fallback for this shape, but carrying the legacy code path
indefinitely is dead weight. This script migrates all such entries to:

- `artifacts` as an object {name: filename} — keys are the stems used today
  (e.g. `1886347_match`); values are discovered by listing S3 under the match
  prefix and matching the file whose stem equals the artifact name.
- `visibility: "public"` (legacy entries are all public-tier SkillCorner data).
- `updated_at: <ISO 8601 UTC>` set to the migration timestamp.
- `source.license` → `source.licence` (British canonical per spec §8.2.1).

Idempotent: entries already in object form are left alone. Skips re-write entirely
if no migration is needed.

Concurrency-safe: uses `IfMatch=<previous_etag>` on the put — a concurrent
`pining-upload` between read and write will surface as a 412 PreconditionFailed
and the script aborts cleanly (run again).

Usage:

    python scripts/backfill_skillcorner_artifacts.py \\
      --provider skillcorner \\
      --bucket karstenskyt-pining-for-the-data

After running this against every legacy provider, the array-form fallback in
`get_artifact.py` (and its 2 regression tests) can be removed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import boto3

# Import canonical model for validation
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from canonical.models import MatchEntry


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill legacy matches.json into canonical object-form")
    parser.add_argument("--provider", required=True, help="Provider name (e.g. skillcorner)")
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument("--dry-run", action="store_true", help="Print the migrated payload without writing back")
    args = parser.parse_args()

    s3 = boto3.client("s3")
    matches_key = f"{args.provider}/matches.json"

    # 1. Read matches.json with ETag for optimistic concurrency control.
    obj = s3.get_object(Bucket=args.bucket, Key=matches_key)
    etag = obj["ETag"]
    data = json.loads(obj["Body"].read().decode("utf-8"))
    print(f"Read {matches_key} (ETag={etag}, {len(data['matches'])} matches)")

    # 2. Migrate each entry.
    migration_ts = _utc_now_iso()
    migrated_count = 0
    untouched_count = 0
    for entry in data["matches"]:
        if isinstance(entry.get("artifacts"), dict):
            untouched_count += 1
            continue
        _migrate_entry(s3, args.bucket, args.provider, entry, migration_ts)
        # Validate against canonical model before accepting the migration.
        MatchEntry.model_validate(entry)
        migrated_count += 1

    print(f"Migrated {migrated_count} entries; left {untouched_count} entries untouched (already canonical).")

    if migrated_count == 0:
        print("Nothing to do — exiting.")
        return

    # 3. Show diff sample.
    sample = next((m for m in data["matches"] if isinstance(m.get("artifacts"), dict)), None)
    if sample:
        print("\nSample migrated entry:")
        print(json.dumps(sample, indent=2))

    if args.dry_run:
        print("\n--dry-run set; not writing back.")
        return

    # 4. Write back with IfMatch (fail fast on concurrent modification).
    s3.put_object(
        Bucket=args.bucket,
        Key=matches_key,
        Body=json.dumps(data, indent=2).encode("utf-8"),
        ContentType="application/json",
        IfMatch=etag,
    )
    print(f"\nWrote migrated {matches_key} (IfMatch={etag} held).")


def _migrate_entry(s3, bucket: str, provider: str, entry: dict, migration_ts: str) -> None:
    """In-place migration of one legacy entry to canonical shape."""
    match_id = entry["id"]

    # Discover actual filenames in S3.
    legacy_names = entry.get("artifacts") or []
    if not isinstance(legacy_names, list):
        raise TypeError(f"Match {match_id}: artifacts must be a list to migrate; got {type(legacy_names)}")

    s3_keys = _list_match_objects(s3, bucket, provider, match_id)
    artifacts_dict: dict[str, str] = {}
    for name in legacy_names:
        # Match the S3 key whose filename's stem (everything before the first `.`) equals the legacy name.
        match_filename = next(
            (k.rsplit("/", 1)[-1] for k in s3_keys if k.rsplit("/", 1)[-1].split(".", 1)[0] == name),
            None,
        )
        if match_filename is None:
            raise FileNotFoundError(
                f"Match {match_id}: legacy artifact {name!r} has no matching file under {provider}/{match_id}/"
            )
        artifacts_dict[name] = match_filename

    entry["artifacts"] = artifacts_dict
    entry.setdefault("visibility", "public")
    entry["updated_at"] = migration_ts

    # Rename source.license → source.licence (spec §8.2.1: British canonical).
    source = entry.get("source")
    if isinstance(source, dict) and "license" in source and "licence" not in source:
        source["licence"] = source.pop("license")


def _list_match_objects(s3, bucket: str, provider: str, match_id: str) -> list[str]:
    """List S3 keys under {provider}/{match_id}/ — returns full keys."""
    prefix = f"{provider}/{match_id}/"
    paginator = s3.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents") or []:
            keys.append(obj["Key"])
    return keys


if __name__ == "__main__":
    main()
