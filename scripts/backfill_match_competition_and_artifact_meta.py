"""One-shot: set competition/season fields and ``artifact_meta`` on every live match entry (ADR 0013).

For each entry of each selected ``{provider}/matches.json`` the script:

1. reads the stored metadata artifact and proves it is the entry's own (the metadata match id must
   equal the entry ``id``, spec §5.2);
2. sets ``competition_id``, ``competition_name``, ``season_id`` and ``season_name`` from it by the
   §5.3 value rules (a value the source does not give stays absent);
3. stream-hashes every stored artifact object and sets ``artifact_meta`` (no ETag, no cache);
4. validates the entry with ``MatchEntry`` and sets ``updated_at`` to the run's single timestamp
   only when the entry changed. Every other field is copied unchanged.

Two passes (owner ruling, spec §5.9). Pass 1 reads, checks and hashes every selected provider and
writes nothing; any per-entry error in any provider stops the run before any write. Pass 2 (only
with ``--apply``) writes each provider's ``matches.json`` once, with ``IfMatch`` set to the ETag read
in pass 1. A write error on one provider (a ``412`` or any other) stops only that provider; the
others are still written, and the exit status is 1. A re-run is safe: an unchanged entry keeps its
``updated_at``. Dry-run by default. The report starts with the sha256 of this file.

Retained as the audit trail for a change applied to live S3, in the manner of
``backfill_skillcorner_artifacts.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from botocore.exceptions import ClientError  # noqa: E402

from canonical.models import MatchEntry  # noqa: E402
from formats.provider_metadata import metadata_artifact_key, raw_id_values, read_metadata_identity  # noqa: E402
from mock_api._cli_common import utc_now_iso  # noqa: E402
from mock_api.artifact_meta import hash_stream  # noqa: E402
from mock_api.index_records import carry_updated_at  # noqa: E402

COMPETITION_FIELDS = ("competition_id", "competition_name", "season_id", "season_name")


@dataclass
class ProviderReport:
    provider: str
    changed: int = 0
    unchanged: int = 0
    written: bool = False
    error: str | None = None
    id_types: Counter[str] = field(default_factory=Counter)

    def line(self) -> str:
        types = dict(sorted(self.id_types.items()))
        return (
            f"provider {self.provider} changed={self.changed} unchanged={self.unchanged} "
            f"written={self.written} error={self.error} id_types={types}"
        )


@dataclass
class PreparedProvider:
    report: ProviderReport
    etag: str | None = None
    index: dict[str, Any] | None = None


def script_sha256() -> str:
    """The sha256 of this script file: the provenance of an applied run (spec §7)."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def object_key(provider: str, entry: Mapping[str, Any], filename: str) -> str:
    """The S3 key of one stored artifact, by the layout ``upload_game`` writes."""
    if entry.get("visibility") == "private":
        return f"{provider}/_private/{entry['id']}/{filename}"
    return f"{provider}/{entry['id']}/{filename}"


def backfill_entry(s3, bucket: str, provider: str, entry: dict, now: str, id_types: Counter[str]) -> dict:
    """Return the backfilled copy of one entry. Raises on a wrong artifact or a refused value."""
    visibility = entry.get("visibility", "public")
    metadata_key = metadata_artifact_key(provider, entry)
    body = s3.get_object(Bucket=bucket, Key=object_key(provider, entry, entry["artifacts"][metadata_key]))[
        "Body"
    ].read()
    for raw in raw_id_values(provider, visibility, body).values():
        # An id the source does not give is counted apart, so the report's types are real source types.
        id_types["absent" if raw is None else type(raw).__name__] += 1
    identity = read_metadata_identity(provider, visibility, body)
    if identity.match_id != str(entry["id"]):
        raise ValueError(
            f"entry {entry['id']!r}: metadata match id is {identity.match_id!r} — the artifact is not this match's"
        )

    new = {k: v for k, v in entry.items() if k not in COMPETITION_FIELDS and k != "artifact_meta"}
    new.update({k: v for k, v in identity.competition.as_kwargs().items() if v is not None})
    new["artifact_meta"] = {
        name: hash_stream(s3.get_object(Bucket=bucket, Key=object_key(provider, entry, filename))["Body"]).as_dict()
        for name, filename in entry["artifacts"].items()
    }
    new["updated_at"] = now
    MatchEntry.model_validate(new)
    return carry_updated_at(new, entry)


def prepare_provider(s3, bucket: str, provider: str, now: str) -> PreparedProvider:
    """Pass 1: read, check and hash every entry of one provider. Never writes."""
    prepared = PreparedProvider(ProviderReport(provider))
    report = prepared.report
    entry_id: object = None
    try:
        obj = s3.get_object(Bucket=bucket, Key=f"{provider}/matches.json")
        etag = obj["ETag"]
        index = json.loads(obj["Body"].read().decode("utf-8"))
        matches: list[dict] = []
        for entry in index.get("matches", []):
            entry_id = entry.get("id")
            new = backfill_entry(s3, bucket, provider, entry, now, report.id_types)
            if new == entry:
                report.unchanged += 1
            else:
                report.changed += 1
            matches.append(new)
    except Exception as e:  # any per-entry failure stops the run after pass 1 (spec §5.9)
        where = f"entry {entry_id!r}: " if entry_id is not None else ""
        report.error = f"{where}{type(e).__name__}: {e}"
        return prepared
    prepared.etag = etag
    prepared.index = {**index, "matches": matches}
    return prepared


def write_provider(s3, bucket: str, prepared: PreparedProvider) -> None:
    """Pass 2: one ``IfMatch`` write of the provider's index. Records any error; never raises."""
    report = prepared.report
    try:
        s3.put_object(
            Bucket=bucket,
            Key=f"{report.provider}/matches.json",
            Body=json.dumps(prepared.index, indent=2).encode("utf-8"),
            ContentType="application/json",
            IfMatch=prepared.etag,
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        report.error = "conflict (412)" if code == "PreconditionFailed" else f"write failed: ClientError: {e}"
        return
    except Exception as e:  # spec §5.9: any other write error stops only this provider
        report.error = f"write failed: {type(e).__name__}: {e}"
        return
    report.written = True


def _providers(s3, bucket: str) -> list[str]:
    obj = s3.get_object(Bucket=bucket, Key="providers.json")
    return list(json.loads(obj["Body"].read().decode("utf-8"))["providers"])


def main() -> int:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="backslashreplace")  # a non-ASCII value in an error never crashes the report

    import boto3

    ap = argparse.ArgumentParser(description="Backfill competition/season fields and artifact_meta (ADR 0013)")
    ap.add_argument("--bucket", default=os.environ.get("PINING_BUCKET"))
    ap.add_argument("--provider", action="append", help="repeatable; default: every provider in providers.json")
    ap.add_argument("--apply", action="store_true", help="write the indexes (default is dry-run)")
    args = ap.parse_args()
    if not args.bucket:
        ap.error("--bucket required (or set PINING_BUCKET)")

    print(f"script sha256: {script_sha256()}")
    s3 = boto3.client("s3")
    providers = args.provider or _providers(s3, args.bucket)
    now = utc_now_iso()
    print(f"mode: {'APPLY' if args.apply else 'DRY-RUN'}; providers: {providers}; run timestamp: {now}")

    prepared = [prepare_provider(s3, args.bucket, provider, now) for provider in providers]
    if any(p.report.error for p in prepared):
        for p in prepared:
            print(p.report.line())
        print("run stopped after pass 1: no provider written")
        return 1

    if args.apply:
        for p in prepared:
            write_provider(s3, args.bucket, p)
    for p in prepared:
        print(p.report.line())
    return 1 if any(p.report.error for p in prepared) else 0


if __name__ == "__main__":
    sys.exit(main())
