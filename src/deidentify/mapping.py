"""Two-layer jersey-to-identity mapping.

Layer 1 (private): Player → stable synthetic identity
    Maintained by the data owner. Ensures the same player is always
    mapped to the same synthetic name across all games, even if jersey
    numbers change.

Layer 2 (per-game): Jersey number → synthetic identity
    Derived from the roster JSON for each game. This is what the tracking
    data uses — it maps provider-detected jersey numbers to synthetic names.
"""

from __future__ import annotations

from dataclasses import dataclass

from deidentify.roster_generator import GameRoster


@dataclass(frozen=True)
class JerseyMapping:
    """Maps a jersey number to a synthetic identity for a single game."""

    jersey: int
    name: str
    team: str
    position: str = ""

    @property
    def player_id(self) -> str:
        """Stable identifier for use in tracking data columns."""
        return self.name.lower().replace(" ", "_").replace("'", "")


class TwoLayerMapping:
    """Resolves jersey numbers to synthetic identities for a game.

    Constructed from a GameRoster (the per-game JSON). Provides fast
    lookup by jersey number for both home and away teams.
    """

    def __init__(self, game_roster: GameRoster) -> None:
        self._game_id = game_roster.game_id
        self._home_map: dict[int, JerseyMapping] = {}
        self._away_map: dict[int, JerseyMapping] = {}

        for player in game_roster.home.roster:
            self._home_map[player.jersey] = JerseyMapping(
                jersey=player.jersey,
                name=player.name,
                team=game_roster.home.team_name,
                position=player.position,
            )

        for player in game_roster.away.roster:
            self._away_map[player.jersey] = JerseyMapping(
                jersey=player.jersey,
                name=player.name,
                team=game_roster.away.team_name,
                position=player.position,
            )

    @property
    def game_id(self) -> str:
        return self._game_id

    def resolve_home(self, jersey: int) -> JerseyMapping | None:
        """Look up a home player by jersey number."""
        return self._home_map.get(jersey)

    def resolve_away(self, jersey: int) -> JerseyMapping | None:
        """Look up an away player by jersey number."""
        return self._away_map.get(jersey)

    def resolve(self, jersey: int, team: str) -> JerseyMapping | None:
        """Look up a player by jersey number and team side ('home' or 'away')."""
        if team == "home":
            return self.resolve_home(jersey)
        if team == "away":
            return self.resolve_away(jersey)
        return None

    @property
    def home_players(self) -> list[JerseyMapping]:
        return sorted(self._home_map.values(), key=lambda m: m.jersey)

    @property
    def away_players(self) -> list[JerseyMapping]:
        return sorted(self._away_map.values(), key=lambda m: m.jersey)

    def to_column_rename_map(self, team: str) -> dict[str, str]:
        """Generate a column rename mapping for tracking data.

        Converts provider column names like 'Home_14_x' → 'tchalla_stark_x'.
        Used when applying de-identification to raw Metrica CSVs.
        """
        mapping: dict[int, JerseyMapping] = self._home_map if team == "home" else self._away_map
        prefix = "Home" if team == "home" else "Away"
        renames: dict[str, str] = {}
        for jersey, identity in mapping.items():
            pid = identity.player_id
            renames[f"{prefix}_{jersey}_x"] = f"{pid}_x"
            renames[f"{prefix}_{jersey}_y"] = f"{pid}_y"
        return renames

    @classmethod
    def from_roster_file(cls, path: str | bytes) -> TwoLayerMapping:
        """Load from a roster JSON file."""
        import json
        from pathlib import Path

        from deidentify.roster_generator import GameRoster, MatchMetadata, Player, TeamRoster

        with open(Path(path), encoding="utf-8") as f:
            data = json.load(f)

        home = TeamRoster(
            team_name=data["home"]["team_name"],
            roster=[Player(**p) for p in data["home"]["roster"]],
        )
        away = TeamRoster(
            team_name=data["away"]["team_name"],
            roster=[Player(**p) for p in data["away"]["roster"]],
        )
        metadata = MatchMetadata(**data.get("metadata", {}))
        game_roster = GameRoster(
            game_id=data["game_id"],
            date=data["date"],
            home=home,
            away=away,
            metadata=metadata,
        )
        return cls(game_roster)
