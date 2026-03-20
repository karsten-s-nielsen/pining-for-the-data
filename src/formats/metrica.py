"""Read and de-identify Metrica Sports CSV tracking data.

Metrica CSV format (Games 1-2 style):
  - 3-row multi-line header: team names, jersey numbers, column labels
  - Wide format: each player has two columns (x, y) with 0-1 normalized coords
  - Column pattern: Period, Frame, Time [s], Home_11_x, Home_11_y, ..., Ball_x, Ball_y

This module reads the raw CSV, applies de-identification via a TwoLayerMapping,
and outputs clean CSV or Parquet with synthetic player identifiers.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import pandas as pd

from deidentify.mapping import TwoLayerMapping

# Regex to extract (Team, Jersey, Axis) from column names like "Home_14_x"
_PLAYER_COL_RE = re.compile(r"^(Home|Away)_(\d+)_(x|y)$")


def read_metrica_csv(path: Path) -> tuple[pd.DataFrame, list[str]]:
    """Read a Metrica tracking CSV with its 3-row header.

    Returns
    -------
    df : pd.DataFrame
        Tracking data with descriptive column names (e.g., Home_14_x).
    raw_columns : list[str]
        The constructed column names for reference.
    """
    text = path.read_text(encoding="utf-8")
    reader = csv.reader(io.StringIO(text))
    team_row = next(reader)
    jersey_row = next(reader)
    _column_row = next(reader)

    columns = _build_columns(team_row, jersey_row, _column_row)

    df = pd.read_csv(path, skiprows=3, header=None, names=columns)
    return df, columns


def _build_columns(team_row: list[str], jersey_row: list[str], column_row: list[str]) -> list[str]:
    """Build descriptive column names from the 3-row Metrica header.

    Produces: Period, Frame, Time [s], Home_11_x, Home_11_y, ..., Ball_x, Ball_y.
    """
    columns: list[str] = []
    last_team = ""
    last_player = ""

    for i, col_name in enumerate(column_row):
        stripped = col_name.strip()
        jersey = jersey_row[i].strip() if i < len(jersey_row) else ""
        team = team_row[i].strip() if i < len(team_row) else ""

        if stripped in ("Period", "Frame", "Time [s]"):
            columns.append(stripped)
        elif jersey == "Ball":
            last_team = "Ball"
            last_player = ""
            columns.append("Ball_x")
        elif jersey:
            last_team = team
            last_player = jersey
            columns.append(f"{team}_{jersey}_x")
        elif last_player and not stripped:
            columns.append(f"{last_team}_{last_player}_y")
            last_player = ""
        elif last_team == "Ball" and not stripped:
            columns.append("Ball_y")
            last_team = ""
        else:
            columns.append(f"col_{i}")

    return columns


def deidentify_columns(df: pd.DataFrame, mapping: TwoLayerMapping) -> pd.DataFrame:
    """Rename player columns using de-identified names from the mapping.

    Transforms columns like ``Home_14_x`` → ``tchalla_stark_x`` based on
    the jersey-to-identity mapping.

    Non-player columns (Period, Frame, Time, Ball) are preserved unchanged.
    Unmapped player columns (jersey not in roster) are dropped.
    """
    rename_map: dict[str, str] = {}
    drop_cols: list[str] = []

    for col in df.columns:
        match = _PLAYER_COL_RE.match(col)
        if not match:
            continue

        team_side = match.group(1).lower()
        jersey = int(match.group(2))
        axis = match.group(3)

        identity = mapping.resolve(jersey, team_side)
        if identity:
            rename_map[col] = f"{identity.player_id}_{axis}"
        else:
            drop_cols.append(col)

    result = df.drop(columns=drop_cols) if drop_cols else df
    return result.rename(columns=rename_map)


def extract_player_jerseys(df: pd.DataFrame) -> dict[str, list[int]]:
    """Extract home and away jersey numbers from the DataFrame columns.

    Returns dict with keys 'home' and 'away', each containing sorted jersey numbers.
    """
    jerseys: dict[str, set[int]] = {"home": set(), "away": set()}
    for col in df.columns:
        match = _PLAYER_COL_RE.match(col)
        if match:
            side = match.group(1).lower()
            jersey = int(match.group(2))
            jerseys[side].add(jersey)
    return {k: sorted(v) for k, v in jerseys.items()}


def write_csv(df: pd.DataFrame, path: Path) -> None:
    """Write de-identified tracking data as a clean CSV (no multi-row header)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def write_parquet(df: pd.DataFrame, path: Path) -> None:
    """Write de-identified tracking data as Parquet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False, engine="pyarrow")


def process_game(
    tracking_path: Path,
    roster_path: Path,
    output_dir: Path,
    output_format: str = "both",
) -> Path:
    """Full pipeline: read raw Metrica CSV → de-identify → write output.

    Parameters
    ----------
    tracking_path : Path
        Raw Metrica tracking CSV from the provider.
    roster_path : Path
        De-identified roster JSON (from pfd-generate-roster).
    output_dir : Path
        Directory for output files.
    output_format : str
        One of 'csv', 'parquet', or 'both'.

    Returns
    -------
    Path
        The output directory containing the written file(s).
    """
    mapping = TwoLayerMapping.from_roster_file(str(roster_path))
    df, _columns = read_metrica_csv(tracking_path)
    df_clean = deidentify_columns(df, mapping)

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = tracking_path.stem

    if output_format in ("csv", "both"):
        write_csv(df_clean, output_dir / f"{stem}.csv")
    if output_format in ("parquet", "both"):
        write_parquet(df_clean, output_dir / f"{stem}.parquet")

    return output_dir


def main() -> None:
    """CLI entry point for de-identifying a Metrica CSV."""
    import argparse

    parser = argparse.ArgumentParser(description="De-identify a Metrica tracking CSV")
    parser.add_argument("tracking", type=Path, help="Path to raw Metrica tracking CSV")
    parser.add_argument("--roster", type=Path, required=True, help="Path to roster JSON")
    parser.add_argument("--output-dir", type=Path, default=Path("output"), help="Output directory")
    parser.add_argument("--format", choices=["csv", "parquet", "both"], default="both", help="Output format")
    args = parser.parse_args()

    output = process_game(args.tracking, args.roster, args.output_dir, args.format)
    print(f"De-identified data written to {output}")
