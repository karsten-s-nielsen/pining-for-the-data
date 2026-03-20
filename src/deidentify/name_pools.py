"""Load and sample from fictional name pools.

Name pools are JSON arrays of strings sourced from GOT, LOTR, BB/BCS,
Princess Bride, EEAAO, Ghibli, and Elf.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

_POOLS_DIR = Path(__file__).resolve().parent.parent.parent / "name_pools"


class NamePools:
    """Lazy-loaded name pools with sampling support.

    Parameters
    ----------
    pools_dir : Path | None
        Override the default name_pools/ directory location.
    rng : random.Random | None
        Random number generator for reproducible sampling.
    """

    def __init__(self, pools_dir: Path | None = None, rng: random.Random | None = None) -> None:
        self._pools_dir = pools_dir or _POOLS_DIR
        self._rng = rng or random.Random()
        self._cache: dict[str, list[str]] = {}

    def _load(self, name: str) -> list[str]:
        if name not in self._cache:
            path = self._pools_dir / f"{name}.json"
            with open(path, encoding="utf-8") as f:
                self._cache[name] = json.load(f)
        return self._cache[name]

    @property
    def male_names(self) -> list[str]:
        return self._load("male_names")

    @property
    def female_names(self) -> list[str]:
        return self._load("female_names")

    @property
    def last_names(self) -> list[str]:
        return self._load("last_names")

    @property
    def cities(self) -> list[str]:
        return self._load("cities")

    def sample_player_name(self, gender: str = "male") -> str:
        """Generate a random 'First Last' name."""
        first_pool = self.male_names if gender == "male" else self.female_names
        first = self._rng.choice(first_pool)
        last = self._rng.choice(self.last_names)
        return f"{first} {last}"

    def sample_team_name(
        self, suffixes: tuple[str, ...] = ("FC", "United", "Athletic", "Rovers", "City", "Town")
    ) -> str:
        """Generate a random team name from the cities pool."""
        city = self._rng.choice(self.cities)
        suffix = self._rng.choice(suffixes)
        return f"{city} {suffix}"

    def sample_unique_player_names(
        self, count: int, gender: str = "male", exclude: set[str] | None = None
    ) -> list[str]:
        """Generate ``count`` unique player names, avoiding collisions with ``exclude``."""
        exclude = exclude or set()
        names: list[str] = []
        attempts = 0
        max_attempts = count * 20
        while len(names) < count and attempts < max_attempts:
            name = self.sample_player_name(gender)
            if name not in exclude and name not in names:
                names.append(name)
            attempts += 1
        if len(names) < count:
            msg = f"Could not generate {count} unique names after {max_attempts} attempts"
            raise ValueError(msg)
        return names
