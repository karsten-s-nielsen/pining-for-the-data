"""Tests for formats.metrica."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from deidentify.mapping import TwoLayerMapping
from deidentify.roster_generator import GameRoster, Player, TeamRoster
from formats.metrica import (
    deidentify_columns,
    extract_player_jerseys,
    read_metrica_csv,
    write_csv,
    write_parquet,
)


def _make_roster_for_fixture() -> GameRoster:
    """Roster matching sample_tracking.csv fixture (Home: 11, 14, 1; Away: 23, 7, 9)."""
    return GameRoster(
        game_id="fixture_test",
        date="2026-01-01",
        home=TeamRoster(
            team_name="Wakanda FC",
            roster=[
                Player(jersey=1, name="Fezzik Took", position="GK"),
                Player(jersey=11, name="Tormund Tully"),
                Player(jersey=14, name="Westley Montoya"),
            ],
        ),
        away=TeamRoster(
            team_name="Shire Town",
            roster=[
                Player(jersey=7, name="Bilbo Lannister"),
                Player(jersey=9, name="Samwise Baratheon"),
                Player(jersey=23, name="Gandalf Arryn"),
            ],
        ),
    )


class TestReadMetricaCsv:
    def test_reads_correct_shape(self, sample_tracking_csv: Path) -> None:
        df, columns = read_metrica_csv(sample_tracking_csv)
        assert len(df) == 3  # 3 data rows
        assert "Period" in columns
        assert "Frame" in columns
        assert "Ball_x" in columns
        assert "Ball_y" in columns

    def test_player_columns_present(self, sample_tracking_csv: Path) -> None:
        df, _columns = read_metrica_csv(sample_tracking_csv)
        assert "Home_11_x" in df.columns
        assert "Home_11_y" in df.columns
        assert "Away_23_x" in df.columns
        assert "Away_9_y" in df.columns

    def test_values_are_numeric(self, sample_tracking_csv: Path) -> None:
        df, _columns = read_metrica_csv(sample_tracking_csv)
        assert df["Home_11_x"].dtype in ("float64", "float32")
        assert df["Ball_x"].dtype in ("float64", "float32")


class TestExtractPlayerJerseys:
    def test_extracts_home_and_away(self, sample_tracking_csv: Path) -> None:
        df, _columns = read_metrica_csv(sample_tracking_csv)
        jerseys = extract_player_jerseys(df)
        assert jerseys["home"] == [1, 11, 14]
        assert jerseys["away"] == [7, 9, 23]


class TestDeidentifyColumns:
    def test_renames_player_columns(self, sample_tracking_csv: Path) -> None:
        df, _columns = read_metrica_csv(sample_tracking_csv)
        mapping = TwoLayerMapping(_make_roster_for_fixture())
        result = deidentify_columns(df, mapping)

        assert "tormund_tully_x" in result.columns
        assert "tormund_tully_y" in result.columns
        assert "fezzik_took_x" in result.columns
        assert "gandalf_arryn_x" in result.columns

        # Original columns should be gone
        assert "Home_11_x" not in result.columns
        assert "Away_23_x" not in result.columns

    def test_preserves_non_player_columns(self, sample_tracking_csv: Path) -> None:
        df, _columns = read_metrica_csv(sample_tracking_csv)
        mapping = TwoLayerMapping(_make_roster_for_fixture())
        result = deidentify_columns(df, mapping)

        assert "Period" in result.columns
        assert "Frame" in result.columns
        assert "Time [s]" in result.columns
        assert "Ball_x" in result.columns
        assert "Ball_y" in result.columns

    def test_drops_unmapped_players(self, sample_tracking_csv: Path) -> None:
        """If a jersey in the data has no roster entry, its columns are dropped."""
        df, _columns = read_metrica_csv(sample_tracking_csv)
        # Roster missing jersey #14
        partial_roster = GameRoster(
            game_id="partial",
            date="2026-01-01",
            home=TeamRoster(
                team_name="Wakanda FC",
                roster=[
                    Player(jersey=1, name="Fezzik Took"),
                    Player(jersey=11, name="Tormund Tully"),
                    # #14 intentionally missing
                ],
            ),
            away=TeamRoster(
                team_name="Shire Town",
                roster=[
                    Player(jersey=7, name="Bilbo Lannister"),
                    Player(jersey=9, name="Samwise Baratheon"),
                    Player(jersey=23, name="Gandalf Arryn"),
                ],
            ),
        )
        mapping = TwoLayerMapping(partial_roster)
        result = deidentify_columns(df, mapping)

        # #14 columns should be dropped
        assert not any("14" in col for col in result.columns)
        assert not any("westley" in col for col in result.columns)
        # Others should be present
        assert "tormund_tully_x" in result.columns

    def test_data_values_preserved(self, sample_tracking_csv: Path) -> None:
        df, _columns = read_metrica_csv(sample_tracking_csv)
        mapping = TwoLayerMapping(_make_roster_for_fixture())
        result = deidentify_columns(df, mapping)

        # Values should be the same — just column names changed
        original_home_11_x = df["Home_11_x"].tolist()
        renamed_home_11_x = result["tormund_tully_x"].tolist()
        assert original_home_11_x == renamed_home_11_x


class TestWriteOutput:
    def test_write_csv(self, sample_tracking_csv: Path, tmp_path: Path) -> None:
        df, _columns = read_metrica_csv(sample_tracking_csv)
        mapping = TwoLayerMapping(_make_roster_for_fixture())
        result = deidentify_columns(df, mapping)

        out_path = tmp_path / "output.csv"
        write_csv(result, out_path)

        loaded = pd.read_csv(out_path)
        assert len(loaded) == 3
        assert "tormund_tully_x" in loaded.columns

    def test_write_parquet(self, sample_tracking_csv: Path, tmp_path: Path) -> None:
        df, _columns = read_metrica_csv(sample_tracking_csv)
        mapping = TwoLayerMapping(_make_roster_for_fixture())
        result = deidentify_columns(df, mapping)

        out_path = tmp_path / "output.parquet"
        write_parquet(result, out_path)

        loaded = pd.read_parquet(out_path)
        assert len(loaded) == 3
        assert "fezzik_took_x" in loaded.columns

    def test_process_game_end_to_end(self, sample_tracking_csv: Path, tmp_path: Path) -> None:
        """Full pipeline: raw CSV → roster JSON → de-identified output."""
        from formats.metrica import process_game

        roster = _make_roster_for_fixture()
        roster_path = tmp_path / "roster.json"
        with open(roster_path, "w", encoding="utf-8") as f:
            json.dump(roster.to_dict(), f)

        output_dir = tmp_path / "output"
        process_game(sample_tracking_csv, roster_path, output_dir, output_format="both")

        csv_files = list(output_dir.glob("*.csv"))
        parquet_files = list(output_dir.glob("*.parquet"))
        assert len(csv_files) == 1
        assert len(parquet_files) == 1

        # Verify de-identification applied
        loaded = pd.read_csv(csv_files[0])
        assert "tormund_tully_x" in loaded.columns
        assert "Home_11_x" not in loaded.columns
