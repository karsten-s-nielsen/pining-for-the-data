"""Generate synthetic rosters for de-identified match data.

Produces per-game roster JSON files with featured names for select
players and randomly generated names for the rest.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

from deidentify.name_pools import NamePools

# Featured synthetic identities for Wakanda FC starters.
# These are stable across all games (Layer 1 of the two-layer mapping).
FEATURED_NAMES: dict[int, dict[str, str]] = {
    1: {"name": "Fezzik Took", "position": "GK"},
    17: {"name": "Tormund Tully", "position": "FW"},
    30: {"name": "Westley Montoya", "position": "MF"},
    41: {"name": "T'Challa Stark", "position": "MF"},
}

HOME_TEAM_NAME = "Wakanda FC"

# Pre-assigned opponent names (sequentially assigned per game).
OPPONENT_TEAMS: list[str] = [
    "Asgard Athletic",
    "Gondor Rangers",
    "Hogwarts United",
    "Themyscira FC",
    "Coruscant City",
    "Narnia Town",
    "Rivendell FC",
    "Hyrule United",
    "Laputa Athletic",
    "Krypton City",
    "Lothlorien FC",
    "Naboo Rovers",
    "Ankh-Morpork FC",
    "Pandora United",
    "Tatooine FC",
    "Metropolis City",
    "Shire Town",
    "Elysium FC",
    "Iron Town Athletic",
    "Dagobah FC",
]


@dataclass
class Player:
    """A single player in a de-identified roster."""

    jersey: int
    name: str
    position: str = ""


@dataclass
class TeamRoster:
    """One team's roster for a game — team name and list of players."""

    team_name: str
    roster: list[Player] = field(default_factory=list)


@dataclass
class MatchMetadata:
    """Competition metadata attached to a game roster (gender, age group, level)."""

    gender: str = "boys"
    age_group: str = "youth"
    level: str = "club competitive"
    competition: str = "Westeros Youth League"


@dataclass
class GameRoster:
    """Complete de-identified roster for a game — home team, away team, and metadata."""

    game_id: str
    date: str
    home: TeamRoster
    away: TeamRoster
    metadata: MatchMetadata = field(default_factory=MatchMetadata)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())


class RosterGenerator:
    """Generate de-identified rosters for a match.

    Parameters
    ----------
    pools : NamePools | None
        Name pool instance. Created with default paths if not provided.
    seed : int | None
        Random seed for reproducible roster generation.
    """

    def __init__(self, pools: NamePools | None = None, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._pools = pools or NamePools(rng=self._rng)

    def generate_home_roster(self, jersey_numbers: list[int]) -> TeamRoster:
        """Generate Wakanda FC roster with featured + random names.

        Featured names are assigned to designated jersey numbers.
        Remaining numbers get randomly generated names.
        """
        cherry_picked_names = {info["name"] for info in FEATURED_NAMES.values()}
        random_count = sum(1 for j in jersey_numbers if j not in FEATURED_NAMES)
        random_names = self._pools.sample_unique_player_names(random_count, gender="male", exclude=cherry_picked_names)

        players: list[Player] = []
        random_idx = 0
        for jersey in sorted(jersey_numbers):
            if jersey in FEATURED_NAMES:
                info = FEATURED_NAMES[jersey]
                players.append(Player(jersey=jersey, name=info["name"], position=info["position"]))
            else:
                players.append(Player(jersey=jersey, name=random_names[random_idx]))
                random_idx += 1

        return TeamRoster(team_name=HOME_TEAM_NAME, roster=players)

    def generate_away_roster(
        self, jersey_numbers: list[int], opponent_index: int | None = None, team_name: str | None = None
    ) -> TeamRoster:
        """Generate a fully random away team roster.

        Parameters
        ----------
        jersey_numbers : list[int]
            Jersey numbers detected for the away team.
        opponent_index : int | None
            Index into OPPONENT_TEAMS (0-based). Used for sequential assignment.
        team_name : str | None
            Explicit team name override. Takes precedence over opponent_index.
        """
        if team_name is None:
            if opponent_index is not None and 0 <= opponent_index < len(OPPONENT_TEAMS):
                team_name = OPPONENT_TEAMS[opponent_index]
            else:
                team_name = self._pools.sample_team_name()

        names = self._pools.sample_unique_player_names(len(jersey_numbers), gender="male")
        players = [Player(jersey=j, name=n) for j, n in zip(sorted(jersey_numbers), names, strict=True)]
        return TeamRoster(team_name=team_name, roster=players)

    def generate_game_roster(
        self,
        game_id: str,
        date: str,
        home_jerseys: list[int],
        away_jerseys: list[int],
        opponent_index: int | None = None,
        away_team_name: str | None = None,
    ) -> GameRoster:
        """Generate a complete game roster (home + away)."""
        home = self.generate_home_roster(home_jerseys)
        away = self.generate_away_roster(away_jerseys, opponent_index=opponent_index, team_name=away_team_name)
        return GameRoster(game_id=game_id, date=date, home=home, away=away)


def main() -> None:
    """CLI entry point for generating a roster."""
    import argparse

    parser = argparse.ArgumentParser(description="Generate a de-identified roster for a game")
    parser.add_argument("--game-id", required=True, help="Game identifier (e.g., game_03)")
    parser.add_argument("--date", required=True, help="Game date (YYYY-MM-DD)")
    parser.add_argument("--home-jerseys", required=True, help="Comma-separated home jersey numbers")
    parser.add_argument("--away-jerseys", required=True, help="Comma-separated away jersey numbers")
    parser.add_argument("--opponent-index", type=int, default=None, help="Index into pre-defined opponent names (0-19)")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    parser.add_argument("--output", type=Path, default=None, help="Output JSON path (default: rosters/<game_id>.json)")
    args = parser.parse_args()

    home_jerseys = [int(j.strip()) for j in args.home_jerseys.split(",")]
    away_jerseys = [int(j.strip()) for j in args.away_jerseys.split(",")]

    gen = RosterGenerator(seed=args.seed)
    roster = gen.generate_game_roster(
        game_id=args.game_id,
        date=args.date,
        home_jerseys=home_jerseys,
        away_jerseys=away_jerseys,
        opponent_index=args.opponent_index,
    )

    output = args.output or Path("rosters") / f"{args.game_id}.json"
    roster.save(output)
    print(f"Roster saved to {output}")
