"""Re-sync drifted public SkillCorner opendata artifacts to the pinned upstream commit (ADR 0015).

The loader (``upload_skillcorner_opendata.py``) is pinned and skips already-live ids, so a file that
changed upstream never reaches the store. This script brings every public opendata entry to the pin:
it stamps ``source.revision`` on all of them, and re-uploads only a ``dynamic_events`` or
``phases_of_play`` object whose bytes differ from the pin. It is upsert-only — it never deletes.

Design (ADR 0015, spec 2026-10-27):
- Fetch each artifact from the pinned raw host. The three small roles (``match``, ``dynamic_events``,
  ``phases_of_play``) give their real bytes; ``tracking_extrapolated`` is LFS-tracked and serves a
  pointer, whose ``oid`` IS the content sha256 — used for the drift check without the ~90 MB download
  (the object is never re-uploaded by this script; the verify hashes its stored bytes).
- Only ``dynamic_events``/``phases_of_play`` may be re-uploaded, and only when the sha256 differs. An
  unexpected ``match``/``tracking`` drift, an upstream 404, or an entry with no ``source`` is
  reported and that one match is stopped — never a silent re-upload, never a delete.
- Each artifact PUT is conditional on that object's ETag (``If-Match``); the index is written per
  match with the ETag re-read each iteration, so a ``412`` aborts only that match and a prior match's
  own write never ``412``s the next. Rollback uses the pre-apply VersionIds (dry-run records them).

Dry-run by default; ``--apply`` writes. Pure helpers (``plan_match``, ``refresh_entry``, the
preflights) are unit-tested with injected values; ``main`` performs the HTTP/S3 I/O at the gated step.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from upload_skillcorner_opendata import MEDIA_BASE, UPSTREAM_COMMIT  # noqa: E402
from upload_skillcorner_opendata import http_get as fetch_raw  # noqa: E402

from canonical.models import MatchEntry  # noqa: E402
from formats.skillcorner_opendata import (  # noqa: E402
    is_lfs_pointer,
    opendata_files,
    parse_lfs_pointer,
    public_opendata_ids,
)
from mock_api._cli_common import utc_now_iso  # noqa: E402
from mock_api.index_records import carry_updated_at  # noqa: E402

PROVIDER = "skillcorner"
INDEX_KEY = f"{PROVIDER}/matches.json"
_CSV_SUFFIXES = ("_dynamic_events", "_phases_of_play")  # re-syncable roles
_PROTECTED_SUFFIXES = ("_match", "_tracking_extrapolated")  # never re-PUT; a drift stops the match
_ALLOWED_CHANGE = ("artifact_meta", "updated_at")  # plus source.revision (nested) — see refresh_entry
_CONTENT_TYPE = {"csv": "text/csv", "json": "application/json"}


def _role_is_csv(key: str) -> bool:
    return key.endswith(_CSV_SUFFIXES)


def _role_is_protected(key: str) -> bool:
    return key.endswith(_PROTECTED_SUFFIXES)


def plan_match(entry: dict, staged: dict[str, dict]) -> dict:
    """Decide what to do with one entry (pure). ``staged`` maps artifact key -> {sha256, size}.

    Returns ``{"to_put": [keys], "stop_reason": str | None}``. A CSV whose sha differs (or whose
    stored meta is absent) is re-uploaded. A differing protected role, a missing staged key (an
    upstream 404), or an entry with no ``source.name`` stops the match with a reason.
    """
    keys = set(entry.get("artifacts", {}))
    missing = sorted(keys - set(staged))
    if missing:
        return {"to_put": [], "stop_reason": f"upstream file(s) missing (kept): {missing}"}
    source = entry.get("source")
    if not isinstance(source, dict) or not source.get("name"):
        return {"to_put": [], "stop_reason": "entry has no source.name; cannot stamp source.revision"}
    meta = entry.get("artifact_meta") or {}
    to_put: list[str] = []
    for key in sorted(keys):
        staged_sha = staged[key]["sha256"]
        stored_sha = (meta.get(key) or {}).get("sha256")
        differs = stored_sha != staged_sha
        if _role_is_csv(key):
            if differs:
                to_put.append(key)
        elif _role_is_protected(key) and stored_sha is not None and differs:
            return {"to_put": [], "stop_reason": f"unexpected drift in protected artifact {key}"}
    return {"to_put": to_put, "stop_reason": None}


def _reduced(entry: dict) -> dict:
    """``entry`` with the allowed-to-change fields removed, for the carry assertion."""
    out = copy.deepcopy(entry)
    for field in _ALLOWED_CHANGE:
        out.pop(field, None)
    source = out.get("source")
    if isinstance(source, dict):
        source = dict(source)
        source.pop("revision", None)
        out["source"] = source
    return out


def refresh_entry(entry: dict, new_meta: dict[str, dict], revision: str, now: str) -> dict:
    """Return a copy of ``entry`` carrying every field verbatim, changing only artifact_meta (for the
    re-uploaded keys), ``source.revision`` and ``updated_at`` (pure). Raises if anything else changed.
    """
    out = copy.deepcopy(entry)
    meta = dict(out.get("artifact_meta") or {})
    meta.update(new_meta)
    out["artifact_meta"] = meta
    source = dict(out["source"])
    source["revision"] = revision
    out["source"] = source
    out["updated_at"] = now
    out = carry_updated_at(out, entry)
    MatchEntry.model_validate(out)
    if _reduced(entry) != _reduced(out):
        raise ValueError(f"re-sync would change a field other than {_ALLOWED_CHANGE} + source.revision")
    return out


def preflight_media_sha(status_getter: Callable[[], int]) -> None:
    """Confirm the pinned commit-SHA media (LFS) ref is served: a ranged GET must return HTTP 206."""
    status = status_getter()
    if status != 206:
        raise RuntimeError(f"pinned media host returned {status}, not 206; the commit-SHA ref is unconfirmed")


def preflight_if_match(s3, bucket: str, index_key: str) -> None:
    """Confirm the bucket honours ``If-Match`` before any real write.

    Re-read the index bytes, then a conditional ``put_object`` of those exact bytes with a stale
    ``IfMatch`` must raise ``PreconditionFailed`` (nothing is written). If it instead succeeds, the
    bucket ignored the precondition — the whole conditional-write design is unsafe. In that failure
    case the probe has written a new object version, but from the just-read bytes, so the content is
    identical (a no-op version under bucket versioning); the run then aborts before any artifact PUT.
    """
    from botocore.exceptions import ClientError

    obj = s3.get_object(Bucket=bucket, Key=index_key)
    body = obj["Body"].read()
    try:
        s3.put_object(
            Bucket=bucket,
            Key=index_key,
            Body=body,
            ContentType=_CONTENT_TYPE["json"],
            IfMatch='"resync-preflight-stale-etag"',
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "PreconditionFailed":
            return
        raise
    raise RuntimeError("bucket did not honour If-Match (a stale precondition was accepted); aborting before any write")


def stage(mid: str, fetch: Callable[[str], bytes]) -> tuple[dict[str, dict], dict[str, bytes]]:
    """Fetch the four artifacts of ``mid`` from the pin. Return (digests, blobs).

    ``digests`` maps each artifact key to ``{sha256, size}``; for LFS ``tracking`` the sha256 is the
    pointer oid (no blob download). ``blobs`` holds the real bytes of the non-LFS roles only.
    """
    digests: dict[str, dict] = {}
    blobs: dict[str, bytes] = {}
    for role, rel in opendata_files(mid).items():
        key = f"{mid}_{role}"
        raw = fetch(rel)
        if is_lfs_pointer(raw):
            oid, size = parse_lfs_pointer(raw)
            digests[key] = {"sha256": oid, "size": size}
        else:
            digests[key] = {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
            blobs[key] = raw
    return digests, blobs


def media_status(mid: str, n: int = 64) -> int:
    """HTTP status of a ranged GET of ``mid``'s pinned-SHA tracking media (LFS) URL."""
    rel = opendata_files(mid)["tracking_extrapolated"]
    req = urllib.request.Request(f"{MEDIA_BASE}/{rel}", headers={"Range": f"bytes=0-{n - 1}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status


def _find_entry(index: dict, mid: str) -> dict | None:
    return next((m for m in index.get("matches", []) if str(m.get("id")) == mid), None)


def _report(note: Callable[[str], None], mid: str, message: str) -> None:
    note(f"  STOP {mid}: {message}")


def _resync_one(s3, bucket: str, mid: str, *, apply: bool, note: Callable[[str], None]) -> bool:
    """Process one match. Return True when an index write happened (apply) or would (dry-run)."""
    from botocore.exceptions import ClientError

    index_obj = s3.get_object(Bucket=bucket, Key=INDEX_KEY)
    index_etag = index_obj["ETag"]
    index = json.loads(index_obj["Body"].read().decode("utf-8"))
    entry = _find_entry(index, mid)
    if entry is None:
        _report(note, mid, "no live entry")
        return False

    try:
        digests, blobs = stage(mid, fetch_raw)
    except Exception as exc:
        _report(note, mid, f"upstream fetch failed (kept): {exc}")
        return False

    plan = plan_match(entry, digests)
    if plan["stop_reason"]:
        _report(note, mid, plan["stop_reason"])
        return False

    new_meta = {key: digests[key] for key in plan["to_put"]}
    try:
        new_entry = refresh_entry(entry, new_meta, UPSTREAM_COMMIT, utc_now_iso())
    except Exception as exc:  # pydantic validation or the carry guard — report and keep, never crash the loop
        _report(note, mid, f"entry assembly failed (kept, not stamped): {exc}")
        return False

    note(f"  {'APPLY' if apply else 'DRY-RUN'} {mid}: re-put {sorted(plan['to_put'])}; stamp source.revision")

    if not apply:
        for key in plan["to_put"]:
            obj_key = f"{PROVIDER}/{mid}/{entry['artifacts'][key]}"
            ver = s3.get_object(Bucket=bucket, Key=obj_key).get("VersionId")
            note(f"    pre-apply VersionId {obj_key} = {ver}")
        note(f"    pre-apply VersionId {INDEX_KEY} = {index_obj.get('VersionId')}")
        for field in sorted(set(entry) | set(new_entry)):
            if entry.get(field) != new_entry.get(field):
                note(f"    diff {field}: {entry.get(field)!r} -> {new_entry.get(field)!r}")
        return True

    written: list[str] = []  # objects this match already re-put; every 412 report lists them (rollback aid)
    for key in plan["to_put"]:
        obj_key = f"{PROVIDER}/{mid}/{entry['artifacts'][key]}"
        head = s3.get_object(Bucket=bucket, Key=obj_key)
        etag = head["ETag"]
        content_type = head.get("ContentType") or _CONTENT_TYPE["csv"]  # preserve the object's own type (Hyrum)
        d = digests[key]
        try:
            s3.put_object(Bucket=bucket, Key=obj_key, Body=blobs[key], ContentType=content_type, IfMatch=etag)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "PreconditionFailed":
                raise
            _report(
                note,
                mid,
                f"412 on {obj_key} (sha256={d['sha256']} size={d['size']}); aborting this match. "
                f"Already re-put this match (roll back via pre-apply VersionIds): {written or 'none'}",
            )
            return False
        written.append(f"{obj_key} sha256={d['sha256']} size={d['size']}")

    idx = index["matches"].index(entry)
    index["matches"][idx] = new_entry
    body = json.dumps(index, indent=2).encode("utf-8")
    index_ct = index_obj.get("ContentType") or _CONTENT_TYPE["json"]
    try:
        s3.put_object(Bucket=bucket, Key=INDEX_KEY, Body=body, ContentType=index_ct, IfMatch=index_etag)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "PreconditionFailed":
            raise
        _report(
            note,
            mid,
            f"412 on {INDEX_KEY}; artifacts re-put but index NOT written — re-run to converge. "
            f"Already re-put this match (roll back via pre-apply VersionIds): {written or 'none'}",
        )
        return False
    note(f"  OK {mid}: index updated")
    return True


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

    import boto3

    ap = argparse.ArgumentParser(description="Re-sync drifted public SkillCorner opendata artifacts to the pin")
    ap.add_argument("--bucket", default=os.environ.get("PINING_BUCKET"))
    ap.add_argument("--apply", action="store_true", help="apply changes (default is dry-run)")
    ap.add_argument("--ids", default=None, help="comma/space-separated ids (default: all public opendata ids)")
    args = ap.parse_args()
    if not args.bucket:
        ap.error("--bucket required (or set PINING_BUCKET)")

    s3 = boto3.client("s3")

    index = json.loads(s3.get_object(Bucket=args.bucket, Key=INDEX_KEY)["Body"].read().decode("utf-8"))
    if args.ids:
        ids = args.ids.replace(",", " ").split()
    else:
        ids = public_opendata_ids(index.get("matches", []))

    note = print
    note(f"opendata re-sync: {len(ids)} public entr{'y' if len(ids) == 1 else 'ies'} (apply={args.apply})")

    # Preflights (spec §9): media SHA ref served, and the bucket honours If-Match. Run both in dry-run.
    preflight_media_sha(lambda: media_status(ids[0]) if ids else 206)
    preflight_if_match(s3, args.bucket, INDEX_KEY)

    processed = sum(1 for mid in ids if _resync_one(s3, args.bucket, mid, apply=args.apply, note=note))
    verb = "updated" if args.apply else "planned"
    note(f"Done — {processed}/{len(ids)} entr{'y' if processed == 1 else 'ies'} {verb}.")
    stopped = len(ids) - processed
    if stopped:
        note(f"  {stopped} stopped/aborted (see STOP lines above); re-run to converge.")
    return 0 if stopped == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
