"""Loader for the pinned SkillCorner events reference schema.

The reference is a committed ``{column: arrow-type}`` map (``schemas/skillcorner_events_reference.json``)
seeded once from an authored SkillCorner events Parquet. It pins the dtypes of the shared
event columns so that CSV-family ingests conform deterministically to the same schema across
matches and seasons (see ``formats/skillcorner_canonical.events_csv_to_parquet``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa

_REF = Path(__file__).resolve().parents[2] / "schemas" / "skillcorner_events_reference.json"


def load_events_reference_schema() -> dict[str, str]:
    """Return the ordered ``{column: arrow-type-string}`` map from the pinned reference JSON."""
    data = json.loads(_REF.read_text(encoding="utf-8"))
    return {name: typ for name, typ in data["columns"]}


def reference_arrow_schema(extra_cols: dict[str, str] | None = None) -> pa.Schema:
    """Build a pyarrow schema: pinned reference columns first, then any extra columns (nullable)."""
    fields = [
        pa.field(name, pa.type_for_alias(typ), nullable=True) for name, typ in load_events_reference_schema().items()
    ]
    for name, typ in (extra_cols or {}).items():
        fields.append(pa.field(name, pa.type_for_alias(typ), nullable=True))
    return pa.schema(fields)
