"""Gradient Sports metadata reader (spec §5.2, D3). Invented fixture values only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from formats.competition_season import CompetitionSeason
from formats.gradientsports import match_info

FIXTURE = Path(__file__).parent / "fixtures" / "gradientsports_metadata_synthetic.json"


def _fixture() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_gradient_match_info_values():
    info = match_info(_fixture())
    assert info.match_id == "990001"
    assert info.competition == CompetitionSeason("77", "Synthetic Cup", None, "2099")


def test_gradient_season_is_name_only():
    fixture = _fixture()
    info = match_info(fixture)
    assert info.competition.season_id is None
    assert info.competition.season_name == fixture[0]["season"]


def test_gradient_date_home_away_parity():
    """The reader reproduces the expressions the loader used before this change."""
    metadata_obj = _fixture()[0]
    info = match_info(_fixture())
    assert info.date == metadata_obj.get("date", "").split("T", 1)[0]
    assert info.home == (metadata_obj.get("homeTeam") or {}).get("name", "")
    assert info.away == (metadata_obj.get("awayTeam") or {}).get("name", "")
    assert (info.date, info.home, info.away) == ("2099-07-01", "Synthetic Rovers", "Invented Athletic")


def test_gradient_accepts_unwrapped_dict():
    assert match_info(_fixture()[0]).match_id == "990001"


def test_gradient_missing_source_value_gives_absent_field():
    meta = {k: v for k, v in _fixture()[0].items() if k != "season"}
    assert match_info([meta]).competition.season_name is None


def test_gradient_missing_match_id_refused():
    meta = {k: v for k, v in _fixture()[0].items() if k != "id"}
    with pytest.raises(ValueError, match="match_id"):
        match_info([meta])
