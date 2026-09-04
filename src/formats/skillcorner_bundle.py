"""SkillCorner multi-artifact bundle reader (Soccermatics-course distribution).

Pure, I/O-light reader for the owner-tier restricted SkillCorner Real Madrid data.
Parses ONLY the small ``meta/*.json``; the large tracking/events/freeze/physical
bodies are never read here (the adapter stages them as-is). See
docs/superpowers/specs/2026-06-29-skillcorner-restricted-realmadrid-owner-tier-design.md.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_MADRID = ZoneInfo("Europe/Madrid")

# (role_key, source_subdir, source_ext, staged_filename)
# upload_game derives the artifact key from the staged filename's stem
# (name.split(".", 1)[0]): tracking.json.gz -> "tracking", events.parquet -> "events".
ARTIFACT_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("tracking", "tracking", ".json", "tracking.json.gz"),
    ("events", "dynamic", ".parquet", "events.parquet"),
    ("freeze_frames", "freeze", ".parquet", "freeze_frames.parquet"),
    ("metadata", "meta", ".json", "metadata.json"),
    ("physical", "physical", ".parquet", "physical.parquet"),
)


@dataclass(frozen=True)
class MatchInfo:
    """Index metadata derived from one meta JSON."""

    match_id: str
    date: str  # YYYY-MM-DD, local (Europe/Madrid) match date
    home: str
    away: str


def local_match_date(timestamp: str) -> str:
    """ISO date (YYYY-MM-DD) of a tz-aware timestamp, in Europe/Madrid local time.

    meta.date_time is a tz-aware ISO 8601 value (e.g. ``...Z`` or ``+00:00``). The
    canonical match date is the *local* date, so convert to Europe/Madrid before
    taking the date component. Mirrors formats/idsse.py:local_match_date.
    """
    dt = datetime.fromisoformat(timestamp)
    if dt.tzinfo is None:
        raise ValueError(f"date_time {timestamp!r} is not timezone-aware")
    return dt.astimezone(_MADRID).date().isoformat()


def _team_label(team: dict | None) -> str | None:
    if not team:
        return None
    return team.get("short_name") or team.get("name")


def match_info(meta: dict) -> MatchInfo:
    """Derive index metadata from a loaded meta dict. Raises on missing fields."""
    match_id = meta.get("id")
    home = _team_label(meta.get("home_team"))
    away = _team_label(meta.get("away_team"))
    timestamp = meta.get("date_time")
    if match_id is None or not home or not away or not timestamp:
        raise ValueError("missing required meta fields (need id, date_time, home_team, away_team)")
    return MatchInfo(match_id=str(match_id), date=local_match_date(timestamp), home=home, away=away)


def load_meta(path: Path) -> dict:
    """Load a meta JSON file into a dict."""
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def discover_matches(root: Path) -> list[str]:
    """Return sorted match ids (the stems of meta/*.json) under the bundle root."""
    meta_dir = root / "meta"
    if not meta_dir.is_dir():
        raise FileNotFoundError(f"no meta/ directory under {root}")
    return sorted(p.stem for p in meta_dir.glob("*.json"))


def source_files(root: Path, match_id: str) -> dict[str, Path]:
    """Map each role key to its expected source-file Path (existence not checked)."""
    return {role: root / subdir / f"{match_id}{ext}" for role, subdir, ext, _staged in ARTIFACT_SPECS}


def missing_artifacts(root: Path, match_id: str) -> list[str]:
    """Return the role keys whose source file is absent for this match."""
    return [role for role, path in source_files(root, match_id).items() if not path.is_file()]


def is_complete(root: Path, match_id: str) -> bool:
    """True if all 5 ingested artifacts are present for this match."""
    return not missing_artifacts(root, match_id)


# Roles required for a canonical parquet-family ingest (RM + PL 24/25). `physical` is optional
# and `freeze` is dropped from the canonical output, so neither forces incompleteness. This is
# ADDITIVE (spec §6.2): ARTIFACT_SPECS / source_files / missing_artifacts / is_complete describe
# the 5-role *source* bundle and are left unchanged.
REQUIRED_ROLES = {"metadata", "tracking", "events"}


def missing_required(root: Path, match_id: str) -> list[str]:
    """Required roles whose source file is absent. physical/freeze are optional and ignored."""
    files = source_files(root, match_id)
    return sorted(role for role in REQUIRED_ROLES if not files[role].is_file())


# role -> (source subdir, extension) for the parquet-processed family (RealMadrid24-25,
# PremierLeague24-25). `events` is the already-Parquet dynamic/*.parquet (conformed to the pinned
# reference downstream); `physical` is per-match Parquet and OPTIONAL (PL 24/25 ships none);
# `freeze` is intentionally excluded — dropped from the canonical output (ADR 0011).
PARQUET_ROLE_LAYOUT: dict[str, tuple[str, str]] = {
    "metadata": ("meta", ".json"),
    "tracking": ("tracking", ".json"),
    "events": ("dynamic", ".parquet"),
    "physical": ("physical", ".parquet"),
}


def parquet_role_files(match_id: str) -> dict[str, str]:
    """Map each per-match role to its source-relative path in the parquet-processed layout."""
    return {role: f"{sub}/{match_id}{ext}" for role, (sub, ext) in PARQUET_ROLE_LAYOUT.items()}


def partition_ingestible(
    match_ids: list[str],
    role_size: Callable[[str, str], int | None],
) -> tuple[list[str], dict[str, str]]:
    """Split candidate match ids into (ingestible, {skipped_id: reason}).

    A match is ingestible iff every REQUIRED_ROLE (metadata/tracking/events) is present and its
    tracking body is non-empty. ``role_size(match_id, role)`` returns the source byte size, or None
    if that role's file is absent. Source-family agnostic — the caller supplies role_size from a
    raw-JSON or parquet-family file inventory — so both ingest adapters skip defective matches up
    front (Champions League zero-byte tracking / missing events, PL 24/25 events-less matches)
    instead of crashing mid-ingest. `physical`/`freeze` are never required and never cause a skip.
    """
    good: list[str] = []
    skipped: dict[str, str] = {}
    for mid in match_ids:
        sizes = {role: role_size(mid, role) for role in sorted(REQUIRED_ROLES)}
        absent = sorted(role for role, size in sizes.items() if size is None)
        if absent:
            skipped[mid] = f"missing {absent}"
        elif sizes["tracking"] == 0:
            skipped[mid] = "tracking empty (0 bytes)"
        else:
            good.append(mid)
    return good, skipped


def players_from_meta(meta: dict) -> list[dict]:
    """Derive canonical PlayerRecord dicts (without visibility/updated_at) from meta.players.

    meta.players is the self-contained matchday squad list; each entry already
    carries names, birthday, and player_role (position). Empty strings are
    normalised to None so the PlayerRecord ``nickname OR (firstName AND lastName)``
    validator behaves predictably.
    """
    records: list[dict] = []
    for p in meta.get("players", []):
        pid = p.get("id")
        if pid is None:
            continue
        role = p.get("player_role") or {}
        records.append(
            {
                "id": str(pid),
                "firstName": p.get("first_name") or None,
                "lastName": p.get("last_name") or None,
                "nickname": p.get("short_name") or None,
                "dob": p.get("birthday") or None,
                "position": role.get("name") or None,
                "positionGroupType": role.get("position_group") or None,
            }
        )
    return records
