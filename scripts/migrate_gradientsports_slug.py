"""Migrate S3 data from gradient-sports/ prefix to gradientsports/ prefix.

One-shot migration script for the gradient-sports -> gradientsports slug rename,
aligning with the skillcorner convention of no hyphens in provider slugs.

Copies all objects under the old prefix to the new one, updates the provider
field in index files (matches.json, players.json — both public and _private/
tiers), updates providers.json at the bucket root, then deletes the old objects.

Idempotent — safe to re-run. Objects that already exist at the new prefix
are overwritten (same content). The old prefix is only deleted after all
copies succeed.

Usage:
    python scripts/migrate_gradientsports_slug.py --bucket karstenskyt-pining-for-the-data [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate S3 data from gradient-sports/ to gradientsports/")
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be done without making changes")
    args = parser.parse_args()

    import boto3

    s3 = boto3.client("s3")
    old_prefix = "gradient-sports/"
    new_prefix = "gradientsports/"
    old_slug = "gradient-sports"
    new_slug = "gradientsports"

    # 1. List all objects under gradient-sports/
    old_keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=args.bucket, Prefix=old_prefix):
        for obj in page.get("Contents", []):
            old_keys.append(obj["Key"])

    if not old_keys:
        print(f"No objects found under {old_prefix} — nothing to migrate.")
        return 0

    print(f"Found {len(old_keys)} object(s) under {old_prefix}")

    # 2. Copy each object to the new prefix
    for old_key in old_keys:
        new_key = new_prefix + old_key[len(old_prefix) :]
        if args.dry_run:
            print(f"  [dry-run] COPY {old_key} -> {new_key}")
        else:
            s3.copy_object(
                Bucket=args.bucket,
                CopySource={"Bucket": args.bucket, "Key": old_key},
                Key=new_key,
            )
            print(f"  COPIED {old_key} -> {new_key}")

    # 3. Update matches.json provider field (public + private tiers)
    for matches_key in [f"{new_prefix}matches.json", f"{new_prefix}_private/matches.json"]:
        try:
            obj = s3.get_object(Bucket=args.bucket, Key=matches_key)
            matches_data = json.loads(obj["Body"].read().decode("utf-8"))
            if matches_data.get("provider") == old_slug:
                matches_data["provider"] = new_slug
                if args.dry_run:
                    print(f"  [dry-run] UPDATE {matches_key}: provider {old_slug} -> {new_slug}")
                else:
                    s3.put_object(
                        Bucket=args.bucket,
                        Key=matches_key,
                        Body=json.dumps(matches_data, indent=2).encode("utf-8"),
                        ContentType="application/json",
                    )
                    print(f"  UPDATED {matches_key}: provider {old_slug} -> {new_slug}")
        except s3.exceptions.NoSuchKey:
            pass

    # 4. Update players.json provider field (public + private tiers)
    for players_key in [f"{new_prefix}players.json", f"{new_prefix}_private/players.json"]:
        try:
            obj = s3.get_object(Bucket=args.bucket, Key=players_key)
            players_data = json.loads(obj["Body"].read().decode("utf-8"))
            if players_data.get("provider") == old_slug:
                players_data["provider"] = new_slug
                if args.dry_run:
                    print(f"  [dry-run] UPDATE {players_key}: provider {old_slug} -> {new_slug}")
                else:
                    s3.put_object(
                        Bucket=args.bucket,
                        Key=players_key,
                        Body=json.dumps(players_data, indent=2).encode("utf-8"),
                        ContentType="application/json",
                    )
                    print(f"  UPDATED {players_key}: provider {old_slug} -> {new_slug}")
        except s3.exceptions.NoSuchKey:
            pass

    # 5. Update providers.json at the bucket root
    try:
        obj = s3.get_object(Bucket=args.bucket, Key="providers.json")
        providers_data = json.loads(obj["Body"].read().decode("utf-8"))
        providers_list = providers_data.get("providers", [])
        if old_slug in providers_list:
            providers_list = [new_slug if p == old_slug else p for p in providers_list]
            providers_data["providers"] = providers_list
            if args.dry_run:
                print(f"  [dry-run] UPDATE providers.json: {old_slug} -> {new_slug} in providers list")
            else:
                s3.put_object(
                    Bucket=args.bucket,
                    Key="providers.json",
                    Body=json.dumps(providers_data, indent=2).encode("utf-8"),
                    ContentType="application/json",
                )
                print(f"  UPDATED providers.json: {old_slug} -> {new_slug} in providers list")
    except s3.exceptions.NoSuchKey:
        print("  WARN: providers.json not found at bucket root")

    # 6. Delete old objects
    for old_key in old_keys:
        if args.dry_run:
            print(f"  [dry-run] DELETE {old_key}")
        else:
            s3.delete_object(Bucket=args.bucket, Key=old_key)
            print(f"  DELETED {old_key}")

    action = "would migrate" if args.dry_run else "migrated"
    print(f"\nDone — {action} {len(old_keys)} object(s) from {old_prefix} to {new_prefix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
