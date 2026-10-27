"""Competition and season identity of a match, read verbatim from the provider's own metadata.

One set of value rules (spec §5.3) applies to every provider, so the upload path and the backfill
cannot disagree:

- an ``int`` id becomes its decimal string; a non-empty ``str`` id is kept verbatim (no stripping);
- ``None`` or ``""`` means the source gives no value, so the field stays absent (ADR 0010);
- any other type (``float``, ``bool``, ``list``, ``dict``) is refused loudly, because a coerced value
  would be an inferred one.

A field is filled only from the source path that holds it, in the role the source gives it: a name
is never copied into an id or an id into a name (D2).

Pure functions only (no I/O).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict


class CompetitionKwargs(TypedDict):
    """The four fields as ``upload_game`` keyword arguments (a TypedDict, so ``**`` unpacking type-checks)."""

    competition_id: str | None
    competition_name: str | None
    season_id: str | None
    season_name: str | None


@dataclass(frozen=True)
class CompetitionSeason:
    """The four index fields of one match. ``None`` means the source gives no value."""

    competition_id: str | None = None
    competition_name: str | None = None
    season_id: str | None = None
    season_name: str | None = None

    def as_kwargs(self) -> CompetitionKwargs:
        """The four fields as ``upload_game`` keyword arguments."""
        return CompetitionKwargs(
            competition_id=self.competition_id,
            competition_name=self.competition_name,
            season_id=self.season_id,
            season_name=self.season_name,
        )


def native_id(value: object, field: str) -> str | None:
    """Apply the §5.3 id rule to one raw source value. Raises ``ValueError`` naming ``field``."""
    if value is None or value == "":
        return None
    # bool is a subclass of int: refuse it before the int branch accepts True as "1".
    if isinstance(value, bool):
        raise ValueError(f"{field}: bool value {value!r} is not an id")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    raise ValueError(f"{field}: {type(value).__name__} value {value!r} is not an id (int or str only)")


def native_name(value: object, field: str) -> str | None:
    """Apply the §5.3 name rule to one raw source value. Raises ``ValueError`` naming ``field``."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value
    raise ValueError(f"{field}: {type(value).__name__} value {value!r} is not a name (str only)")


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def skillcorner_v3_competition_season(meta: Mapping[str, Any]) -> CompetitionSeason:
    """Read ``competition_edition.competition.{id,name}`` and ``competition_edition.season.{id,name}``.

    A missing ``competition_edition``, ``competition`` or ``season`` gives absent fields.
    """
    edition = _mapping(meta.get("competition_edition"))
    competition = _mapping(edition.get("competition"))
    season = _mapping(edition.get("season"))
    return CompetitionSeason(
        competition_id=native_id(competition.get("id"), "competition_id"),
        competition_name=native_name(competition.get("name"), "competition_name"),
        season_id=native_id(season.get("id"), "season_id"),
        season_name=native_name(season.get("name"), "season_name"),
    )
