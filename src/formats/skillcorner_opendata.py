"""SkillCorner Open Data (github.com/SkillCorner/opendata) — pure discovery + role mapping.

The opendata match layout is the legacy id-prefixed PUBLIC artifact set the mock API already
serves for the original A-League drop: per match a SkillCorner V3 ``{id}_match.json`` metadata
file plus three bodies redistributed AS-IS — extrapolated tracking JSONL, dynamic-events CSV and
phases-of-play CSV. The metadata schema is the V3 shape, so
``formats.skillcorner_bundle.players_from_meta`` is reused unchanged by the adapter to derive
players.

Pure functions only (no I/O): ``scripts/upload_skillcorner_opendata.py`` injects the HTTP/S3
boundary. This is the public-tier peer of ``formats.skillcorner_raw`` (owner tier). It does NOT
reuse ``skillcorner_bundle.match_info`` because that converts the kickoff to Europe/Madrid local
time — correct for the owner-tier Real Madrid data but wrong for A-League (Australia/NZ) and, more
importantly, a mismatch with the UTC calendar date the original 10 public entries already carry.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# (role, filename-suffix) for the four opendata files per match. The staged filename keeps the
# id-prefixed opendata basename so ``upload_game`` derives the exact legacy artifact keys
# ({id}_match, {id}_tracking_extrapolated, {id}_dynamic_events, {id}_phases_of_play) already used
# by the original 10 public matches (ADR 0008: the legacy skillcorner provider uses id-prefixed
# keys; the wire format is out-of-band of the role key).
OPENDATA_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("match", "_match.json"),
    ("tracking_extrapolated", "_tracking_extrapolated.jsonl"),
    ("dynamic_events", "_dynamic_events.csv"),
    ("phases_of_play", "_phases_of_play.csv"),
)


@dataclass(frozen=True)
class OpenDataMatchInfo:
    """Index metadata derived from one opendata ``{id}_match.json``."""

    match_id: str
    date: str  # UTC calendar date (date_time[:10]) — matches the existing public entries
    home: str
    away: str


def opendata_files(match_id: str) -> dict[str, str]:
    """Map each role to its source path relative to the opendata ``data/`` root."""
    return {role: f"matches/{match_id}/{match_id}{suffix}" for role, suffix in OPENDATA_SUFFIXES}


def _sorted_ids(ids: Iterable[str]) -> list[str]:
    """Sort match ids numerically when all-numeric (SkillCorner ids), else lexicographically."""
    out = list(ids)
    return sorted(out, key=int) if all(s.isdigit() for s in out) else sorted(out)


def discover_match_ids(index: list[dict]) -> list[str]:
    """Sorted string match ids from the opendata ``data/matches.json`` index."""
    return _sorted_ids(str(m["id"]) for m in index)


def select_new_matches(all_ids: list[str], live_ids: set[str]) -> list[str]:
    """Sorted opendata ids not already present in the live index (idempotent skip)."""
    return _sorted_ids(set(all_ids) - set(live_ids))


def _team_label(team: dict | None) -> str | None:
    if not team:
        return None
    return team.get("short_name") or team.get("name")


def match_info(meta: dict) -> OpenDataMatchInfo:
    """Derive index metadata from an opendata ``{id}_match.json``. Raises on missing fields.

    ``date`` is the UTC calendar date (the ``date_time`` prefix), reproducing the convention of
    the original 10 public entries exactly — see the module docstring for why no timezone
    conversion is applied.
    """
    match_id = meta.get("id")
    home = _team_label(meta.get("home_team"))
    away = _team_label(meta.get("away_team"))
    date_time = meta.get("date_time")
    if match_id is None or not home or not away or not date_time:
        raise ValueError("missing required meta fields (need id, date_time, home_team, away_team)")
    if "T" not in date_time or len(date_time) < 10:
        raise ValueError(f"unexpected date_time format: {date_time!r}")
    return OpenDataMatchInfo(match_id=str(match_id), date=date_time[:10], home=home, away=away)
