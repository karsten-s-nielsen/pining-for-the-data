"""Gradient Sports match metadata — pure reader for one ``Metadata/{id}.json`` document.

The source wraps the match object in a one-element list. The reader returns the match id, the
index fields the loader has always written (``date``, ``home``, ``away``), and the
competition/season identity (spec §5.2):

- ``competition_id`` / ``competition_name`` <- ``competition.id`` / ``competition.name``;
- ``season_name`` <- ``season`` verbatim (for example ``"2022"``);
- ``season_id`` is never set: the source gives no season id, and its season value is a name (D3).

Pure functions only (no I/O): ``scripts/upload_gradient_wc2022.py`` reads the file.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from formats.competition_season import CompetitionSeason, native_id, native_name


@dataclass(frozen=True)
class MatchInfo:
    """Index metadata derived from one Gradient Sports metadata document."""

    match_id: str
    date: str  # YYYY-MM-DD prefix of the source ``date``; "" when the source gives none
    home: str
    away: str
    competition: CompetitionSeason = field(default_factory=CompetitionSeason)


def _unwrap(metadata_obj: list | dict) -> Mapping[str, Any]:
    if isinstance(metadata_obj, list):
        # Gradient Sports wraps the metadata in a single-element list.
        return metadata_obj[0] if metadata_obj else {}
    return metadata_obj


def match_info(metadata_obj: list | dict) -> MatchInfo:
    """Derive index metadata from a loaded metadata document. Raises when the match id is missing."""
    meta = _unwrap(metadata_obj)
    match_id = native_id(meta.get("id"), "match_id")
    if match_id is None:
        raise ValueError("Gradient Sports metadata lacks match_id ([0].id)")
    competition = meta.get("competition") or {}
    return MatchInfo(
        match_id=match_id,
        date=meta.get("date", "").split("T", 1)[0],
        home=(meta.get("homeTeam") or {}).get("name", ""),
        away=(meta.get("awayTeam") or {}).get("name", ""),
        competition=CompetitionSeason(
            competition_id=native_id(competition.get("id"), "competition_id"),
            competition_name=native_name(competition.get("name"), "competition_name"),
            season_id=None,
            season_name=native_name(meta.get("season"), "season_name"),
        ),
    )
