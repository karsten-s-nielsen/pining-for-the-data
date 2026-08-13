"""Tests for src/formats/statsbomb.py (pure reader — no S3, no network)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from formats import statsbomb as sb


class TestShapeDetection:
    def test_column_orient_dump_is_detected(self) -> None:
        payload = {"match_id": {"7": 9999999}, "match_date": {"7": "2026-01-01"}}
        assert sb.is_column_orient(payload) is True

    def test_raw_feed_object_is_not_detected(self) -> None:
        # A real match object: values are scalars and mixed-shape dicts.
        payload = {"match_id": 9999999, "competition": {"competition_id": 1}}
        assert sb.is_column_orient(payload) is False

    def test_uniform_subobjects_with_non_integer_keys_are_not_detected(self) -> None:
        # THE false-positive case: every value is a same-keyed dict, but the
        # shared keys are field names, not a DataFrame index.
        payload = {
            "home_team": {"id": 1, "name": "Alpha"},
            "away_team": {"id": 2, "name": "Beta"},
        }
        assert sb.is_column_orient(payload) is False

    def test_single_column_is_not_detected(self) -> None:
        assert sb.is_column_orient({"match_id": {"7": 9999999}}) is False

    def test_list_is_not_detected(self) -> None:
        assert sb.is_column_orient([{"match_id": 9999999}]) is False


class TestDepivot:
    def test_single_row_yields_one_record_value_for_value(self) -> None:
        payload = {
            "match_id": {"7": 9999999},
            "match_date": {"7": "2026-01-01"},
            "home_score": {"7": 2.0},
        }
        assert sb.depivot(payload, "match_id") == [{"match_id": 9999999, "match_date": "2026-01-01", "home_score": 2.0}]

    def test_multiple_rows_are_ordered_by_integer_index(self) -> None:
        payload = {"competition_id": {"10": 50, "2": 49}, "season_name": {"10": "2027", "2": "2026"}}
        assert sb.depivot(payload, "competition_id") == [
            {"competition_id": 49, "season_name": "2026"},
            {"competition_id": 50, "season_name": "2027"},
        ]

    def test_missing_expected_key_raises(self) -> None:
        payload = {"foo": {"7": 1}, "bar": {"7": 2}}
        with pytest.raises(ValueError, match="match_id"):
            sb.depivot(payload, "match_id")


class TestNormaliseRecords:
    def test_feed_array_passes_through_unchanged(self) -> None:
        records = [{"match_id": 9999999, "competition": {"competition_id": 1}}]
        assert sb.normalise_records(records, "match_id") == records

    def test_feed_array_missing_expected_key_raises(self) -> None:
        # The RAW path is the one §4.1's durability claim is about, so it must
        # carry the post-condition too — not just the de-pivot branch.
        with pytest.raises(ValueError, match="match_id"):
            sb.normalise_records([{"competition": {"competition_id": 1}}], "match_id")

    def test_single_raw_object_is_wrapped(self) -> None:
        obj = {"match_id": 9999999, "competition": {"competition_id": 1}}
        assert sb.normalise_records(obj, "match_id") == [obj]

    def test_column_orient_is_transposed(self) -> None:
        payload = {"match_id": {"7": 9999999}, "match_date": {"7": "2026-01-01"}}
        assert sb.normalise_records(payload, "match_id") == [{"match_id": 9999999, "match_date": "2026-01-01"}]

    def test_unrecognised_object_raises(self) -> None:
        with pytest.raises(ValueError, match="unrecognised payload shape"):
            sb.normalise_records({"competition": {"competition_id": 1}}, "match_id")

    def test_pivot_shaped_single_column_object_raises(self) -> None:
        # is_column_orient rejects this (needs >= 2 keys), so it must not slip
        # through the single-raw-object branch with a nested index dict as its id.
        with pytest.raises(ValueError, match="match_id"):
            sb.normalise_records({"match_id": {"7": 9999999}}, "match_id")


class TestLoadJson:
    def test_bare_nan_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text('{"attendance": NaN}', encoding="utf-8")
        with pytest.raises(ValueError, match="non-finite"):
            sb.load_json(path)

    def test_null_is_fine(self, tmp_path: Path) -> None:
        path = tmp_path / "ok.json"
        path.write_text('{"attendance": null}', encoding="utf-8")
        assert sb.load_json(path) == {"attendance": None}


class TestArtifactSpecs:
    def test_roles_map_to_the_expected_source_and_staged_names(self) -> None:
        # Asserts the actual role -> (source, staged) contract, so a wrong
        # mapping fails even though the lookup itself cannot.
        assert {role for role, _src, _staged in sb.ARTIFACT_SPECS} == {
            "events",
            "freeze_frames",
            "roster",
        }
        assert {role: src for role, src, _staged in sb.ARTIFACT_SPECS} == {
            "events": sb.SOURCE_FILES["events"],
            "freeze_frames": sb.SOURCE_FILES["frames"],
            "roster": sb.SOURCE_FILES["lineups"],
        }

    def test_staged_stems_are_the_artifact_keys(self) -> None:
        # upload_game derives the artifact key as name.split(".", 1)[0], so the
        # staged stem IS the API allowlist entry.
        assert [staged.split(".", 1)[0] for _role, _src, staged in sb.ARTIFACT_SPECS] == [
            "events",
            "freeze_frames",
            "roster",
        ]


def _competitions_multi() -> list[dict]:
    """Three entitlement rows. Row 0 is deliberately the WRONG one.

    Local to this module: these are adversarial variants, not the canonical
    fixture that conftest provides.
    """
    return [
        {
            "competition_id": 11,
            "country_name": "Wakanda",
            "competition_name": "Vibranium League",
            "season_id": 900,
            "season_name": "2025",
        },
        {
            "competition_id": 22,
            "country_name": "Arendelle",
            "competition_name": "Ice Cup",
            "season_id": 901,
            "season_name": "2026",
        },
        {
            "competition_id": 33,
            "country_name": "Wakanda",
            "competition_name": "Vibranium League",
            "season_id": 902,
            "season_name": "2026",
        },
    ]


class TestJoinCompetition:
    def test_joins_on_rendered_string_and_season_not_row_zero(self) -> None:
        match = {"competition": "Wakanda - Vibranium League", "season": "2026"}
        joined = sb.join_competition(_competitions_multi(), match)
        # Row 0 shares the competition string; row 1 shares the season. Only row 2 is right.
        assert joined["competition_id"] == 33
        assert joined["season_id"] == 902

    def test_season_compared_as_string_when_source_is_numeric(self) -> None:
        rows = [
            {
                "competition_id": 33,
                "country_name": "Wakanda",
                "competition_name": "Vibranium League",
                "season_id": 902,
                "season_name": 2026,
            }
        ]
        match = {"competition": "Wakanda - Vibranium League", "season": "2026"}
        assert sb.join_competition(rows, match)["competition_id"] == 33

    def test_no_match_raises(self) -> None:
        match = {"competition": "Atlantis - Trident Cup", "season": "2026"}
        with pytest.raises(ValueError, match="matched 0 row"):
            sb.join_competition(_competitions_multi(), match)

    def test_multiple_matches_raise(self) -> None:
        rows = [
            *_competitions_multi(),
            {
                "competition_id": 44,
                "country_name": "Wakanda",
                "competition_name": "Vibranium League",
                "season_id": 903,
                "season_name": "2026",
            },
        ]
        match = {"competition": "Wakanda - Vibranium League", "season": "2026"}
        with pytest.raises(ValueError, match="matched 2 row"):
            sb.join_competition(rows, match)

    def test_missing_join_fields_raise(self) -> None:
        with pytest.raises(ValueError, match="competition"):
            sb.join_competition(_competitions_multi(), {"season": "2026"})


def _events_two_teams() -> list[dict]:
    """Away team appears FIRST, so any order-based rule inverts. Local: adversarial variant."""
    return [
        {"id": "e1", "team": {"id": 90007, "name": "Beta United"}},
        {"id": "e2", "team": {"id": 90001, "name": "Alpha City"}},
        {"id": "e3", "team": {"id": 90001, "name": "Alpha City"}},
    ]


class TestResolveTeamIds:
    def test_home_is_home_not_first_seen(self) -> None:
        match = {"home_team": "Alpha City", "away_team": "Beta United"}
        home_id, away_id = sb.resolve_team_ids(_events_two_teams(), match)
        # First team in the event stream is the AWAY side — an order-based
        # implementation would return (90007, 90001) here.
        assert (home_id, away_id) == (90001, 90007)

    def test_unmatched_name_raises(self) -> None:
        match = {"home_team": "Alpha City FC", "away_team": "Beta United"}
        with pytest.raises(ValueError, match="home_team"):
            sb.resolve_team_ids(_events_two_teams(), match)

    def test_same_name_two_ids_raises(self) -> None:
        events = [*_events_two_teams(), {"id": "e4", "team": {"id": 999, "name": "Alpha City"}}]
        match = {"home_team": "Alpha City", "away_team": "Beta United"}
        with pytest.raises(ValueError, match="expected exactly 1"):
            sb.resolve_team_ids(events, match)

    def test_both_sides_same_id_raises(self) -> None:
        events = [{"id": "e1", "team": {"id": 90001, "name": "Alpha City"}}]
        match = {"home_team": "Alpha City", "away_team": "Alpha City"}
        with pytest.raises(ValueError, match="same team id"):
            sb.resolve_team_ids(events, match)


class TestTeamGender:
    def test_resolves_per_team_not_delivery_wide(self) -> None:
        # The feed models gender per TEAM, so a delivery-wide value would be wrong
        # for a fixture whose two squads differ.
        lineups = [
            {"team_id": 90001, "lineup": [{"player_gender": "female"}, {"player_gender": "female"}]},
            {"team_id": 90007, "lineup": [{"player_gender": "male"}]},
        ]
        assert sb.team_gender(lineups, 90001) == "female"
        assert sb.team_gender(lineups, 90007) == "male"

    def test_disagreement_within_a_team_yields_none(self) -> None:
        lineups = [{"team_id": 90001, "lineup": [{"player_gender": "female"}, {"player_gender": "male"}]}]
        assert sb.team_gender(lineups, 90001) is None

    def test_absent_or_unknown_team_yields_none(self) -> None:
        assert sb.team_gender([{"team_id": 90001, "lineup": [{}]}], 90001) is None
        assert sb.team_gender([{"team_id": 90001, "lineup": [{"player_gender": "female"}]}], 999) is None


@pytest.fixture
def built(sb_match_row, sb_competition_row) -> dict:
    return sb.build_metadata(sb_match_row, sb_competition_row, 90001, 90007, "female", "female")


class TestBuildMetadata:
    def test_envelope_is_an_object_not_an_array(self, built: dict) -> None:
        assert isinstance(built, dict)

    def test_top_level_key_set_matches_the_feed_exactly(self, built: dict) -> None:
        # The test that catches a FORGOTTEN feed field. Per-field assertions
        # cannot substitute for it.
        assert set(built) == {
            "match_id",
            "match_date",
            "kick_off",
            "competition",
            "season",
            "home_team",
            "away_team",
            "home_score",
            "away_score",
            "match_status",
            "match_status_360",
            "last_updated",
            "last_updated_360",
            "metadata",
            "match_week",
            "competition_stage",
            "stadium",
            "referee",
            "attendance",
            "behind_closed_doors",
            "neutral_ground",
            "collection_status",
            "play_status",
        }

    def test_nested_substructure_key_sets_match_the_feed(self, built: dict) -> None:
        assert set(built["home_team"]) == {
            "home_team_id",
            "home_team_name",
            "home_team_gender",
            "home_team_group",
            "country",
            "managers",
        }
        assert set(built["away_team"]) == {
            "away_team_id",
            "away_team_name",
            "away_team_gender",
            "away_team_group",
            "country",
            "managers",
        }
        assert set(built["competition"]) == {"competition_id", "country_name", "competition_name"}
        assert set(built["season"]) == {"season_id", "season_name"}
        assert set(built["stadium"]) == {"id", "name", "country"}
        assert set(built["referee"]) == {"id", "name", "country"}
        assert set(built["competition_stage"]) == {"id", "name"}
        assert set(built["metadata"]) == {
            "data_version",
            "shot_fidelity_version",
            "xy_fidelity_version",
        }

    def test_competition_and_season_are_nested(self, built: dict) -> None:
        assert built["competition"] == {
            "competition_id": 33,
            "country_name": "Wakanda",
            "competition_name": "Vibranium League",
        }
        assert built["season"] == {"season_id": 902, "season_name": "2026"}

    def test_team_ids_and_gender_are_recovered(self, built: dict) -> None:
        assert built["home_team"]["home_team_id"] == 90001
        assert built["home_team"]["home_team_name"] == "Alpha City"
        assert built["home_team"]["home_team_gender"] == "female"
        assert built["away_team"]["away_team_id"] == 90007

    def test_gender_is_applied_per_side(self, sb_match_row, sb_competition_row) -> None:
        md = sb.build_metadata(sb_match_row, sb_competition_row, 90001, 90007, "female", "male")
        assert md["home_team"]["home_team_gender"] == "female"
        assert md["away_team"]["away_team_gender"] == "male"

    def test_manager_name_present_and_ids_null(self, built: dict) -> None:
        managers = built["home_team"]["managers"]
        assert len(managers) == 1
        assert managers[0]["name"] == "Dana Coach"
        assert managers[0]["id"] is None
        assert managers[0]["nickname"] is None
        assert managers[0]["dob"] is None
        assert managers[0]["country"] is None

    def test_each_side_gets_its_own_manager(self, built: dict) -> None:
        # The two sides have different managers in the fixture, so a block that
        # wired home_managers into away_team would pass every other test.
        assert built["home_team"]["managers"][0]["name"] == "Dana Coach"
        assert built["away_team"]["managers"][0]["name"] == "Robin Boss"

    def test_no_manager_yields_empty_list(self, sb_match_row, sb_competition_row) -> None:
        sb_match_row["home_managers"] = None
        md = sb.build_metadata(sb_match_row, sb_competition_row, 90001, 90007, "female", "female")
        assert md["home_team"]["managers"] == []

    def test_non_string_manager_cell_raises(self, sb_match_row, sb_competition_row) -> None:
        # A multi-manager cell cannot be split without inventing a boundary (D-4),
        # so the shape is asserted rather than silently accepted.
        sb_match_row["home_managers"] = ["Dana Coach", "Robin Boss"]
        with pytest.raises(ValueError, match="single name string"):
            sb.build_metadata(sb_match_row, sb_competition_row, 90001, 90007, "female", "female")

    def test_fidelity_block_is_renested(self, built: dict) -> None:
        assert built["metadata"] == {
            "data_version": "1.1.0",
            "shot_fidelity_version": "2",
            "xy_fidelity_version": "2",
        }

    def test_scores_are_ints_not_floats(self, built: dict) -> None:
        assert built["home_score"] == 2 and isinstance(built["home_score"], int)
        assert built["away_score"] == 1 and isinstance(built["away_score"], int)

    def test_commercial_only_fields_are_preserved_top_level(self, built: dict) -> None:
        assert built["collection_status"] == "Complete"
        assert built["play_status"] == "Normal"
        assert built["behind_closed_doors"] is False
        assert built["neutral_ground"] is False
        assert built["attendance"] is None

    @pytest.mark.parametrize(
        ("path", "_label"),
        [
            (("home_team", "home_team_group"), "home group"),
            (("home_team", "country"), "home country"),
            (("away_team", "away_team_group"), "away group"),
            (("away_team", "country"), "away country"),
            (("competition_stage", "id"), "stage id"),
            (("stadium", "id"), "stadium id"),
            (("stadium", "country"), "stadium country"),
            (("referee", "id"), "referee id"),
            (("referee", "country"), "referee country"),
        ],
    )
    def test_absent_values_are_null_per_field(self, built: dict, path: tuple[str, ...], _label: str) -> None:
        node: Any = built
        for key in path:
            node = node[key]
        assert node is None

    def test_named_substructures_keep_their_names(self, built: dict) -> None:
        assert built["competition_stage"]["name"] == "Regular Season"
        assert built["stadium"]["name"] == "Alpha Arena"
        assert built["referee"]["name"] == "Sam Whistle"


class TestMatchInfo:
    def test_derives_index_fields(self, built: dict) -> None:
        info = sb.match_info(built)
        assert info.match_id == "9999999"
        assert isinstance(info.match_id, str)
        assert info.date == "2026-01-01"
        assert info.home == "Alpha City"
        assert info.away == "Beta United"

    def test_missing_date_raises(self, built: dict) -> None:
        built["match_date"] = None
        with pytest.raises(ValueError, match="match_date"):
            sb.match_info(built)

    def test_empty_date_raises(self, built: dict) -> None:
        built["match_date"] = ""
        with pytest.raises(ValueError, match="match_date"):
            sb.match_info(built)

    def test_missing_match_id_raises(self, built: dict) -> None:
        built["match_id"] = None
        with pytest.raises(ValueError, match="match_id"):
            sb.match_info(built)

    def test_missing_team_name_raises(self, built: dict) -> None:
        built["home_team"]["home_team_name"] = None
        with pytest.raises(ValueError, match="home and/or away"):
            sb.match_info(built)


class TestPlayersFromLineups:
    def test_nickname_used_when_present(self, sb_lineups) -> None:
        assert sb.players_from_lineups(sb_lineups)[0]["nickname"] == "Roz Fenwick"

    def test_full_name_falls_back_into_nickname_unsplit(self, sb_lineups) -> None:
        assert sb.players_from_lineups(sb_lineups)[1]["nickname"] == "Ingrid Beatrix Halvorsen"

    def test_first_and_last_name_are_never_populated(self, sb_lineups) -> None:
        for rec in sb.players_from_lineups(sb_lineups):
            assert rec.get("firstName") is None
            assert rec.get("lastName") is None

    def test_ids_are_strings(self, sb_lineups) -> None:
        for rec in sb.players_from_lineups(sb_lineups):
            assert isinstance(rec["id"], str)

    def test_canonical_scalars_map_verbatim(self, sb_lineups) -> None:
        rec = sb.players_from_lineups(sb_lineups)[0]
        assert rec["dob"] == "2001-02-03"
        assert rec["height"] == 168.5
        assert rec["nationality"] == "Wakanda"

    def test_earliest_spell_wins(self, sb_lineups) -> None:
        # The period-1 spell is listed SECOND; the earliest must still win.
        assert sb.players_from_lineups(sb_lineups)[0]["position"] == "Right Wing"

    def test_empty_positions_yield_none(self, sb_lineups) -> None:
        assert sb.players_from_lineups(sb_lineups)[1]["position"] is None

    def test_position_group_type_never_populated(self, sb_lineups) -> None:
        for rec in sb.players_from_lineups(sb_lineups):
            assert rec.get("positionGroupType") is None

    def test_proprietary_and_excluded_fields_absent(self, sb_lineups) -> None:
        for rec in sb.players_from_lineups(sb_lineups):
            for excluded in ("skills", "player_weight", "weight", "stats", "jersey_number"):
                assert excluded not in rec

    def test_records_validate_against_the_canonical_model(self, sb_lineups) -> None:
        from canonical.models import PlayerRecord

        for rec in sb.players_from_lineups(sb_lineups):
            PlayerRecord.model_validate({**rec, "visibility": "private", "updated_at": "2026-01-01T00:00:00Z"})

    def test_player_without_id_is_skipped(self) -> None:
        lineups = [{"lineup": [{"player_name": "Ghost"}, {"player_id": 7, "player_name": "Real"}]}]
        assert [r["id"] for r in sb.players_from_lineups(lineups)] == ["7"]

    def test_spell_without_from_period_raises(self, sb_lineups) -> None:
        # A malformed spell must not silently outrank a genuine period-1 spell.
        sb_lineups[0]["lineup"][0]["positions"].append({"position": "Ghost", "from": "00:00:00.000"})
        with pytest.raises(ValueError, match="from_period"):
            sb.players_from_lineups(sb_lineups)


class TestAssertDeliveryCoherent:
    def test_coherent_delivery_passes(self, sb_events, sb_lineups) -> None:
        sb.assert_delivery_coherent(sb_events, [{"event_uuid": "e1"}, {"event_uuid": "e2"}], sb_lineups)

    def test_orphan_frame_raises(self, sb_events, sb_lineups) -> None:
        with pytest.raises(ValueError, match="unknown event id"):
            sb.assert_delivery_coherent(sb_events, [{"event_uuid": "nope"}], sb_lineups)

    def test_uuidless_frame_is_an_orphan_even_when_an_event_lacks_an_id(self, sb_events, sb_lineups) -> None:
        # `None` must not enter event_ids, or a uuid-less frame matches it and
        # slips through the orphan check.
        events = [*sb_events, {"period": 2, "type": {"name": "Pass"}, "team": {"id": 90001, "name": "Alpha City"}}]
        with pytest.raises(ValueError, match="unknown event id"):
            sb.assert_delivery_coherent(events, [{"visible_area": []}], sb_lineups)

    def test_zero_frames_raises(self, sb_events, sb_lineups) -> None:
        # The orphan check passes vacuously on an empty list, so this needs its
        # own guard: 360 frames are the entire point of this provider.
        with pytest.raises(ValueError, match="zero freeze frames"):
            sb.assert_delivery_coherent(sb_events, [], sb_lineups)

    def test_zero_lineups_raises(self, sb_events) -> None:
        # Same vacuous pass as zero frames, one file over: [] satisfies
        # _require_array, stages an empty roster, and skips the player upload —
        # shipping an indexed match with no squad and no error.
        with pytest.raises(ValueError, match="zero lineup entries"):
            sb.assert_delivery_coherent(sb_events, [{"event_uuid": "e1"}], [])

    def test_empty_per_team_squads_raise(self, sb_events, sb_lineups) -> None:
        # The shape BETWEEN test_zero_lineups_raises and the half delivery below:
        # both team blocks present, both squads empty. The outer array is non-empty
        # so the zero-lineups guard passes, and the team_ids are still there so the
        # lineups-vs-events cross-check passes too — yet players_from_lineups
        # returns [], upload_players is skipped by its `if players:` guard, and the
        # match is indexed with an empty roster, no catalogue and no error.
        for team in sb_lineups:
            team["lineup"] = []
        with pytest.raises(ValueError, match="are empty"):
            sb.assert_delivery_coherent(sb_events, [{"event_uuid": "e1"}], sb_lineups)

    def test_missing_lineup_key_raises(self, sb_events, sb_lineups) -> None:
        # An absent `lineup` key is the same truncation as an empty one: no squad.
        del sb_lineups[0]["lineup"]
        with pytest.raises(ValueError, match="are empty"):
            sb.assert_delivery_coherent(sb_events, [{"event_uuid": "e1"}], sb_lineups)

    def test_lineups_and_events_disagreeing_on_teams_raises(self, sb_events, sb_lineups) -> None:
        # A contradiction INSIDE the delivery (ADR 0010 rule 3): one export of one
        # fixture cannot field one pair of teams in events and another in lineups.
        sb_lineups[1]["team_id"] = 90002
        with pytest.raises(ValueError, match="contradicts itself"):
            sb.assert_delivery_coherent(sb_events, [{"event_uuid": "e1"}], sb_lineups)

    def test_lineups_covering_only_one_side_raises(self, sb_events, sb_lineups) -> None:
        # The half-delivery case: a lineups file for the away side only. Every other
        # check passes and home_team_gender silently resolves to null.
        with pytest.raises(ValueError, match="contradicts itself"):
            sb.assert_delivery_coherent(sb_events, [{"event_uuid": "e1"}], sb_lineups[:1])

    def test_single_period_raises(self, sb_events, sb_lineups) -> None:
        with pytest.raises(ValueError, match="period"):
            sb.assert_delivery_coherent([e for e in sb_events if e["period"] == 1], [{"event_uuid": "e1"}], sb_lineups)

    def test_missing_half_end_raises(self, sb_events, sb_lineups) -> None:
        events = [e for e in sb_events if e["type"]["name"] != "Half End"]
        with pytest.raises(ValueError, match="Half End"):
            sb.assert_delivery_coherent(events, [{"event_uuid": "e1"}], sb_lineups)

    def test_wrong_team_count_raises(self, sb_events, sb_lineups) -> None:
        events = [*sb_events, {"id": "e4", "period": 2, "type": {"name": "Pass"}, "team": {"id": 3, "name": "Gamma"}}]
        with pytest.raises(ValueError, match="distinct team"):
            sb.assert_delivery_coherent(events, [{"event_uuid": "e1"}], sb_lineups)

    def test_single_team_raises(self, sb_events, sb_lineups) -> None:
        events = [e for e in sb_events if e["team"]["id"] == 90001]
        with pytest.raises(ValueError, match="distinct team"):
            sb.assert_delivery_coherent(events, [{"event_uuid": "e1"}], sb_lineups)


class TestReadBundle:
    def test_reads_and_joins(self, sb_bundle_dir) -> None:
        bundle = sb.read_bundle(sb_bundle_dir())
        assert bundle.match["match_id"] == 9999999
        assert bundle.competition["competition_id"] == 33
        assert len(bundle.events) == 3
        assert len(bundle.frames) == 1
        assert len(bundle.lineups) == 2

    def test_missing_file_raises(self, sb_bundle_dir) -> None:
        root = sb_bundle_dir()
        (root / "frames.json").unlink()
        with pytest.raises(FileNotFoundError, match=r"frames\.json"):
            sb.read_bundle(root)

    def test_multi_match_dump_raises(self, sb_bundle_dir, sb_match_row) -> None:
        root = sb_bundle_dir(
            overrides={"matches.json": {col: {"7": val, "8": val} for col, val in sb_match_row.items()}}
        )
        with pytest.raises(ValueError, match="one match per bundle"):
            sb.read_bundle(root)

    def test_passthrough_file_that_is_not_an_array_raises(self, sb_bundle_dir) -> None:
        # Keeps the fail-loud story consistent across all five files.
        root = sb_bundle_dir(overrides={"lineups.json": {"not": "an array"}})
        with pytest.raises(ValueError, match=r"lineups\.json is a dict, expected a JSON array"):
            sb.read_bundle(root)

    def test_passthrough_array_of_non_objects_raises(self, sb_bundle_dir) -> None:
        # The container check alone lets this through — it IS a list — and the
        # AttributeError the guard claims to prevent happens later anyway.
        root = sb_bundle_dir(overrides={"events.json": ["a", "b"]})
        with pytest.raises(ValueError, match=r"events\.json\[0\] is a str, expected a JSON object"):
            sb.read_bundle(root)
