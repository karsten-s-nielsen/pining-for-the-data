"""Schema-drift tests: committed JSON Schema files must match the current Pydantic models.

Spec §6.6: Pydantic models in shared.py are the single source of truth.
schemas/{matches,players}.schema.json are generated from them via
scripts/regenerate_schemas.py and committed for consumer reference. Editing
a model without regenerating the schema fails this test.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = REPO_ROOT / "schemas"
sys.path.insert(0, str(REPO_ROOT / "src"))


def _load_schema(name: str) -> dict:
    return json.loads((SCHEMAS_DIR / name).read_text(encoding="utf-8"))


class TestMatchEntrySchema:
    def test_committed_schema_matches_model(self):
        from canonical.models import MatchEntry

        committed = _load_schema("matches.schema.json")
        generated = MatchEntry.model_json_schema()
        assert committed == generated, (
            "schemas/matches.schema.json is stale. Run scripts/regenerate_schemas.py "
            "to refresh after editing the MatchEntry model."
        )

    def test_schema_has_id_and_schema_metadata(self):
        committed = _load_schema("matches.schema.json")
        assert committed.get("$id") == "urn:pining-for-the-data:schema:matches:v1"
        assert committed.get("$schema") == "https://json-schema.org/draft/2020-12/schema"


class TestPlayerRecordSchema:
    def test_committed_schema_matches_model(self):
        from canonical.models import PlayerRecord

        committed = _load_schema("players.schema.json")
        generated = PlayerRecord.model_json_schema()
        assert committed == generated, (
            "schemas/players.schema.json is stale. Run scripts/regenerate_schemas.py "
            "to refresh after editing the PlayerRecord model."
        )

    def test_schema_has_id_and_schema_metadata(self):
        committed = _load_schema("players.schema.json")
        assert committed.get("$id") == "urn:pining-for-the-data:schema:players:v1"
        assert committed.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
