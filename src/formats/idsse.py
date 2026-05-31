"""IDSSE/Sportec (DFL) open Bundesliga format — matchinformation XML reader.

Redistributed as-is (raw DFL XML); see
docs/superpowers/specs/2026-05-29-idsse-bundesliga-redistribution-design.md.

This module parses ONLY the small (~12 KB) matchinformation XML to derive index
metadata for matches.json. The positions/events XML are served byte-for-byte and
are never parsed here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

# figshare `name` fields carry hyphenated DFL ids, e.g. DFL-MAT-J03WN1.
_MATCH_ID_RE = re.compile(r"DFL-MAT-[A-Z0-9]+")

# DFL filename role marker -> role-aligned artifact key (Gradient Sports vocabulary).
# Markers are mutually exclusive across the three DFL file types.
_FILE_ROLE_MARKERS: tuple[tuple[str, str], ...] = (
    ("matchinformation", "metadata"),
    ("positions_raw_observed", "tracking"),
    ("events_raw", "events"),
)

REQUIRED_ARTIFACTS: tuple[str, ...] = ("metadata", "events", "tracking")

_BERLIN = ZoneInfo("Europe/Berlin")


@dataclass(frozen=True)
class MatchInfo:
    """Index metadata derived from one matchinformation XML."""

    match_id: str
    date: str  # YYYY-MM-DD, local (Europe/Berlin) match date
    home: str
    away: str


def match_id_from_filename(filename: str) -> str | None:
    """Return the DFL-MAT-… id embedded in a figshare filename, or None."""
    m = _MATCH_ID_RE.search(filename)
    return m.group(0) if m else None


def artifact_key_for_filename(filename: str) -> str | None:
    """Map a DFL filename to its role-aligned artifact key, or None if unrecognized."""
    for marker, key in _FILE_ROLE_MARKERS:
        if marker in filename:
            return key
    return None


def group_files_by_match(filenames: list[str]) -> dict[str, dict[str, str]]:
    """Group filenames into {match_id: {artifact_key: filename}}.

    Files without a recognizable match id AND role marker are skipped.
    """
    groups: dict[str, dict[str, str]] = {}
    for name in filenames:
        match_id = match_id_from_filename(name)
        key = artifact_key_for_filename(name)
        if match_id is None or key is None:
            continue
        groups.setdefault(match_id, {})[key] = name
    return groups


def is_complete(group: dict[str, str]) -> bool:
    """True if a group has all three required artifacts."""
    return all(k in group for k in REQUIRED_ARTIFACTS)


def local_match_date(kickoff: str) -> str:
    """ISO date (YYYY-MM-DD) of a tz-aware kickoff timestamp, in Europe/Berlin local time.

    DFL stores KickoffTime as a tz-aware ISO 8601 value (e.g. with a +00:00 UTC
    offset). The canonical match date is the *local* date, so we convert to
    Europe/Berlin before taking the date component.
    """
    dt = datetime.fromisoformat(kickoff)
    if dt.tzinfo is None:
        raise ValueError(f"KickoffTime {kickoff!r} is not timezone-aware")
    return dt.astimezone(_BERLIN).date().isoformat()


def read_match_information(path: Path) -> MatchInfo:
    """Parse a DFL matchinformation XML into index metadata.

    Schema (namespace-free): PutDataRequest > MatchInformation > General, with
    attributes MatchId, KickoffTime (tz-aware) / PlannedKickoffTime fallback,
    HomeTeamName, GuestTeamName.
    """
    # S314: input is the operator-fetched, md5-pinned figshare matchinformation file
    # (trusted CC-BY source), parsed only in ops tooling/tests — never user input.
    general = ET.parse(path).getroot().find("./MatchInformation/General")  # noqa: S314
    if general is None:
        raise ValueError(f"{path}: no MatchInformation/General element")

    match_id = general.get("MatchId")
    home = general.get("HomeTeamName")
    away = general.get("GuestTeamName")
    kickoff = general.get("KickoffTime") or general.get("PlannedKickoffTime")
    if not (match_id and home and away and kickoff):
        raise ValueError(f"{path}: missing required General attributes")

    return MatchInfo(match_id=match_id, date=local_match_date(kickoff), home=home, away=away)
