"""Tests for deidentify.name_pools."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from deidentify.name_pools import NamePools


class TestNamePools:
    def test_load_male_names(self, name_pools_dir: Path) -> None:
        pools = NamePools(pools_dir=name_pools_dir)
        names = pools.male_names
        assert len(names) > 100
        assert all(isinstance(n, str) for n in names)

    def test_load_female_names(self, name_pools_dir: Path) -> None:
        pools = NamePools(pools_dir=name_pools_dir)
        names = pools.female_names
        assert len(names) > 50
        assert all(isinstance(n, str) for n in names)

    def test_load_last_names(self, name_pools_dir: Path) -> None:
        pools = NamePools(pools_dir=name_pools_dir)
        names = pools.last_names
        assert len(names) > 100
        assert all(isinstance(n, str) for n in names)

    def test_load_cities(self, name_pools_dir: Path) -> None:
        pools = NamePools(pools_dir=name_pools_dir)
        cities = pools.cities
        assert len(cities) > 100
        assert all(isinstance(c, str) for c in cities)

    def test_caching(self, name_pools_dir: Path) -> None:
        pools = NamePools(pools_dir=name_pools_dir)
        first = pools.male_names
        second = pools.male_names
        assert first is second  # Same object from cache

    def test_sample_player_name(self, name_pools_dir: Path) -> None:
        rng = random.Random(42)
        pools = NamePools(pools_dir=name_pools_dir, rng=rng)
        name = pools.sample_player_name("male")
        assert " " in name  # "First Last" format
        parts = name.split(" ")
        assert len(parts) == 2

    def test_sample_team_name(self, name_pools_dir: Path) -> None:
        rng = random.Random(42)
        pools = NamePools(pools_dir=name_pools_dir, rng=rng)
        team = pools.sample_team_name()
        assert " " in team
        suffixes = ("FC", "United", "Athletic", "Rovers", "City", "Town")
        assert any(team.endswith(s) for s in suffixes)

    def test_sample_unique_names_no_duplicates(self, name_pools_dir: Path) -> None:
        rng = random.Random(42)
        pools = NamePools(pools_dir=name_pools_dir, rng=rng)
        names = pools.sample_unique_player_names(20, gender="male")
        assert len(names) == 20
        assert len(set(names)) == 20  # All unique

    def test_sample_unique_names_respects_exclude(self, name_pools_dir: Path) -> None:
        rng = random.Random(42)
        pools = NamePools(pools_dir=name_pools_dir, rng=rng)
        exclude = {"Fezzik Took", "T'Challa Stark"}
        names = pools.sample_unique_player_names(10, gender="male", exclude=exclude)
        assert len(names) == 10
        assert not any(n in exclude for n in names)

    def test_reproducible_with_seed(self, name_pools_dir: Path) -> None:
        rng1 = random.Random(123)
        rng2 = random.Random(123)
        pools1 = NamePools(pools_dir=name_pools_dir, rng=rng1)
        pools2 = NamePools(pools_dir=name_pools_dir, rng=rng2)
        name1 = pools1.sample_player_name()
        name2 = pools2.sample_player_name()
        assert name1 == name2

    def test_missing_pool_file_raises(self, tmp_path: Path) -> None:
        pools = NamePools(pools_dir=tmp_path)
        with pytest.raises(FileNotFoundError):
            _ = pools.male_names
