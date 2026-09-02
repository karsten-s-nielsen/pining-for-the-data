"""Seed the pinned SkillCorner events reference schema.

One-time (and re-runnable) seeder for ``schemas/skillcorner_events_reference.json`` — a
committed ``{column: arrow-type}`` map used to conform CSV-family event columns to stable
dtypes (see ``formats/skillcorner_canonical.events_csv_to_parquet``).

The reference is a *schema* (column names + Arrow types), not data values. SkillCorner's
event column vocabulary is already public via the openly-redistributed A-League
``*_dynamic_events.csv``; only row values are restricted, and none appear here.

Source parquet path comes from ``$SKILLCORNER_EVENTS_REF_SOURCE`` (operator-local; never
hard-coded). Record which authored match seeded the reference in the commit message.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pyarrow.parquet as pq


def main() -> None:
    src = os.environ.get("SKILLCORNER_EVENTS_REF_SOURCE")
    if not src:
        sys.exit("set $SKILLCORNER_EVENTS_REF_SOURCE to an authored SkillCorner events .parquet")
    schema = pq.read_schema(src)
    pairs = [[field.name, str(field.type)] for field in schema]
    out = Path(__file__).resolve().parents[1] / "schemas" / "skillcorner_events_reference.json"
    out.write_text(json.dumps({"columns": pairs}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(pairs)} columns)")


if __name__ == "__main__":
    main()
