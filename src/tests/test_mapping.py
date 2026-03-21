"""Tests for deidentify.mapping."""

from __future__ import annotations

import json
from pathlib import Path

from deidentify.mapping import JerseyMapping, TwoLayerMapping
from deidentify.roster_generator import GameRoster, Player, TeamRoster


def _make_game_roster() -> GameRoster:
    """Create a minimal game roster for testing."""
    return GameRoster(
        game_id="test_01",
        date="2026-03-15",
        home=TeamRoster(
            team_name="Wakanda FC",
            roster=[
                Player(jersey=1, name="Fezzik Took", position="GK"),
                Player(jersey=41, name="T'Challa Stark", position="MF"),
            ],
        ),
        away=TeamRoster(
            team_name="Shire Town",
            roster=[
                Player(jersey=7, name="Bilbo Lannister"),
                Player(jersey=9, name="Samwise Baratheon"),
            ],
        ),
    )


class TestJerseyMapping:
    def test_player_id_generation(self) -> None:
        m = JerseyMapping(jersey=41, name="T'Challa Stark", team="Wakanda FC")
        assert m.player_id == "tchalla_stark"

    def test_player_id_no_apostrophe(self) -> None:
        m = JerseyMapping(jersey=1, name="Fezzik Took", team="Wakanda FC")
        assert m.player_id == "fezzik_took"

    def test_frozen(self) -> None:
        m = JerseyMapping(jersey=1, name="Test", team="Test FC")
        # Should be immutable
        import pytest

        with pytest.raises(AttributeError):
            m.jersey = 2  # type: ignore[misc]


class TestTwoLayerMapping:
    def test_resolve_home(self) -> None:
        mapping = TwoLayerMapping(_make_game_roster())
        result = mapping.resolve_home(41)
        assert result is not None
        assert result.name == "T'Challa Stark"
        assert result.team == "Wakanda FC"

    def test_resolve_away(self) -> None:
        mapping = TwoLayerMapping(_make_game_roster())
        result = mapping.resolve_away(7)
        assert result is not None
        assert result.name == "Bilbo Lannister"

    def test_resolve_missing_jersey(self) -> None:
        mapping = TwoLayerMapping(_make_game_roster())
        assert mapping.resolve_home(99) is None
        assert mapping.resolve_away(99) is None

    def test_resolve_by_team_side(self) -> None:
        mapping = TwoLayerMapping(_make_game_roster())
        assert mapping.resolve(41, "home") is not None
        assert mapping.resolve(7, "away") is not None
        assert mapping.resolve(41, "away") is None
        assert mapping.resolve(7, "home") is None

    def test_home_players_sorted(self) -> None:
        mapping = TwoLayerMapping(_make_game_roster())
        players = mapping.home_players
        assert len(players) == 2
        assert players[0].jersey < players[1].jersey

    def test_away_players_sorted(self) -> None:
        mapping = TwoLayerMapping(_make_game_roster())
        players = mapping.away_players
        assert len(players) == 2
        assert players[0].jersey < players[1].jersey

    def test_from_roster_file(self, tmp_path: Path) -> None:
        roster = _make_game_roster()
        path = tmp_path / "roster.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(roster.to_dict(), f)

        mapping = TwoLayerMapping.from_roster_file(str(path))
        assert mapping.game_id == "test_01"
        assert mapping.resolve_home(41) is not None
        assert mapping.resolve_away(7) is not None
