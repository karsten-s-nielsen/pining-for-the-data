"""Migrate S3 data from pff/ prefix to gradient-sports/ prefix.

One-shot migration script for the PFF -> Gradient Sports rebrand. Copies all
objects under the old prefix to the new one, updates providers.json, then
deletes the old objects.

Idempotent — safe to re-run. Objects that already exist at the new prefix
are overwritten (same content). The old prefix is only deleted after all
copies succeed.

Usage:
    python scripts/migrate_pff_to_gradient_sports.py --bucket karstenskyt-pining-for-the-data [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate S3 data from pff/ to gradient-sports/")
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be done without making changes")
    args = parser.parse_args()

    import boto3

    s3 = boto3.client("s3")
    old_prefix = "pff/"
    new_prefix = "gradient-sports/"

    # 1. List all objects under pff/
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

    # 3. Update matches.json provider field (if it exists at the new location)
    matches_key = f"{new_prefix}matches.json"
    try:
        obj = s3.get_object(Bucket=args.bucket, Key=matches_key)
        matches_data = json.loads(obj["Body"].read().decode("utf-8"))
        if matches_data.get("provider") == "pff":
            matches_data["provider"] = "gradient-sports"
            if args.dry_run:
                print(f"  [dry-run] UPDATE {matches_key}: provider pff -> gradient-sports")
            else:
                s3.put_object(
                    Bucket=args.bucket,
                    Key=matches_key,
                    Body=json.dumps(matches_data, indent=2).encode("utf-8"),
                    ContentType="application/json",
                )
                print(f"  UPDATED {matches_key}: provider pff -> gradient-sports")
    except s3.exceptions.NoSuchKey:
        pass

    # 4. Update players.json provider field (public + private)
    for players_key in [f"{new_prefix}players.json", f"{new_prefix}_private/players.json"]:
        try:
            obj = s3.get_object(Bucket=args.bucket, Key=players_key)
            players_data = json.loads(obj["Body"].read().decode("utf-8"))
            if players_data.get("provider") == "pff":
                players_data["provider"] = "gradient-sports"
                if args.dry_run:
                    print(f"  [dry-run] UPDATE {players_key}: provider pff -> gradient-sports")
                else:
                    s3.put_object(
                        Bucket=args.bucket,
                        Key=players_key,
                        Body=json.dumps(players_data, indent=2).encode("utf-8"),
                        ContentType="application/json",
                    )
                    print(f"  UPDATED {players_key}: provider pff -> gradient-sports")
        except s3.exceptions.NoSuchKey:
            pass

    # 5. Update providers.json at the bucket root
    try:
        obj = s3.get_object(Bucket=args.bucket, Key="providers.json")
        providers_data = json.loads(obj["Body"].read().decode("utf-8"))
        providers_list = providers_data.get("providers", [])
        if "pff" in providers_list:
            providers_list = ["gradient-sports" if p == "pff" else p for p in providers_list]
            providers_data["providers"] = providers_list
            if args.dry_run:
                print("  [dry-run] UPDATE providers.json: pff -> gradient-sports in providers list")
            else:
                s3.put_object(
                    Bucket=args.bucket,
                    Key="providers.json",
                    Body=json.dumps(providers_data, indent=2).encode("utf-8"),
                    ContentType="application/json",
                )
                print("  UPDATED providers.json: pff -> gradient-sports in providers list")
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
