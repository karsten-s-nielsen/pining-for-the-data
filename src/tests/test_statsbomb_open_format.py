"""Tests for formats/statsbomb_open.py (pure reader)."""

from __future__ import annotations

import pytest

from formats import statsbomb as sb
from formats import statsbomb_open as sbo


def _key_shape(d: dict) -> dict:
    """Recursive top-level+one-level key structure, for schema comparison."""
    return {k: (sorted(v.keys()) if isinstance(v, dict) else None) for k, v in d.items()}


def test_metadata_key_set_matches_commercial(sbo_match, sb_match_row, sb_competition_row) -> None:
    commercial = sb.build_metadata(sb_match_row, sb_competition_row, 90001, 90007, "female", "female")
    open_md = sbo.build_metadata_open(sbo_match)
    assert _key_shape(open_md) == _key_shape(commercial)


def test_richer_fields_all_preserved(sbo_match) -> None:
    md = sbo.build_metadata_open(sbo_match)
    # every slot build_metadata hard-nulls, populated from the open feed (SBO-SPEC-01)
    assert md["competition_stage"]["id"] == 10
    assert md["stadium"]["id"] == 4001
    assert md["stadium"]["country"] == {"id": 900, "name": "Northmoor"}
    assert md["referee"]["id"] == 3001
    assert md["referee"]["country"] == {"id": 902, "name": "Elsewhere"}
    assert md["home_team"]["home_team_group"] == "A"
    assert md["home_team"]["country"] == {"id": 900, "name": "Northmoor"}
    assert md["home_team"]["managers"][0]["dob"] == "1970-01-01"
    assert md["home_team"]["managers"][0]["id"] == 5001
    # away path is a structurally identical passthrough — assert it too
    assert md["away_team"]["country"] == {"id": 901, "name": "Southford"}
    assert md["away_team"]["managers"][0]["dob"] == "1972-02-02"


def test_metadata_triplet_read_from_nested_object(sbo_match) -> None:
    md = sbo.build_metadata_open(sbo_match)
    assert md["metadata"] == {"data_version": "1.1.0", "shot_fidelity_version": "2", "xy_fidelity_version": "2"}


def test_credentialed_fields_are_null(sbo_match) -> None:
    md = sbo.build_metadata_open(sbo_match)
    for field in ("attendance", "behind_closed_doors", "neutral_ground", "collection_status", "play_status"):
        assert md[field] is None


def test_team_ids_read_from_match_object(sbo_match) -> None:
    md = sbo.build_metadata_open(sbo_match)
    assert md["home_team"]["home_team_id"] == 60001
    assert md["away_team"]["away_team_id"] == 60002


def test_gender_from_match_object(sbo_match) -> None:
    md = sbo.build_metadata_open(sbo_match)
    assert md["home_team"]["home_team_gender"] == "male"
    assert md["away_team"]["away_team_gender"] == "male"


def test_tournaments_constant_is_the_six_documented_pairs() -> None:
    assert sbo.TOURNAMENTS == ((43, 106), (72, 107), (55, 43), (55, 282), (53, 106), (53, 315))


def test_select_keeps_only_360_available(sbo_season_matches) -> None:
    got = sbo.select_ingestible_matches(sbo_season_matches, 555555)
    assert [m["match_id"] for m in got] == [8888888]


def test_select_raises_on_transposed_pair(sbo_season_matches) -> None:
    # request competition 111111 but the fetched matches carry 555555 (both invented)
    with pytest.raises(ValueError, match="competition_id"):
        sbo.select_ingestible_matches(sbo_season_matches, 111111)


def test_partition_splits_new_from_known() -> None:
    players = [{"id": "70001"}, {"id": "70002"}, {"id": "70003"}]
    known = {"70002"}
    new, skipped = sbo.partition_new_players(players, known)
    assert [p["id"] for p in new] == ["70001", "70003"]
    assert skipped == ["70002"]
    assert known == {"70002"}  # partition must NOT mutate known_ids (the caller owns updates)


def test_open_players_have_null_dob_height_and_unsplit_name(sbo_lineups) -> None:
    # open lineups lack birth_date / player_height → those fields are null; an empty
    # player_nickname falls back to the full name unsplit (no firstName/lastName)
    records = sb.players_from_lineups(sbo_lineups)
    by_id = {r["id"]: r for r in records}
    assert all(r.get("dob") is None and r.get("height") is None for r in records)
    assert by_id["70002"]["nickname"] == "Player Two"  # empty nickname → full name
    assert "firstName" not in by_id["70002"] and "lastName" not in by_id["70002"]
    assert by_id["70001"]["nickname"] == "P1"  # present nickname kept


def test_open_match_missing_date_raises(sbo_match) -> None:
    m = {**sbo_match, "match_date": None}
    with pytest.raises(ValueError, match="match_date"):
        sb.match_info(sbo.build_metadata_open(m))


def test_open_emitted_ids_are_str(sbo_match, sbo_lineups) -> None:
    info = sb.match_info(sbo.build_metadata_open(sbo_match))
    assert isinstance(info.match_id, str)
    assert all(isinstance(r["id"], str) for r in sb.players_from_lineups(sbo_lineups))


def test_open_skip_is_order_independent_never_reemits_known() -> None:
    # order-independence (open half): a shared id already in the catalogue is never re-emitted,
    # so a prior (richer, e.g. commercial) record is never overwritten regardless of upload order
    players = [{"id": "shared"}, {"id": "fresh"}]
    new, skipped = sbo.partition_new_players(players, {"shared"})
    assert [p["id"] for p in new] == ["fresh"]
    assert skipped == ["shared"]
