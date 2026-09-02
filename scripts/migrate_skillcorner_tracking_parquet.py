"""Migrate the existing owner-tier SkillCorner (Real Madrid) matches to the canonical format.

In-place S3 transform of each ``skillcorner/_private/<id>/`` match:
  - ``tracking.json.gz`` -> nested ``tracking.parquet`` (zstd),
  - ``events.parquet`` / ``physical.parquet`` recompressed snappy -> zstd,
  - ``freeze_frames.parquet`` dropped,
  - ``matches.json`` repointed + ``format_version: 2`` set.

Safety invariant (spec §7.1): no destructive step (overwrite or delete) happens until the
corresponding NEW object has been re-fetched from S3 and verified. Delete is always last.
Idempotent (already-canonical entries are a no-op that also sweeps any lingering legacy files).
Dry-run by default; pass ``--apply`` to mutate. The S3 client is a parameter so tests inject a
fake — no network in tests.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import boto3
import pyarrow.parquet as pq

from formats.skillcorner_canonical import (
    frames_equivalent,
    parquet_to_tracking,
    recompress_parquet_zstd,
    tracking_to_parquet,
)

PROVIDER = "skillcorner"


def _prefix(match_id: str) -> str:
    return f"{PROVIDER}/_private/{match_id}"


def _guard_key(key: str) -> str:
    """Refuse to mutate any key outside the private tier (the shared index is the sole exception)."""
    if key == f"{PROVIDER}/matches.json":
        return key
    if not key.startswith(f"{PROVIDER}/_private/"):
        raise ValueError(f"refusing to touch non-private key: {key}")
    return key


def _get(s3, bucket: str, key: str) -> bytes:
    return s3.get_object(Bucket=bucket, Key=key)["Body"].read()


def _put(s3, bucket: str, key: str, body: bytes, **kw) -> None:
    s3.put_object(Bucket=bucket, Key=_guard_key(key), Body=body, **kw)


def _delete(s3, bucket: str, key: str) -> None:
    s3.delete_object(Bucket=bucket, Key=_guard_key(key))


def _copy(s3, bucket: str, dst: str, src: str) -> None:
    s3.copy_object(Bucket=bucket, Key=_guard_key(dst), CopySource={"Bucket": bucket, "Key": src})


def _tables_equal(a: bytes, b: bytes) -> bool:
    return pq.read_table(io.BytesIO(a)).equals(pq.read_table(io.BytesIO(b)))


def migrate_match(s3, bucket: str, match_id: str, *, dry_run: bool) -> str:
    pref = _prefix(match_id)
    idx = json.loads(_get(s3, bucket, f"{PROVIDER}/matches.json"))
    entry = next(m for m in idx["matches"] if m["id"] == match_id)

    if entry.get("format_version") == 2:
        # self-heal: sweep any legacy artifacts left by a crash between index-update and delete
        if not dry_run:
            for legacy in ("tracking.json.gz", "freeze_frames.parquet"):
                _delete(s3, bucket, f"{pref}/{legacy}")
        return f"{match_id}: already canonical (orphans swept)"

    frames = json.loads(gzip.decompress(_get(s3, bucket, f"{pref}/tracking.json.gz")))
    tracking_parquet = tracking_to_parquet(frames)
    if not frames_equivalent(frames, parquet_to_tracking(tracking_parquet)):  # transform gate
        raise ValueError(f"{match_id}: in-memory tracking round-trip failed")

    events_orig = _get(s3, bucket, f"{pref}/events.parquet")
    physical_orig = _get(s3, bucket, f"{pref}/physical.parquet")
    events_new = recompress_parquet_zstd(events_orig)
    physical_new = recompress_parquet_zstd(physical_orig)
    if not _tables_equal(events_orig, events_new):  # in-memory value gate (spec §7.1 step 4)
        raise ValueError(f"{match_id}: events recompress altered values")
    if not _tables_equal(physical_orig, physical_new):
        raise ValueError(f"{match_id}: physical recompress altered values")

    if dry_run:
        return f"{match_id}: DRY-RUN would migrate (tracking + events/physical + drop freeze)"

    # upload new: tracking to final key (additive), events/physical to staging keys
    _put(s3, bucket, f"{pref}/tracking.parquet", tracking_parquet)
    _put(s3, bucket, f"{pref}/events.parquet.staging", events_new)
    _put(s3, bucket, f"{pref}/physical.parquet.staging", physical_new)

    # STORED-object gate: re-fetch from S3 and verify before any destructive step
    if not frames_equivalent(frames, parquet_to_tracking(_get(s3, bucket, f"{pref}/tracking.parquet"))):
        raise ValueError(f"{match_id}: stored tracking.parquet failed verification")
    if _get(s3, bucket, f"{pref}/events.parquet.staging") != events_new:
        raise ValueError(f"{match_id}: stored events staging mismatch")
    if _get(s3, bucket, f"{pref}/physical.parquet.staging") != physical_new:
        raise ValueError(f"{match_id}: stored physical staging mismatch")

    # swap events/physical: copy staging -> final, delete staging
    for role in ("events", "physical"):
        _copy(s3, bucket, f"{pref}/{role}.parquet", f"{pref}/{role}.parquet.staging")
        _delete(s3, bucket, f"{pref}/{role}.parquet.staging")

    # repoint index (references only verified, existing files)
    entry["artifacts"]["tracking"] = "tracking.parquet"
    entry["artifacts"].pop("freeze_frames", None)
    entry["format_version"] = 2
    _put(
        s3,
        bucket,
        f"{PROVIDER}/matches.json",
        json.dumps(idx, indent=2).encode(),
        ContentType="application/json",
    )

    # delete legacy last
    _delete(s3, bucket, f"{pref}/tracking.json.gz")
    _delete(s3, bucket, f"{pref}/freeze_frames.parquet")
    return f"{match_id}: migrated"


def migrate_all(s3, bucket: str, *, limit: int | None, dry_run: bool) -> None:
    idx = json.loads(_get(s3, bucket, f"{PROVIDER}/matches.json"))
    ids = [m["id"] for m in idx["matches"] if m.get("visibility") == "private"]
    for match_id in ids[: limit or None]:
        print(migrate_match(s3, bucket, match_id, dry_run=dry_run))


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Migrate RM SkillCorner owner-tier tracking to the canonical Parquet format"
    )
    ap.add_argument("--bucket", default=os.environ.get("PINING_BUCKET"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--apply", action="store_true", help="apply changes (default is dry-run)")
    args = ap.parse_args()
    if not args.bucket:
        ap.error("--bucket required (or set PINING_BUCKET)")
    migrate_all(boto3.client("s3"), args.bucket, limit=args.limit, dry_run=not args.apply)


if __name__ == "__main__":
    main()
