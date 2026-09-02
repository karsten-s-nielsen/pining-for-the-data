"""Unit tests for the optional MatchEntry.format_version marker."""

from __future__ import annotations

from canonical.models import MatchEntry

_BASE = {
    "id": "m1",
    "artifacts": {"tracking": "tracking.parquet"},
    "visibility": "private",
    "updated_at": "2026-09-01T00:00:00Z",
}


def test_format_version_optional_and_absent_by_default():
    entry = MatchEntry.model_validate(_BASE)
    assert entry.model_dump(exclude_none=True).get("format_version") is None


def test_format_version_recorded_when_set():
    entry = MatchEntry.model_validate({**_BASE, "format_version": 2})
    assert entry.model_dump(exclude_none=True)["format_version"] == 2
