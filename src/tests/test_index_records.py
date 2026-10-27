"""Record-change detection for the updated_at contract (spec §5.5)."""

from __future__ import annotations

from mock_api.index_records import carry_updated_at, same_record

_STORED = {
    "id": "m1",
    "artifacts": {"tracking": "tracking.parquet"},
    "visibility": "private",
    "season_name": "2099",
    "artifact_meta": {"tracking": {"sha256": "0" * 64, "size": 4}},
    "updated_at": "2001-01-01T00:00:00Z",
}


def _with(**changes):
    return {**_STORED, **changes}


def test_same_record_ignores_updated_at_only():
    assert same_record(_with(updated_at="2099-01-01T00:00:00Z"), _STORED)


def test_same_record_detects_field_change():
    assert not same_record(_with(season_name="2100"), _STORED)


def test_same_record_detects_hash_change():
    meta = {"tracking": {"sha256": "1" + "0" * 63, "size": 4}}
    assert not same_record(_with(artifact_meta=meta), _STORED)


def test_same_record_detects_key_on_one_side_only():
    added = _with(competition_id="301")
    assert not same_record(added, _STORED)
    dropped = {k: v for k, v in _STORED.items() if k != "season_name"}
    assert not same_record(dropped, _STORED)


def test_same_record_none_stored_is_change():
    assert not same_record(_STORED, None)


def test_carry_updated_at():
    new_ts = "2099-01-01T00:00:00Z"
    assert carry_updated_at(_with(updated_at=new_ts), _STORED)["updated_at"] == "2001-01-01T00:00:00Z"
    assert carry_updated_at(_with(updated_at=new_ts, season_name="2100"), _STORED)["updated_at"] == new_ts
    assert carry_updated_at(_with(updated_at=new_ts), None)["updated_at"] == new_ts


def test_carry_updated_at_returns_copy():
    new = _with(updated_at="2099-01-01T00:00:00Z")
    out = carry_updated_at(new, _STORED)
    assert out is not new
    assert new["updated_at"] == "2099-01-01T00:00:00Z"
