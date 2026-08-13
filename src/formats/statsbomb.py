"""StatsBomb commercial 360 bundle reader (club file-drop distribution).

Pure, I/O-light reader for the owner-tier restricted StatsBomb data. Turns the
delivered archive into canonical dicts: detects and de-pivots pandas
column-orient dumps, joins the competition row, resolves team ids from events,
and re-nests the match record into StatsBomb's real feed shape.

Structure may be reconstructed; values are never invented. See
docs/superpowers/specs/2026-08-12-statsbomb-commercial-360-owner-tier-design.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

# Source filenames as delivered.
SOURCE_FILES: dict[str, str] = {
    "events": "events.json",
    "frames": "frames.json",
    "lineups": "lineups.json",
    "matches": "matches.json",
    "competitions": "competitions.json",
}

# (role_key, source_key, staged_filename) for artifacts copied verbatim.
# `metadata` is NOT here — it is synthesised by build_metadata().
# upload_game derives the artifact key from the staged stem (name.split(".", 1)[0]).
_ARTIFACT_ROLES: tuple[tuple[str, str, str], ...] = (
    ("events", "events", "events.json.gz"),
    ("freeze_frames", "frames", "freeze_frames.json.gz"),
    ("roster", "lineups", "roster.json"),
)

# (role_key, source_filename, staged_filename). The source filename is resolved
# through SOURCE_FILES so a rename cannot half-apply.
ARTIFACT_SPECS: tuple[tuple[str, str, str], ...] = tuple(
    (role, SOURCE_FILES[source_key], staged) for role, source_key, staged in _ARTIFACT_ROLES
)

STAGED_METADATA_FILENAME = "metadata.json"

_INT_INDEX_RE = re.compile(r"^-?\d+$")


def _reject_constant(token: str) -> NoReturn:
    """Refuse bare NaN/Infinity: valid to Python's json, rejected by JSON.parse."""
    raise ValueError(
        f"non-finite JSON token {token!r} in payload — a strict JSON parser would reject "
        f"the served artifact. Normalise it to null at the producer."
    )


def load_json(path: Path) -> Any:
    """Load JSON, refusing bare NaN/Infinity tokens (spec §4.1)."""
    with path.open(encoding="utf-8") as f:
        return json.load(f, parse_constant=_reject_constant)


def is_column_orient(payload: object) -> bool:
    """True if `payload` is a pandas column-orient dump (spec §4.1).

    ALL of: an object, >= 2 keys, every value an object, all values sharing one
    identical non-empty key set, and every key of that set an integer-like string
    (the DataFrame index). The integer-index requirement is what makes this a rule
    rather than an accident — without it, an object whose values happen to be
    uniformly-shaped sub-objects would be transposed into garbage.
    """
    if not isinstance(payload, dict) or len(payload) < 2:
        return False
    key_sets: list[frozenset[str]] = []
    for value in payload.values():
        if not isinstance(value, dict):
            return False
        key_sets.append(frozenset(value))
    shared = key_sets[0]
    if not shared or any(ks != shared for ks in key_sets):
        return False
    return all(isinstance(k, str) and _INT_INDEX_RE.match(k) for k in shared)


def _require_key(records: list[dict], expected_key: str) -> list[dict]:
    """Post-condition: every record carries the key its file is keyed on, as a scalar id.

    Applied on EVERY branch of normalise_records, not just the de-pivot. §4.1's
    durability claim is about the raw path, so leaving that path unchecked would
    put the post-condition everywhere except where it is most needed.

    An explicitly-null id is treated as missing. This is deliberate: a nulled id
    does not identify a record, so there is nothing useful to distinguish it from
    an absent one.

    The value must also be a scalar. A still-pivoted single-column dump slips past
    is_column_orient (which requires >= 2 columns), as does a ragged dump whose
    columns disagree on their key sets; without this check either would be waved
    through the single-raw-object branch carrying a nested index dict as its id.
    """
    for record in records:
        if not isinstance(record, dict) or record.get(expected_key) is None:
            raise ValueError(
                f"record is missing required key {expected_key!r} — the payload is not the file this reader expected"
            )
        value = record[expected_key]
        if isinstance(value, (dict, list)):
            raise ValueError(
                f"record key {expected_key!r} is a {type(value).__name__} where a scalar id was expected — "
                f"the payload is not the file this reader expected"
            )
    return records


def depivot(payload: dict, expected_key: str) -> list[dict]:
    """Transpose a column-orient dump back to records. Lossless: only the container changes."""
    if not is_column_orient(payload):
        raise ValueError("payload is not a column-orient dump")
    index = sorted(next(iter(payload.values())).keys(), key=int)
    records = [{col: values.get(i) for col, values in payload.items()} for i in index]
    return _require_key(records, expected_key)


def normalise_records(payload: object, expected_key: str) -> list[dict]:
    """Return records from a feed array, a single raw object, or a column-orient dump.

    The durable entry point: a future fully-raw delivery flows through here with no
    flag and no edit.
    """
    if isinstance(payload, list):
        return _require_key(payload, expected_key)
    if isinstance(payload, dict):
        if is_column_orient(payload):
            return depivot(payload, expected_key)
        if expected_key in payload:
            return _require_key([payload], expected_key)
    raise ValueError(f"unrecognised payload shape for a file keyed on {expected_key!r}")


def _rendered_competition(row: dict) -> str:
    """statsbombpy renders the competition column as "{country_name} - {competition_name}"."""
    return f"{row.get('country_name')} - {row.get('competition_name')}"


def join_competition(competitions: list[dict], match: dict) -> dict:
    """Return the single competitions row belonging to `match` (spec §4.2.1).

    The match row carries no competition_id/season_id — that is precisely why the
    competitions file is needed — so the join is on statsbombpy's rendered
    "{country} - {competition}" string plus the season name.

    This key is a TOOLING CONVENTION, not a feed contract. If a future delivery
    stops matching, re-derive the key. NEVER loosen the comparison: substring,
    case-insensitive or fuzzy matching converts a fail-loud stop into a silent
    wrong-competition attachment, which is the exact failure this guard prevents.
    """
    want_competition = match.get("competition")
    want_season = match.get("season")
    if not want_competition or want_season is None:
        raise ValueError("match row lacks 'competition' and/or 'season' — cannot join the competitions file")

    hits = [
        row
        for row in competitions
        if _rendered_competition(row) == want_competition and str(row.get("season_name")) == str(want_season)
    ]
    if len(hits) != 1:
        raise ValueError(
            f"competition join matched {len(hits)} rows for "
            f"{want_competition!r} / season {want_season!r}; expected exactly 1. "
            f"Re-derive the join key — do not loosen the comparison."
        )
    return hits[0]


def resolve_team_ids(events: list[dict], match: dict) -> tuple[int, int]:
    """Resolve (home_team_id, away_team_id) from events by EXACT name equality (spec §4.2.2).

    An inverted fixture is strictly worse than a null: it is indistinguishable from
    correct downstream and silently corrupts every home/away-dependent computation.
    So: exactly one id per side, and the two sides must differ — anything else raises.

    No fuzzy or normalised matching. A near-match is exactly the case that should stop
    the upload rather than guess. Lineup ordering is NOT a fallback: the delivered
    lineups list the away team first, so ordering carries no home/away signal.
    """
    by_name: dict[str, set[int]] = {}
    for event in events:
        team = event.get("team")
        if isinstance(team, dict) and team.get("name") is not None and team.get("id") is not None:
            by_name.setdefault(team["name"], set()).add(team["id"])

    resolved: list[int] = []
    for side in ("home_team", "away_team"):
        name = match.get(side)
        ids = by_name.get(name, set()) if name is not None else set()
        if len(ids) != 1:
            raise ValueError(f"{side} {name!r} resolved to {len(ids)} team id(s) in events; expected exactly 1")
        resolved.append(next(iter(ids)))

    if resolved[0] == resolved[1]:
        raise ValueError(f"home and away resolved to the same team id ({resolved[0]})")
    return resolved[0], resolved[1]


def team_gender(lineups: list[dict], team_id: int) -> str | None:
    """The single gender shared by one team's players, else None.

    StatsBomb records gender per player and the match object carries it PER TEAM,
    so this resolves per team rather than delivery-wide. A team whose players
    disagree — or an unknown team — yields None rather than a guess.
    """
    genders = {
        player.get("player_gender")
        for team in lineups
        if team.get("team_id") == team_id
        for player in team.get("lineup", [])
        if player.get("player_gender")
    }
    return next(iter(genders)) if len(genders) == 1 else None


@dataclass(frozen=True)
class MatchInfo:
    """Index metadata for one match (spec §4.5)."""

    match_id: str
    date: str
    home: str
    away: str


def _int_or_none(value: float | None) -> int | None:
    """Cast a numeric to int, preserving None (spec §4.2.3).

    Applied to `match_id`, `match_week` and both scores — all integers in the feed,
    all delivered as floats because one null upcasts a whole pandas column to float64.

    The `match_id` case is load-bearing: match_info stringifies it into MatchEntry.id,
    and `"9999999.0"` fails that field's path-param regex at upload time.
    """
    return None if value is None else int(value)


def _managers(name: object) -> list[dict]:
    """The feed's managers array. Only the name survived the export; the rest are null.

    A non-string cell (e.g. a list, if statsbombpy ever renders multiple managers)
    is refused rather than accepted: splitting it would invent a boundary the export
    does not assert (D-4), and accepting it silently would emit a compound name as a
    single manager.
    """
    if name is None or name == "":
        return []
    if not isinstance(name, str):
        raise ValueError(
            f"manager cell is {type(name).__name__}, expected a single name string — "
            f"a multi-manager cell cannot be split without inventing a boundary"
        )
    return [{"id": None, "name": name, "nickname": None, "dob": None, "country": None}]


def build_metadata(
    match: dict,
    competition: dict,
    home_id: int,
    away_id: int,
    home_gender: str | None,
    away_gender: str | None,
) -> dict:
    """Re-nest a flattened match row into StatsBomb's real match shape (spec §4.2.3).

    The real match object natively co-locates competition{} and season{}, which is
    what licenses pulling them from the competitions file — this restores what the
    feed contains, it does not invent a composite.

    Fields the export dropped are emitted as null. Nothing is looked up externally.
    """
    return {
        "match_id": _int_or_none(match.get("match_id")),
        "match_date": match.get("match_date"),
        "kick_off": match.get("kick_off"),
        "competition": {
            "competition_id": competition.get("competition_id"),
            "country_name": competition.get("country_name"),
            "competition_name": competition.get("competition_name"),
        },
        "season": {
            "season_id": competition.get("season_id"),
            "season_name": competition.get("season_name"),
        },
        "home_team": {
            "home_team_id": home_id,
            "home_team_name": match.get("home_team"),
            "home_team_gender": home_gender,
            "home_team_group": None,
            "country": None,
            "managers": _managers(match.get("home_managers")),
        },
        "away_team": {
            "away_team_id": away_id,
            "away_team_name": match.get("away_team"),
            "away_team_gender": away_gender,
            "away_team_group": None,
            "country": None,
            "managers": _managers(match.get("away_managers")),
        },
        "home_score": _int_or_none(match.get("home_score")),
        "away_score": _int_or_none(match.get("away_score")),
        "match_status": match.get("match_status"),
        "match_status_360": match.get("match_status_360"),
        "last_updated": match.get("last_updated"),
        "last_updated_360": match.get("last_updated_360"),
        "metadata": {
            "data_version": match.get("data_version"),
            "shot_fidelity_version": match.get("shot_fidelity_version"),
            "xy_fidelity_version": match.get("xy_fidelity_version"),
        },
        "match_week": _int_or_none(match.get("match_week")),
        "competition_stage": {"id": None, "name": match.get("competition_stage")},
        "stadium": {"id": None, "name": match.get("stadium"), "country": None},
        "referee": {"id": None, "name": match.get("referee"), "country": None},
        # Credentialed-endpoint fields the open catalogue does not carry. Genuine
        # feed fields, not export artifacts — preserved at top level.
        "attendance": match.get("attendance"),
        "behind_closed_doors": match.get("behind_closed_doors"),
        "neutral_ground": match.get("neutral_ground"),
        "collection_status": match.get("collection_status"),
        "play_status": match.get("play_status"),
    }


def match_info(metadata: dict) -> MatchInfo:
    """Derive the provider-index entry fields from a built metadata artifact.

    `date` is REQUIRED. apply_filters excludes an empty date from both range
    filters (shared.py:296,299), so a dateless entry is invisible to every
    dateFrom/dateTo query while still returning 200 on the unfiltered list.
    Refuse rather than upload a match that cannot be found by date.
    """
    match_id = metadata.get("match_id")
    date = metadata.get("match_date")
    home = (metadata.get("home_team") or {}).get("home_team_name")
    away = (metadata.get("away_team") or {}).get("away_team_name")

    if match_id is None:
        raise ValueError("metadata lacks match_id")
    if not date:
        raise ValueError("metadata lacks match_date — refusing to upload a match that cannot be found by date")
    if not home or not away:
        raise ValueError("metadata lacks home and/or away team name")
    return MatchInfo(match_id=str(match_id), date=date, home=home, away=away)


def _starting_position(player: dict) -> str | None:
    """The earliest positional spell by (from_period, from). Spec §5.2.

    positions[] is a per-spell array (up to 4 entries). The catalogue records the
    player's STARTING position; the full history stays in the roster artifact.

    A spell with no `from_period` raises rather than sorting to zero: treating a
    malformed spell as "earliest" would silently outrank a genuine period-1 spell,
    and a silently-wrong position is worse than a stop.
    """
    spells = player.get("positions") or []
    if not spells:
        return None
    for spell in spells:
        if spell.get("from_period") is None:
            raise ValueError(
                f"positional spell for player {player.get('player_id')!r} has no 'from_period' — "
                f"cannot order spells to find the starting position"
            )
    earliest = min(spells, key=lambda s: (s["from_period"], s.get("from") or ""))
    return earliest.get("position") or None


def players_from_lineups(lineups: list[dict]) -> list[dict]:
    """Derive canonical PlayerRecord dicts (without visibility/updated_at). Spec §5.1.

    StatsBomb's lineups feed carries NO first/last split — only `player_name` plus an
    often-empty `player_nickname`. Satisfying PlayerRecord's
    ``nickname OR (firstName AND lastName)`` validator via first/last would require
    splitting the full name on whitespace, which invents a boundary the feed does not
    assert and mangles compound surnames. D-4 forbids it, so the full name routes into
    `nickname` and firstName/lastName are never populated.

    Note: upload_players serialises with model_dump(exclude_none=True), so unset
    fields are ABSENT from the served players.json rather than null. A consumer must
    read absence — not a null value — as "this provider asserts no split".

    On the asymmetry with _starting_position: an entry with no `player_id` is skipped
    silently, whereas a positional spell with no `from_period` raises. Both are
    deliberate, and the difference is what a bad entry would *produce*. An id-less
    entry identifies no player, so there is no record to emit and nothing to get
    wrong; an unorderable spell would emit a WRONG position for a real player, which
    is indistinguishable from a right one downstream.
    """
    records: list[dict] = []
    for team in lineups:
        for player in team.get("lineup", []):
            player_id = player.get("player_id")
            if player_id is None:
                continue
            country = player.get("country") or {}
            records.append(
                {
                    "id": str(player_id),
                    "nickname": player.get("player_nickname") or player.get("player_name") or None,
                    "dob": player.get("birth_date") or None,
                    "height": player.get("player_height"),
                    "nationality": country.get("name") or None,
                    "position": _starting_position(player),
                }
            )
    return records


def assert_delivery_coherent(events: list[dict], frames: list[dict], lineups: list[dict]) -> None:
    """Fail loud on an incoherent delivery, BEFORE anything is staged (spec §7.1).

    The synthetic-fixture tests prove these helpers work; this run proves THIS
    delivery is coherent. Both are needed.

    A legitimately abnormal delivery (abandoned match, unusual period structure) is
    handled by amending the SPECIFIC assertion that no longer applies, with the
    reason recorded in the operator's delivery note. There is deliberately no
    --force flag: a blanket override is what gets reached for under time pressure,
    and it would disable every check at once.
    """
    if not frames:
        # Without this the orphan check below passes VACUOUSLY on an empty list,
        # and a 360 provider ships a match with no 360 data.
        raise ValueError("delivery contains zero freeze frames — 360 payload is empty or truncated")

    if not lineups:
        # Same vacuous-pass hazard, one file over: an empty lineups array satisfies
        # _require_array, stages an empty roster artifact, and skips the player
        # upload entirely — leaving an indexed match with no squad and no error.
        raise ValueError("delivery contains zero lineup entries — roster payload is empty or truncated")

    # The shape BETWEEN the guard above and the lineups-vs-events cross-check below:
    # both team blocks present but their squads empty. The outer array is non-empty,
    # and the team_ids are still there for the cross-check to agree on — so neither
    # guard fires, players_from_lineups returns [], upload_players is skipped by its
    # `if players:` guard, and the match is indexed with no catalogue and no error.
    # That is the very failure the zero-lineups comment above claims to close.
    empty_squads = sorted((team.get("team_id") for team in lineups if not team.get("lineup")), key=str)
    if empty_squads:
        raise ValueError(f"lineup(s) for team(s) {empty_squads} are empty — roster payload is truncated")

    # `None` must stay OUT of this set. An id-less event would otherwise put it in,
    # and a frame with no event_uuid would then match it and slip through as valid.
    event_ids = {event["id"] for event in events if event.get("id") is not None}
    orphans = [f.get("event_uuid") for f in frames if f.get("event_uuid") not in event_ids]
    if orphans:
        raise ValueError(
            f"{len(orphans)} freeze frame(s) reference unknown event ids, e.g. {orphans[:3]} — "
            f"events and frames are not from the same export"
        )

    periods = {event.get("period") for event in events if event.get("period") is not None}
    if len(periods) < 2:
        raise ValueError(f"events cover {len(periods)} period(s); expected at least 2")

    if not any((event.get("type") or {}).get("name") == "Half End" for event in events):
        raise ValueError("no 'Half End' event — the delivery looks truncated")

    team_ids = {
        event["team"]["id"]
        for event in events
        if isinstance(event.get("team"), dict) and event["team"].get("id") is not None
    }
    if len(team_ids) != 2:
        raise ValueError(f"events contain {len(team_ids)} distinct team(s); expected exactly 2")

    # A contradiction INSIDE the delivery, which ADR 0010 rule 3 says is exactly the
    # kind that can be made to raise: one export of one fixture cannot legitimately
    # field one pair of teams in its events and a different pair in its lineups.
    lineup_team_ids = {team.get("team_id") for team in lineups if team.get("team_id") is not None}
    if lineup_team_ids != team_ids:
        raise ValueError(
            f"lineups cover team(s) {sorted(lineup_team_ids, key=str)} but events cover "
            f"{sorted(team_ids, key=str)} — the delivery contradicts itself"
        )


@dataclass(frozen=True)
class Bundle:
    """One delivered match: parsed, de-pivoted and joined. No artifact bodies are transformed."""

    root: Path
    match: dict
    competition: dict
    events: list[dict]
    frames: list[dict]
    lineups: list[dict]


def _require_array(root: Path, source_key: str) -> list[dict]:
    """Load a passthrough file that must already be a feed array OF OBJECTS.

    events/frames/lineups are staged verbatim, so they get no normalise_records
    treatment — but they still need a stated failure. Without this, a malformed
    file surfaces downstream as ``AttributeError: 'str' object has no attribute
    'get'`` instead of naming the file and the expectation.

    The element check is what actually closes that gap. A container check alone
    waves ``["a", "b"]`` through — it IS a list — and the AttributeError still
    happens, just later, in assert_delivery_coherent. So the offending index is
    named here, where the file name is still in hand.
    """
    name = SOURCE_FILES[source_key]
    payload = load_json(root / name)
    if not isinstance(payload, list):
        raise ValueError(f"{name} is a {type(payload).__name__}, expected a JSON array")
    for index, record in enumerate(payload):
        if not isinstance(record, dict):
            raise ValueError(f"{name}[{index}] is a {type(record).__name__}, expected a JSON object")
    return payload


def read_bundle(root: Path) -> Bundle:
    """Load and normalise one delivered bundle directory.

    The match id is discovered from matches.json rather than passed in, so a second
    delivery is a re-run rather than a rewrite.
    """
    missing = [name for name in SOURCE_FILES.values() if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError(f"bundle at {root} is missing {missing}")

    matches = normalise_records(load_json(root / SOURCE_FILES["matches"]), "match_id")
    if len(matches) != 1:
        raise ValueError(
            f"matches.json holds {len(matches)} rows; this reader ingests one match per bundle "
            f"(events/frames cover a single fixture, so a multi-row dump is ambiguous)"
        )
    competitions = normalise_records(load_json(root / SOURCE_FILES["competitions"]), "competition_id")

    return Bundle(
        root=root,
        match=matches[0],
        competition=join_competition(competitions, matches[0]),
        events=_require_array(root, "events"),
        frames=_require_array(root, "frames"),
        lineups=_require_array(root, "lineups"),
    )
