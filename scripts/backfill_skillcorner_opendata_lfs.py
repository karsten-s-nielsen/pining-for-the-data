"""One-shot: repair public SkillCorner opendata tracking objects stored as Git-LFS pointers.

PR #58 ingested 10 A-League ``_tracking_extrapolated.jsonl`` objects via raw.githubusercontent,
which serves the 133-byte Git-LFS pointer instead of the blob (the repo LFS-tracks every
``*.jsonl``). This script finds every live public opendata tracking object that is still a pointer
and re-uploads the resolved blob, verified byte-for-byte against the pointer's ``sha256``/``size``.

Idempotent and safe to re-run: a real object is skipped; only pointers are replaced. Dry-run by
default; pass ``--apply`` to write. Each replaced object also updates its entry's ``artifact_meta``
(the sha256/size of the new bytes) and ``updated_at``, in one ``IfMatch`` write of ``matches.json``
(ADR 0013). On a ``412`` the objects are already replaced: the script reports them and exits 1,
and ``backfill_match_competition_and_artifact_meta.py`` recomputes ``artifact_meta`` from the
stored bytes. It operates on LIVE S3 state (the source the mock API serves),
never a plan paraphrase, and reuses ``fetch_resolved`` from the loader so resolution + integrity
verification are one code path. Retained as the audit trail for a change applied to live S3, in the
manner of ``backfill_skillcorner_artifacts.py`` / ``migrate_skillcorner_tracking_parquet.py``.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
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

from canonical.models import MatchEntry  # noqa: E402
from formats.skillcorner_opendata import (  # noqa: E402
    is_lfs_pointer,
    opendata_files,
    parse_lfs_pointer,
    public_opendata_ids,
)
from mock_api._cli_common import utc_now_iso  # noqa: E402
from mock_api.artifact_meta import ArtifactDigest, hash_stream  # noqa: E402
from mock_api.index_records import carry_updated_at  # noqa: E402

PROVIDER = "skillcorner"
_INDEX_KEY = f"{PROVIDER}/matches.json"
_PROBE_BYTES = 256  # enough to hold any LFS pointer (~133 B); trivial off a ~90 MB object


def select_repairs(probes: list[tuple[str, bytes]]) -> list[str]:
    """From (match_id, head_bytes) pairs, the ids whose tracking body is a Git-LFS pointer.

    The decision is on body content (``is_lfs_pointer``), never on an object's byte count — a
    pointer is ``125 + len(str(size))`` bytes, so an exact size gate would miss pointers whose
    blob size has a different digit count.
    """
    return [mid for mid, head in probes if is_lfs_pointer(head)]


def refresh_entries(
    index: dict,
    repaired: dict[str, bytes],
    digest_of: Callable[[str], ArtifactDigest],
    now: str,
) -> dict:
    """Return a copy of ``index`` whose repaired entries carry the new tracking hash (pure).

    ``repaired`` maps a match id to its new tracking bytes. An entry that already has
    ``artifact_meta`` gets only its tracking value replaced. An entry without one gets a complete
    ``artifact_meta``: ``digest_of(s3_key)`` hashes each of its other stored objects, so the
    key-set validator passes. ``updated_at`` becomes ``now`` only when the entry changes.
    """
    out = copy.deepcopy(index)
    for i, stored in enumerate(index.get("matches", [])):
        mid = str(stored["id"])
        if mid not in repaired:
            continue
        blob = repaired[mid]
        tracking_key = f"{mid}_tracking_extrapolated"
        new_digest = ArtifactDigest(hashlib.sha256(blob).hexdigest(), len(blob))
        entry = copy.deepcopy(stored)
        if entry.get("artifact_meta") is not None:
            entry["artifact_meta"][tracking_key] = new_digest.as_dict()
        else:
            entry["artifact_meta"] = {
                key: (new_digest if key == tracking_key else digest_of(f"{PROVIDER}/{mid}/{filename}")).as_dict()
                for key, filename in entry["artifacts"].items()
            }
        entry["updated_at"] = now
        entry = carry_updated_at(entry, stored)
        MatchEntry.model_validate(entry)
        out["matches"][i] = entry
    return out


def _tracking_key(mid: str) -> str:
    return f"{PROVIDER}/{mid}/{mid}_tracking_extrapolated.jsonl"


def _probe_head(s3, bucket: str, mid: str) -> bytes:
    """First ``_PROBE_BYTES`` of a tracking object via a Range GET (trivial off a ~90 MB blob)."""
    obj = s3.get_object(Bucket=bucket, Key=_tracking_key(mid), Range=f"bytes=0-{_PROBE_BYTES - 1}")
    return obj["Body"].read()


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

    import boto3
    from botocore.exceptions import ClientError
    from upload_skillcorner_opendata import fetch_resolved  # resolve + verify, one code path

    ap = argparse.ArgumentParser(description="Repair opendata tracking objects stored as LFS pointers")
    ap.add_argument("--bucket", default=os.environ.get("PINING_BUCKET"))
    ap.add_argument("--apply", action="store_true", help="apply changes (default is dry-run)")
    args = ap.parse_args()
    if not args.bucket:
        ap.error("--bucket required (or set PINING_BUCKET)")

    s3 = boto3.client("s3")
    # Read the index once; its ETag guards the single index write after the repair.
    index_obj = s3.get_object(Bucket=args.bucket, Key=_INDEX_KEY)
    etag = index_obj["ETag"]
    index = json.loads(index_obj["Body"].read().decode("utf-8"))
    ids = public_opendata_ids(index.get("matches", []))
    print(f"public opendata matches: {len(ids)} (apply={args.apply})")

    # Probe the first bytes of every tracking object (Range GET) — no byte-count gate.
    probes = [(mid, _probe_head(s3, args.bucket, mid)) for mid in ids]
    broken = select_repairs(probes)
    heads = dict(probes)
    for mid in broken:
        _, size = parse_lfs_pointer(heads[mid])
        print(f"  {'REPAIR' if args.apply else 'DRY-RUN'} {mid}: pointer -> {size} bytes")

    if not broken:
        print("Nothing to repair — all public opendata tracking objects are real blobs.")
        return 0
    if not args.apply:
        print(f"Dry-run only — {len(broken)} object(s) would be repaired. Re-run with --apply.")
        return 0

    repaired: dict[str, bytes] = {}
    for mid in broken:
        key = _tracking_key(mid)
        blob = fetch_resolved(opendata_files(mid)["tracking_extrapolated"])  # raw->pointer->media->verify
        with tempfile.TemporaryDirectory(prefix=f"sc-od-repair-{mid}-") as tmp:
            local = Path(tmp) / f"{mid}_tracking_extrapolated.jsonl"
            local.write_bytes(blob)
            s3.upload_file(str(local), args.bucket, key)  # same call as upload_game -> metadata parity
        new_len = s3.head_object(Bucket=args.bucket, Key=key)["ContentLength"]
        if new_len != len(blob):
            raise RuntimeError(f"{mid}: post-upload size {new_len} != resolved {len(blob)}")
        repaired[mid] = blob
        print(f"  OK {mid}: now {new_len} bytes")

    def digest_of(key: str) -> ArtifactDigest:
        return hash_stream(s3.get_object(Bucket=args.bucket, Key=key)["Body"])

    new_index = refresh_entries(index, repaired, digest_of, utc_now_iso())
    try:
        s3.put_object(
            Bucket=args.bucket,
            Key=_INDEX_KEY,
            Body=json.dumps(new_index, indent=2).encode("utf-8"),
            ContentType="application/json",
            IfMatch=etag,
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "PreconditionFailed":
            raise
        print(f"CONFLICT (412): {_INDEX_KEY} changed after it was read; the index was NOT written.")
        print("These objects are already replaced; their artifact_meta in the index is stale:")
        for mid, blob in repaired.items():
            print(f"  {_tracking_key(mid)} sha256={hashlib.sha256(blob).hexdigest()} size={len(blob)}")
        print("Run scripts/backfill_match_competition_and_artifact_meta.py to recompute artifact_meta.")
        return 1

    print(f"Done — {len(repaired)} tracking object(s) repaired; {_INDEX_KEY} updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
