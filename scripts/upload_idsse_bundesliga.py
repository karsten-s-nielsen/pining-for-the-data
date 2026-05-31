"""Upload the IDSSE/Sportec open Bundesliga dataset to the mock provider API (PUBLIC tier).

Pure redistribution of CC-BY-4.0 data sourced directly from the version-pinned
figshare release (not from bronze). See
docs/superpowers/specs/2026-05-29-idsse-bundesliga-redistribution-design.md.

Modes:
  (default)          fetch -> verify against committed manifest -> stage -> upload_game
  --write-manifest   fetch the versioned listing and (re)write scripts/idsse_figshare_manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

# Make src/ importable when run directly from a checkout.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from formats.idsse import REQUIRED_ARTIFACTS, group_files_by_match, is_complete, read_match_information  # noqa: E402
from mock_api.upload import upload_game  # noqa: E402

PROVIDER = "idsse"
ARTICLE_ID = "28196177"
VERSION = 1
VERSION_URL = f"https://api.figshare.com/v2/articles/{ARTICLE_ID}/versions/{VERSION}"
MANIFEST_PATH = _REPO_ROOT / "scripts" / "idsse_figshare_manifest.json"

SOURCE_NAME = "IDSSE — Bassek, Rein, Weber & Memmert (2025); provided by DFL / Sportec Solutions"
SOURCE_URL = f"https://doi.org/10.6084/m9.figshare.{ARTICLE_ID}.v{VERSION}"
SOURCE_LICENCE = "CC-BY 4.0"

# Staged filename per role key (upload_game derives the artifact key from the stem).
_STAGED_FILENAME = {"metadata": "metadata.xml", "events": "events.xml", "tracking": "tracking.xml"}


def _md5_of(listing_entry: dict) -> str:
    # Pin figshare's computed_md5 (its hash of the stored bytes) deterministically.
    # We do NOT fall back to supplied_md5 (review R2): mixing the two fields between
    # --write-manifest time and verify time could flip and raise a false drift error.
    return listing_entry.get("computed_md5") or ""


def fetch_file_listing(version_url: str = VERSION_URL) -> list[dict]:
    """GET the versioned figshare article and return its `files` list."""
    req = urllib.request.Request(version_url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        article = json.loads(resp.read().decode("utf-8"))
    return article["files"]


def manifest_from_listing(listing: list[dict]) -> dict:
    """Build the pinned manifest {version_url, files: {name: md5}} from a listing.

    Refuses to pin a transient/empty md5 (review R2): if figshare has not yet
    computed a file's md5, --write-manifest fails loudly rather than capturing "".
    """
    files: dict[str, str] = {}
    for entry in listing:
        md5 = _md5_of(entry)
        if not md5:
            raise ValueError(f"figshare entry {entry['name']!r} has no computed_md5 yet — refusing to pin")
        files[entry["name"]] = md5
    return {
        "version_url": VERSION_URL,
        # Real (public) DFL filenames + md5s from the CC-BY release are pinned here
        # intentionally — those ids are part of the public dataset (spec §3.1), unlike
        # test fixtures which stay synthetic (review R5).
        "_note": "Real public DFL ids from the CC-BY release; intentional (spec §3.1).",
        "files": files,
    }


def verify_listing(listing: list[dict], manifest: dict) -> None:
    """Raise ValueError if the live listing diverges from the committed manifest."""
    expected = manifest["files"]
    if len(listing) != len(expected):
        raise ValueError(f"figshare file count {len(listing)} != manifest {len(expected)} — version drift?")
    for entry in listing:
        name = entry["name"]
        if name not in expected:
            raise ValueError(f"unexpected file not in manifest: {name}")
        if _md5_of(entry) != expected[name]:
            raise ValueError(f"md5 mismatch for {name}: live {_md5_of(entry)} != manifest {expected[name]}")


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_manifest(listing: list[dict], path: Path = MANIFEST_PATH) -> None:
    path.write_text(json.dumps(manifest_from_listing(listing), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote manifest with {len(listing)} files to {path}")


def download_verified(entry: dict, dest: Path) -> None:
    """Stream a figshare file to dest, verifying its md5 against the listing entry."""
    expected = _md5_of(entry)
    h = hashlib.md5()  # integrity check vs figshare-published md5, not a security primitive
    with urllib.request.urlopen(entry["download_url"], timeout=600) as resp, dest.open("wb") as out:
        for chunk in iter(lambda: resp.read(1 << 20), b""):
            h.update(chunk)
            out.write(chunk)
    if h.hexdigest() != expected:
        raise ValueError(f"md5 mismatch after download of {entry['name']}: {h.hexdigest()} != {expected}")


def complete_matches(filenames: list[str]) -> dict[str, dict[str, str]]:
    """Return only the match groups that have all three required artifacts.

    Logs and drops incomplete groups (spec §8 error handling).
    """
    complete: dict[str, dict[str, str]] = {}
    for match_id, group in group_files_by_match(filenames).items():
        if is_complete(group):
            complete[match_id] = group
        else:
            missing = [k for k in REQUIRED_ARTIFACTS if k not in group]
            print(f"WARN: match {match_id} incomplete (missing {missing}) — skipping")
    return complete


def upload_all(bucket: str, limit: int | None = None) -> int:
    """Fetch, verify, stage, and upload all complete matches. Returns matches uploaded."""
    listing = fetch_file_listing()
    verify_listing(listing, load_manifest())
    by_name = {entry["name"]: entry for entry in listing}

    groups = complete_matches([entry["name"] for entry in listing])
    match_ids = sorted(groups)
    if limit:
        match_ids = match_ids[:limit]
    print(f"Uploading {len(match_ids)} IDSSE match(es) to s3://{bucket}/{PROVIDER}/ (public tier)")

    uploaded = 0
    for match_id in match_ids:
        group = groups[match_id]
        with tempfile.TemporaryDirectory(prefix=f"idsse-{match_id}-") as tmp:
            staging = Path(tmp)
            # Fail-fast (review R1): download + parse the ~12 KB metadata BEFORE the
            # hundreds-of-MB events/tracking, so a present-but-malformed metadata XML
            # skips the match without wasting a large download.
            metadata_dest = staging / _STAGED_FILENAME["metadata"]
            download_verified(by_name[group["metadata"]], metadata_dest)
            try:
                info = read_match_information(metadata_dest)
            except ValueError as e:
                print(f"WARN: match {match_id} metadata unparseable ({e}) — skipping")
                continue
            for key in ("events", "tracking"):
                download_verified(by_name[group[key]], staging / _STAGED_FILENAME[key])
            upload_game(
                game_dir=staging,
                provider=PROVIDER,
                game_id=info.match_id,
                bucket=bucket,
                visibility="public",
                provenance="redistributed",
                date=info.date,
                home=info.home,
                away=info.away,
                source_name=SOURCE_NAME,
                source_url=SOURCE_URL,
                source_licence=SOURCE_LICENCE,
            )
            uploaded += 1
    return uploaded


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload IDSSE Bundesliga open data to the mock provider API (public)")
    parser.add_argument("--bucket", help="S3 bucket name (required unless --write-manifest)")
    parser.add_argument("--limit", type=int, default=None, help="Upload only the first N matches (smoke test)")
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="Fetch the versioned figshare listing and (re)write the committed manifest, then exit",
    )
    args = parser.parse_args()

    if args.write_manifest:
        write_manifest(fetch_file_listing())
        return

    if not args.bucket:
        parser.error("--bucket is required (or use --write-manifest)")

    n = upload_all(bucket=args.bucket, limit=args.limit)
    print(f"Done — {n} match(es) uploaded.")


if __name__ == "__main__":
    main()
