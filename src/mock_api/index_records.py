"""The ``updated_at`` contract for index records (ADR 0013, spec §5.5).

``updated_at`` changes only when the record changes: any field, or the ``sha256`` of any artifact.
A byte-identical re-upload keeps the stored ``updated_at``. Applies to match entries and player records.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _without_updated_at(record: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k != "updated_at"}


def same_record(new: Mapping[str, Any], stored: Mapping[str, Any] | None) -> bool:
    """True when ``new`` equals ``stored`` with ``updated_at`` removed from both."""
    if stored is None:
        return False
    return _without_updated_at(new) == _without_updated_at(stored)


def carry_updated_at(new: dict[str, Any], stored: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a copy of ``new`` that keeps ``stored["updated_at"]`` when the record is unchanged."""
    out = dict(new)
    if stored is not None and same_record(new, stored):
        out["updated_at"] = stored["updated_at"]
    return out
