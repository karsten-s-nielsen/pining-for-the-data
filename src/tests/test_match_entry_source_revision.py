"""MatchEntry/PlayerRecord source.revision field (opendata re-sync, ADR 0015). Invented values only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from canonical.models import MatchEntry, PlayerRecord

_MATCH_BASE = {
    "id": "m1",
    "artifacts": {"tracking": "tracking.parquet"},
    "visibility": "public",
    "updated_at": "2026-09-01T00:00:00Z",
}
_PLAYER_BASE = {
    "id": "p1",
    "visibility": "public",
    "updated_at": "2026-09-01T00:00:00Z",
    "nickname": "Synthetic Player",
}
_REV = "a" * 40
_SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"


def test_source_revision_accepts_40_hex():
    entry = MatchEntry.model_validate({**_MATCH_BASE, "source": {"name": "X", "revision": _REV}})
    assert entry.source is not None
    assert entry.source.revision == _REV


def test_source_revision_rejects_non_hex():
    with pytest.raises(ValidationError):
        MatchEntry.model_validate({**_MATCH_BASE, "source": {"name": "X", "revision": "zz"}})


def test_source_revision_rejects_short():
    with pytest.raises(ValidationError):
        MatchEntry.model_validate({**_MATCH_BASE, "source": {"name": "X", "revision": "a" * 39}})


def test_source_revision_optional():
    entry = MatchEntry.model_validate({**_MATCH_BASE, "source": {"name": "X"}})
    assert entry.source is not None
    assert entry.source.revision is None


def test_player_source_revision_accepts_40_hex():
    rec = PlayerRecord.model_validate({**_PLAYER_BASE, "source": {"name": "X", "revision": _REV}})
    assert rec.source is not None
    assert rec.source.revision == _REV


@pytest.mark.parametrize("schema_file", ["matches.schema.json", "players.schema.json"])
def test_source_revision_in_schema(schema_file):
    schema = json.loads((_SCHEMAS / schema_file).read_text(encoding="utf-8"))
    source_meta = schema["$defs"]["_SourceMeta"]
    assert "revision" in source_meta["properties"]
