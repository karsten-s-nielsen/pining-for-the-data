"""Upload PFF FIFA World Cup 2022 to the mock provider API as private-tier data.

Reshapes the source bundle into per-match staging directories, then calls
the pining-upload primitives. For players, normalises players.csv into a
canonical-JSON file in a temp directory and calls pining-upload-players
(which only accepts canonical JSON; spec §6.5).

Idempotent — re-running re-uploads without producing duplicate index entries.

Loads private-tier only — visibility is hardcoded to "private" for both matches
and players. A single-owner private-tier load (the data goes only into the
operator's own private bucket, served back only to the operator's own
owner-token holder) does not engage redistribution licence concerns: it's the
operator moving their own data between their own systems. If a public-tier
upload mode is ever added to this script, that path will need its own
licence-clarification gate before serving.

Source layout (input):
    FIFA World Cup 2022/
    ├── Event Data/{id}.json
    ├── Metadata/{id}.json
    ├── Rosters/{id}.json
    ├── Tracking Data/{id}.jsonl.bz2
    ├── competitions.csv          # not uploaded — directory data covered by /matches
    ├── players.csv               # normalised to canonical JSON, then uploaded as /players catalogue
    └── PFF FC Change Log.docx    # not uploaded
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import tempfile
from pathlib import Path

# Ensure src/ is importable when running directly from a checkout
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from mock_api.upload import upload_game  # noqa: E402
from mock_api.upload_players import upload_players  # noqa: E402

PROVIDER = "pff"
SOURCE_NAME = "PFF FC"
SOURCE_URL = "https://www.pff.com/"
SOURCE_LICENCE = "Restricted; redistribution not permitted pending licence clarification"


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk-upload PFF World Cup 2022 to the mock provider API")
    parser.add_argument("source_dir", type=Path, help="Path to the 'FIFA World Cup 2022' source folder")
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of matches uploaded (smoke test)")
    parser.add_argument("--skip-players", action="store_true", help="Skip players.csv upload (matches only)")
    parser.add_argument("--skip-matches", action="store_true", help="Skip per-match upload (players only)")
    args = parser.parse_args()

    if not args.source_dir.is_dir():
        parser.error(f"Not a directory: {args.source_dir}")

    if not args.skip_matches:
        match_ids = _discover_match_ids(args.source_dir)
        if args.limit:
            match_ids = match_ids[: args.limit]
        print(f"Uploading {len(match_ids)} match(es) to s3://{args.bucket}/{PROVIDER}/_private/")
        for match_id in match_ids:
            _upload_one_match(args.source_dir, match_id, args.bucket)

    if not args.skip_players:
        players_csv = args.source_dir / "players.csv"
        if not players_csv.is_file():
            print(f"WARN: {players_csv} not found, skipping player catalogue upload")
        else:
            with tempfile.TemporaryDirectory(prefix="pff-players-") as tmp:
                canonical_json = Path(tmp) / "players.json"
                count = _normalise_players_csv_to_canonical(players_csv, canonical_json)
                print(f"Normalised {count} PFF player(s) to canonical JSON at {canonical_json}")
                print(f"Uploading player catalogue to s3://{args.bucket}/{PROVIDER}/_private/players.json")
                upload_players(
                    input_file=canonical_json,
                    provider=PROVIDER,
                    bucket=args.bucket,
                    visibility="private",
                    source_name=SOURCE_NAME,
                    source_url=SOURCE_URL,
                    source_licence=SOURCE_LICENCE,
                )

    print("Done.")


def _normalise_players_csv_to_canonical(csv_path: Path, out_path: Path) -> int:
    """Read PFF's players.csv and write a canonical-JSON file matching PlayerRecord.

    PFF columns: dob, firstName, height, id, lastName, nickname, positionGroupType.
    Maps directly to canonical fields with no semantic translation; type-coerce
    `height` to float. visibility/updated_at/source are added by the upload CLI.
    """
    records: list[dict] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("id"):
                continue
            record: dict = {"id": str(row["id"])}
            for key in ("firstName", "lastName", "nickname", "dob", "positionGroupType"):
                val = row.get(key)
                if val:
                    record[key] = val
            if row.get("height"):
                try:
                    record["height"] = float(row["height"])
                except (TypeError, ValueError):
                    pass
            records.append(record)

    out_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return len(records)


def _discover_match_ids(source_dir: Path) -> list[str]:
    """Return sorted match IDs by listing Metadata/*.json (the canonical match file)."""
    metadata_dir = source_dir / "Metadata"
    if not metadata_dir.is_dir():
        raise FileNotFoundError(f"Missing Metadata/ in {source_dir}")
    return sorted(p.stem for p in metadata_dir.glob("*.json"))


def _upload_one_match(source_dir: Path, match_id: str, bucket: str) -> None:
    """Reshape source files into a temp staging dir and call upload_game."""
    metadata_path = source_dir / "Metadata" / f"{match_id}.json"
    events_path = source_dir / "Event Data" / f"{match_id}.json"
    roster_path = source_dir / "Rosters" / f"{match_id}.json"
    tracking_path = source_dir / "Tracking Data" / f"{match_id}.jsonl.bz2"

    for required in (metadata_path, events_path, roster_path, tracking_path):
        if not required.is_file():
            raise FileNotFoundError(f"Match {match_id}: expected file missing: {required}")

    metadata_obj = json.loads(metadata_path.read_text(encoding="utf-8"))
    if isinstance(metadata_obj, list):
        # PFF wraps the metadata in a single-element list
        metadata_obj = metadata_obj[0] if metadata_obj else {}

    date = metadata_obj.get("date", "").split("T", 1)[0]
    home = (metadata_obj.get("homeTeam") or {}).get("name", "")
    away = (metadata_obj.get("awayTeam") or {}).get("name", "")

    with tempfile.TemporaryDirectory(prefix=f"pff-{match_id}-") as tmp:
        staging = Path(tmp)
        shutil.copy(metadata_path, staging / "metadata.json")
        shutil.copy(events_path, staging / "events.json")
        shutil.copy(roster_path, staging / "roster.json")
        shutil.copy(tracking_path, staging / "tracking.jsonl.bz2")

        upload_game(
            game_dir=staging,
            provider=PROVIDER,
            game_id=match_id,
            bucket=bucket,
            visibility="private",
            date=date,
            home=home,
            away=away,
            provenance="original",
            source_name=SOURCE_NAME,
            source_url=SOURCE_URL,
            source_licence=SOURCE_LICENCE,
        )


if __name__ == "__main__":
    main()
