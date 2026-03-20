"""Tests for deidentify.roster_generator."""

from __future__ import annotations

import json
from pathlib import Path

from deidentify.roster_generator import (
    FEATURED_NAMES,
    HOME_TEAM_NAME,
    OPPONENT_TEAMS,
    RosterGenerator,
)


class TestRosterGenerator:
    def test_home_roster_featured_names(self, name_pools_dir: Path) -> None:
        gen = RosterGenerator(seed=42)
        jerseys = [1, 11, 17, 20, 23, 25, 26, 30, 31, 33, 38, 40, 41, 45]
        roster = gen.generate_home_roster(jerseys)

        assert roster.team_name == HOME_TEAM_NAME

        by_jersey = {p.jersey: p for p in roster.roster}
        for jersey, expected in FEATURED_NAMES.items():
            assert by_jersey[jersey].name == expected["name"]
            assert by_jersey[jersey].position == expected["position"]

    def test_home_roster_fills_remaining_randomly(self, name_pools_dir: Path) -> None:
        gen = RosterGenerator(seed=42)
        jerseys = [1, 11, 17, 30, 41]
        roster = gen.generate_home_roster(jerseys)

        assert len(roster.roster) == 5
        # #11 is not a featured name, should get a random name
        player_11 = next(p for p in roster.roster if p.jersey == 11)
        assert " " in player_11.name  # "First Last" format
        assert player_11.name not in {v["name"] for v in FEATURED_NAMES.values()}

    def test_home_roster_no_duplicate_names(self, name_pools_dir: Path) -> None:
        gen = RosterGenerator(seed=42)
        jerseys = list(range(1, 23))  # 22 players
        roster = gen.generate_home_roster(jerseys)
        names = [p.name for p in roster.roster]
        assert len(names) == len(set(names))

    def test_away_roster_sequential_opponent(self, name_pools_dir: Path) -> None:
        gen = RosterGenerator(seed=42)
        roster = gen.generate_away_roster([1, 2, 3, 4, 5], opponent_index=0)
        assert roster.team_name == OPPONENT_TEAMS[0]
        assert len(roster.roster) == 5

    def test_away_roster_explicit_name(self, name_pools_dir: Path) -> None:
        gen = RosterGenerator(seed=42)
        roster = gen.generate_away_roster([7, 9, 11], team_name="Mordor United")
        assert roster.team_name == "Mordor United"

    def test_away_roster_random_name_fallback(self, name_pools_dir: Path) -> None:
        gen = RosterGenerator(seed=42)
        roster = gen.generate_away_roster([1, 2, 3], opponent_index=999)
        assert roster.team_name  # Got a random team name
        assert len(roster.roster) == 3

    def test_generate_game_roster(self, name_pools_dir: Path) -> None:
        gen = RosterGenerator(seed=42)
        game = gen.generate_game_roster(
            game_id="game_03",
            date="2026-03-15",
            home_jerseys=[1, 17, 30, 41],
            away_jerseys=[2, 5, 8, 10],
            opponent_index=16,
        )

        assert game.game_id == "game_03"
        assert game.date == "2026-03-15"
        assert game.home.team_name == HOME_TEAM_NAME
        assert game.away.team_name == OPPONENT_TEAMS[16]
        assert len(game.home.roster) == 4
        assert len(game.away.roster) == 4
        assert game.metadata.age_group == "youth"

    def test_to_json_roundtrip(self, name_pools_dir: Path, tmp_path: Path) -> None:
        gen = RosterGenerator(seed=42)
        game = gen.generate_game_roster(
            game_id="test_game",
            date="2026-01-01",
            home_jerseys=[1, 41],
            away_jerseys=[7, 9],
            opponent_index=0,
        )

        json_str = game.to_json()
        parsed = json.loads(json_str)
        assert parsed["game_id"] == "test_game"
        assert len(parsed["home"]["roster"]) == 2
        assert len(parsed["away"]["roster"]) == 2

    def test_save_and_load(self, name_pools_dir: Path, tmp_path: Path) -> None:
        gen = RosterGenerator(seed=42)
        game = gen.generate_game_roster(
            game_id="save_test",
            date="2026-01-01",
            home_jerseys=[1, 17, 41],
            away_jerseys=[3, 5],
            opponent_index=5,
        )

        path = tmp_path / "roster.json"
        game.save(path)

        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)

        assert loaded["game_id"] == "save_test"
        assert loaded["home"]["team_name"] == HOME_TEAM_NAME
        assert loaded["away"]["team_name"] == OPPONENT_TEAMS[5]

    def test_reproducible_with_same_seed(self, name_pools_dir: Path) -> None:
        gen1 = RosterGenerator(seed=99)
        gen2 = RosterGenerator(seed=99)
        jerseys = [1, 11, 17, 30, 41]
        r1 = gen1.generate_home_roster(jerseys)
        r2 = gen2.generate_home_roster(jerseys)
        names1 = [p.name for p in r1.roster]
        names2 = [p.name for p in r2.roster]
        assert names1 == names2
