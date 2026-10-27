"""Value rules for competition/season fields (spec §5.3) and the SkillCorner V3 reader (§5.2).

All values are invented. No real provider id, name, or (id -> entity) tuple appears here.
"""

from __future__ import annotations

import pytest

from formats.competition_season import (
    CompetitionSeason,
    native_id,
    native_name,
    skillcorner_v3_competition_season,
)

_EDITION = {
    "competition_edition": {
        "id": 4001,
        "competition": {"id": 301, "name": "Synthetic Premier"},
        "season": {"id": 701, "name": "2099/2100"},
    }
}


def test_native_id_int_becomes_decimal_string():
    assert native_id(61, "competition_id") == "61"


def test_native_id_str_verbatim():
    assert native_id("SYN-COM-01", "x") == "SYN-COM-01"
    # No stripping: the model refuses a leading space later, the value rule never rewrites it.
    assert native_id(" A1", "x") == " A1"


def test_native_id_absent_values():
    assert native_id(None, "x") is None
    assert native_id("", "x") is None


@pytest.mark.parametrize("value", [49.0, True, False, [1], {"a": 1}])
def test_native_id_refuses_other_types(value):
    with pytest.raises(ValueError, match="competition_id"):
        native_id(value, "competition_id")


def test_native_name_rules():
    assert native_name("Synthetic League", "competition_name") == "Synthetic League"
    assert native_name(None, "competition_name") is None
    assert native_name("", "competition_name") is None
    for bad in (5, 5.0, True, ["x"]):
        with pytest.raises(ValueError, match="season_name"):
            native_name(bad, "season_name")


def test_skillcorner_v3_reads_only_its_own_paths():
    assert skillcorner_v3_competition_season(_EDITION) == CompetitionSeason(
        "301", "Synthetic Premier", "701", "2099/2100"
    )


def test_roles_never_cross():
    meta = {
        "competition_edition": {
            "competition": {"name": "Synthetic Premier"},
            "season": {"name": "2099/2100"},
        }
    }
    got = skillcorner_v3_competition_season(meta)
    assert got.competition_id is None
    assert got.competition_name == "Synthetic Premier"
    assert got.season_id is None
    assert got.season_name == "2099/2100"


def test_skillcorner_v3_missing_edition_gives_absent_fields():
    assert skillcorner_v3_competition_season({}) == CompetitionSeason()
    assert skillcorner_v3_competition_season({"competition_edition": {}}) == CompetitionSeason()


def test_as_kwargs_keys():
    assert set(CompetitionSeason().as_kwargs()) == {
        "competition_id",
        "competition_name",
        "season_id",
        "season_name",
    }


def test_as_kwargs_values():
    got = CompetitionSeason("301", "Synthetic Premier", None, "2099").as_kwargs()
    assert got == {
        "competition_id": "301",
        "competition_name": "Synthetic Premier",
        "season_id": None,
        "season_name": "2099",
    }
