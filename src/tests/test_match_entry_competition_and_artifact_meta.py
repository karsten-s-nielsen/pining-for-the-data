"""MatchEntry competition/season fields and artifact_meta (spec §5.1). Invented values only."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from canonical.models import MatchEntry

_BASE = {
    "id": "m1",
    "artifacts": {"tracking": "tracking.parquet"},
    "visibility": "private",
    "updated_at": "2026-09-01T00:00:00Z",
}
_META_OK = {"tracking": {"sha256": "0" * 64, "size": 0}}
_NEW_KEYS = {"competition_id", "competition_name", "season_id", "season_name", "artifact_meta"}


def test_all_new_fields_optional():
    entry = MatchEntry.model_validate(_BASE)
    assert not _NEW_KEYS & set(entry.model_dump(exclude_none=True))


def test_valid_new_fields_accepted():
    entry = MatchEntry.model_validate(
        {
            **_BASE,
            "competition_id": "301",
            "competition_name": "Synthetic Premier",
            "season_id": "701",
            "season_name": "2099/2100",
            "artifact_meta": _META_OK,
        }
    )
    dumped = entry.model_dump(exclude_none=True)
    assert dumped["competition_id"] == "301"
    assert dumped["competition_name"] == "Synthetic Premier"
    assert dumped["season_id"] == "701"
    assert dumped["season_name"] == "2099/2100"
    assert dumped["artifact_meta"] == _META_OK


@pytest.mark.parametrize("field", ["competition_id", "season_id"])
@pytest.mark.parametrize("value", ["-5", "A B", "_x", "a" * 129])
def test_id_regex_enforced(field, value):
    with pytest.raises(ValidationError):
        MatchEntry.model_validate({**_BASE, field: value})


@pytest.mark.parametrize("field", ["competition_name", "season_name"])
def test_empty_name_rejected(field):
    with pytest.raises(ValidationError):
        MatchEntry.model_validate({**_BASE, field: ""})


def test_artifact_meta_key_set_must_equal_artifacts():
    two = {**_BASE, "artifacts": {"tracking": "tracking.parquet", "match": "match.json"}}
    with pytest.raises(ValidationError, match="artifact_meta"):
        MatchEntry.model_validate({**two, "artifact_meta": _META_OK})  # one key missing
    extra = {**_META_OK, "events": {"sha256": "1" * 64, "size": 3}}
    with pytest.raises(ValidationError, match="artifact_meta"):
        MatchEntry.model_validate({**_BASE, "artifact_meta": extra})  # one extra key


@pytest.mark.parametrize(
    "meta",
    [
        {"sha256": "0" * 63, "size": 0},
        {"sha256": "A" * 64, "size": 0},
        {"sha256": "g" * 64, "size": 0},
        {"sha256": "0" * 64, "size": -1},
    ],
)
def test_artifact_meta_bad_values(meta):
    with pytest.raises(ValidationError):
        MatchEntry.model_validate({**_BASE, "artifact_meta": {"tracking": meta}})
