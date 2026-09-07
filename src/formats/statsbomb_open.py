"""StatsBomb open-data tournament reader (second source family).

Pure transforms over StatsBomb's real open-data feed: reshape the already-nested match
object into the canonical metadata schema, select 360-available matches, and partition
players against a known-id set. No I/O — the upload script fetches and stages.

The open feed is the real published format, so unlike formats/statsbomb.py there is no
de-pivot, no competition join, and no team-id resolution. See
docs/superpowers/specs/2026-09-06-statsbomb-open-tournaments-360-owner-tier-design.md.
"""

from __future__ import annotations

from formats.statsbomb import _int_or_none

# Six complete-tournament open-data competitions (public identifiers — spec D-6).
TOURNAMENTS: tuple[tuple[int, int], ...] = (
    (43, 106),  # FIFA World Cup 2022 (M)
    (72, 107),  # Women's World Cup 2023 (F)
    (55, 43),  # UEFA Euro 2020 (M)
    (55, 282),  # UEFA Euro 2024 (M)
    (53, 106),  # UEFA Women's Euro 2022 (F)
    (53, 315),  # UEFA Women's Euro 2025 (F)
)

# (role, source_filename, staged_filename). Source names are what the open assembly
# writes into the bundle dir; the 360 source is `three-sixty.json`, not `frames.json`.
OPEN_ARTIFACT_SPECS: tuple[tuple[str, str, str], ...] = (
    ("events", "events.json", "events.json.gz"),
    ("freeze_frames", "three-sixty.json", "freeze_frames.json.gz"),
    ("roster", "lineups.json", "roster.json"),
)


def build_metadata_open(match: dict) -> dict:
    """Reshape an open-data match object into the canonical metadata schema (spec §4.1).

    The open feed is already nested and richer than the commercial reconstruction; this
    whitelists it into the SAME key set build_metadata() emits, preserving the richer
    values and nulling the credentialed-only fields the open catalogue does not carry.
    Values are never invented (ADR 0010 D-4).
    """
    home = match.get("home_team") or {}
    away = match.get("away_team") or {}
    competition = match.get("competition") or {}
    season = match.get("season") or {}
    stage = match.get("competition_stage") or {}
    stadium = match.get("stadium") or {}
    referee = match.get("referee") or {}
    meta = match.get("metadata") or {}
    return {
        "match_id": _int_or_none(match.get("match_id")),
        "match_date": match.get("match_date"),
        "kick_off": match.get("kick_off"),
        "competition": {
            "competition_id": competition.get("competition_id"),
            "country_name": competition.get("country_name"),
            "competition_name": competition.get("competition_name"),
        },
        "season": {"season_id": season.get("season_id"), "season_name": season.get("season_name")},
        "home_team": {
            "home_team_id": home.get("home_team_id"),
            "home_team_name": home.get("home_team_name"),
            "home_team_gender": home.get("home_team_gender"),
            "home_team_group": home.get("home_team_group"),
            "country": home.get("country"),
            "managers": home.get("managers") or [],
        },
        "away_team": {
            "away_team_id": away.get("away_team_id"),
            "away_team_name": away.get("away_team_name"),
            "away_team_gender": away.get("away_team_gender"),
            "away_team_group": away.get("away_team_group"),
            "country": away.get("country"),
            "managers": away.get("managers") or [],
        },
        "home_score": _int_or_none(match.get("home_score")),
        "away_score": _int_or_none(match.get("away_score")),
        "match_status": match.get("match_status"),
        "match_status_360": match.get("match_status_360"),
        "last_updated": match.get("last_updated"),
        "last_updated_360": match.get("last_updated_360"),
        "metadata": {
            "data_version": meta.get("data_version"),
            "shot_fidelity_version": meta.get("shot_fidelity_version"),
            "xy_fidelity_version": meta.get("xy_fidelity_version"),
        },
        "match_week": _int_or_none(match.get("match_week")),
        "competition_stage": {"id": stage.get("id"), "name": stage.get("name")},
        "stadium": {"id": stadium.get("id"), "name": stadium.get("name"), "country": stadium.get("country")},
        "referee": {"id": referee.get("id"), "name": referee.get("name"), "country": referee.get("country")},
        "attendance": match.get("attendance"),
        "behind_closed_doors": match.get("behind_closed_doors"),
        "neutral_ground": match.get("neutral_ground"),
        "collection_status": match.get("collection_status"),
        "play_status": match.get("play_status"),
    }


def select_ingestible_matches(season_matches: list[dict], competition_id: int) -> list[dict]:
    """Return the 360-available matches, asserting they belong to `competition_id`.

    Transposed-pair guard (spec §6.4): season_id is per-competition, so a swapped
    (competition_id, season_id) pair fetches a real-but-wrong tournament. The open match
    object carries competition.competition_id inline, so a mismatch fails loud here
    rather than silently ingesting the wrong competition.
    """
    ingestible: list[dict] = []
    for match in season_matches:
        got = (match.get("competition") or {}).get("competition_id")
        if got != competition_id:
            raise ValueError(
                f"match {match.get('match_id')!r} carries competition_id {got!r}, "
                f"requested {competition_id!r} — transposed (competition_id, season_id) pair"
            )
        if match.get("match_status_360") == "available":
            ingestible.append(match)
    return ingestible


def partition_new_players(players: list[dict], known_ids: set[str]) -> tuple[list[dict], list[str]]:
    """Split players into (new, skipped-ids) against known_ids (spec §5.2).

    The open (sparser) source yields to any id already present so it never clobbers a
    richer commercial record. Does not mutate known_ids — the caller updates it so the
    skip sees intra-run adds.
    """
    new: list[dict] = []
    skipped: list[str] = []
    for player in players:
        pid = player["id"]
        if pid in known_ids:
            skipped.append(pid)
        else:
            new.append(player)
    return new, skipped
