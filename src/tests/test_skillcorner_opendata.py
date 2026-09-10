"""Unit tests for formats.skillcorner_opendata (pure reader; synthetic data only).

Every id, team and date below is invented — nothing is copied from the opendata feed.
"""

from __future__ import annotations

import pytest

from formats.skillcorner_opendata import (
    OpenDataMatchInfo,
    discover_match_ids,
    match_info,
    opendata_files,
    select_new_matches,
)


def _meta(
    match_id: int = 3001,
    date_time: str = "2024-11-30T04:00:00Z",
    home: dict | None = None,
    away: dict | None = None,
) -> dict:
    return {
        "id": match_id,
        "date_time": date_time,
        "home_team": {"id": 10, "name": "Harbour City FC", "short_name": "Harbour"} if home is None else home,
        "away_team": {"id": 20, "name": "Mountain United FC", "short_name": "Mountain"} if away is None else away,
        "players": [],
    }


class TestOpendataFiles:
    def test_maps_four_id_prefixed_artifacts(self) -> None:
        assert opendata_files("3001") == {
            "match": "matches/3001/3001_match.json",
            "tracking_extrapolated": "matches/3001/3001_tracking_extrapolated.jsonl",
            "dynamic_events": "matches/3001/3001_dynamic_events.csv",
            "phases_of_play": "matches/3001/3001_phases_of_play.csv",
        }


class TestDiscoverMatchIds:
    def test_returns_sorted_string_ids(self) -> None:
        index = [{"id": 2017461}, {"id": 1874553}, {"id": 1959846}]
        assert discover_match_ids(index) == ["1874553", "1959846", "2017461"]


class TestSelectNewMatches:
    def test_returns_sorted_ids_not_already_live(self) -> None:
        assert select_new_matches(["1927964", "1874553", "1886347"], {"1886347"}) == ["1874553", "1927964"]

    def test_empty_when_all_live(self) -> None:
        assert select_new_matches(["1", "2"], {"1", "2"}) == []


class TestMatchInfo:
    def test_uses_utc_date_prefix_and_short_names(self) -> None:
        assert match_info(_meta()) == OpenDataMatchInfo(
            match_id="3001", date="2024-11-30", home="Harbour", away="Mountain"
        )

    def test_date_is_utc_calendar_date_not_tz_converted(self) -> None:
        # A late-UTC kickoff must keep the UTC calendar date (parity with the original 10 public
        # entries), NOT shift under any Europe/Madrid or Australia/NZ local conversion.
        assert match_info(_meta(date_time="2025-05-17T23:30:00Z")).date == "2025-05-17"

    def test_falls_back_to_name_when_short_name_absent(self) -> None:
        info = match_info(_meta(home={"id": 1, "name": "Only Name FC"}, away={"id": 2, "short_name": "ShortOnly"}))
        assert info.home == "Only Name FC"
        assert info.away == "ShortOnly"

    @pytest.mark.parametrize(
        "override",
        [{"id": None}, {"date_time": None}, {"home_team": None}, {"away_team": None}],
    )
    def test_raises_on_missing_required_field(self, override: dict) -> None:
        meta = _meta()
        meta.update(override)
        with pytest.raises(ValueError):
            match_info(meta)
