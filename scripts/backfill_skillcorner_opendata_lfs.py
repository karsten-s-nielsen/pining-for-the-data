"""One-shot: repair public SkillCorner opendata tracking objects stored as Git-LFS pointers.

PR #58 ingested 10 A-League ``_tracking_extrapolated.jsonl`` objects via raw.githubusercontent,
which serves the 133-byte Git-LFS pointer instead of the blob (the repo LFS-tracks every
``*.jsonl``). This script finds every live public opendata tracking object that is still a pointer
and re-uploads the resolved blob, verified byte-for-byte against the pointer's ``sha256``/``size``.

Idempotent and safe to re-run: a real object is skipped; only pointers are replaced. Dry-run by
default; pass ``--apply`` to write. It operates on LIVE S3 state (the source the mock API serves),
never a plan paraphrase, and reuses ``fetch_resolved`` from the loader so resolution + integrity
verification are one code path. Retained as the audit trail for a change applied to live S3, in the
manner of ``backfill_skillcorner_artifacts.py`` / ``migrate_skillcorner_tracking_parquet.py``.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from formats.skillcorner_opendata import (  # noqa: E402
    is_lfs_pointer,
    opendata_files,
    parse_lfs_pointer,
    public_opendata_ids,
)

PROVIDER = "skillcorner"
_PROBE_BYTES = 256  # enough to hold any LFS pointer (~133 B); trivial off a ~90 MB object


def select_repairs(probes: list[tuple[str, bytes]]) -> list[str]:
    """From (match_id, head_bytes) pairs, the ids whose tracking body is a Git-LFS pointer.

    The decision is on body content (``is_lfs_pointer``), never on an object's byte count — a
    pointer is ``125 + len(str(size))`` bytes, so an exact size gate would miss pointers whose
    blob size has a different digit count.
    """
    return [mid for mid, head in probes if is_lfs_pointer(head)]


def _live_public_ids(s3, bucket: str) -> list[str]:
    obj = s3.get_object(Bucket=bucket, Key=f"{PROVIDER}/matches.json")
    data = json.loads(obj["Body"].read().decode("utf-8"))
    return public_opendata_ids(data.get("matches", []))


def _tracking_key(mid: str) -> str:
    return f"{PROVIDER}/{mid}/{mid}_tracking_extrapolated.jsonl"


def _probe_head(s3, bucket: str, mid: str) -> bytes:
    """First ``_PROBE_BYTES`` of a tracking object via a Range GET (trivial off a ~90 MB blob)."""
    obj = s3.get_object(Bucket=bucket, Key=_tracking_key(mid), Range=f"bytes=0-{_PROBE_BYTES - 1}")
    return obj["Body"].read()


def main() -> None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

    import boto3
    from upload_skillcorner_opendata import fetch_resolved  # resolve + verify, one code path

    ap = argparse.ArgumentParser(description="Repair opendata tracking objects stored as LFS pointers")
    ap.add_argument("--bucket", default=os.environ.get("PINING_BUCKET"))
    ap.add_argument("--apply", action="store_true", help="apply changes (default is dry-run)")
    args = ap.parse_args()
    if not args.bucket:
        ap.error("--bucket required (or set PINING_BUCKET)")

    s3 = boto3.client("s3")
    ids = _live_public_ids(s3, args.bucket)
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
        return
    if not args.apply:
        print(f"Dry-run only — {len(broken)} object(s) would be repaired. Re-run with --apply.")
        return

    repaired = 0
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
        repaired += 1
        print(f"  OK {mid}: now {new_len} bytes")

    print(f"Done — {repaired} tracking object(s) repaired.")


if __name__ == "__main__":
    main()
