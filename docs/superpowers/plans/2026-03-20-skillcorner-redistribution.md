# SkillCorner Redistribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add SkillCorner V3 format support and redistribute MIT-licensed open tracking data as-is through the mock provider API and HuggingFace Hub.

**Architecture:** SkillCorner open data (MIT-licensed, 10 A-League matches) is redistributed without de-identification — real player names and teams are preserved, maximizing analytical value and avoiding confusion with the original source. A format reader/writer validates SkillCorner V3 files (match JSON + tracking JSONL at 10fps). The existing de-identification system (`RosterGenerator`/`TwoLayerMapping` with featured names and Wakanda FC) is preserved untouched for future use with private data that requires anonymization. The mock API and upload CLI are already provider-agnostic and need no changes. Respo.Vision scaffolding is preserved for future use.

**Tech Stack:** Python 3.12+, JSON/JSONL processing (stdlib `json`), pytest, ruff, pyright

---

## File Structure

### New files
| File | Responsibility |
|---|---|
| `src/formats/skillcorner.py` | Read/write/validate SkillCorner V3 match JSON + tracking JSONL |
| `src/tests/test_skillcorner_format.py` | Tests for the SkillCorner format handler |
| `src/tests/fixtures/sample_match.json` | Minimal SkillCorner V3 match metadata fixture (6 players) |
| `src/tests/fixtures/sample_tracking.jsonl` | Minimal SkillCorner V3 tracking data fixture (3 frames) |
| `NOTICE` | Attribution for redistributed SkillCorner data (MIT license) |

### Modified files
| File | Changes |
|---|---|
| `src/deidentify/mapping.py` | Remove `to_column_rename_map()` (dead code after Metrica removal) |
| `src/tests/test_mapping.py` | Remove `to_column_rename_map` tests |
| `src/tests/conftest.py` | Replace `sample_tracking_csv` fixture with `sample_match_json` + `sample_tracking_jsonl` |
| `src/formats/convert.py` | Update scaffolding: `respovision_to_skillcorner` (was `respovision_to_metrica`) |
| `src/formats/__init__.py` | Update docstring |
| `src/publish/hf_push.py` | Update dataset card template for SkillCorner format + MIT source license |
| `pyproject.toml` | Update `pining-ingest` entry point to `formats.skillcorner:main` |
| `CLAUDE.md` | Replace format references with SkillCorner |
| `README.md` | Update project description for SkillCorner |
| `ARCHITECTURE.md` | Replace format references with SkillCorner |
| `terraform/docs/setup.md` | Update upload examples for SkillCorner artifacts |

### Deleted files
| File | Reason |
|---|---|
| `src/formats/metrica.py` | Replaced by `skillcorner.py` |
| `src/tests/test_metrica_format.py` | Replaced by `test_skillcorner_format.py` |
| `src/tests/fixtures/sample_tracking.csv` | Replaced by `sample_match.json` + `sample_tracking.jsonl` |

---

## Task 1: Create SkillCorner Test Fixtures

**Files:**
- Create: `src/tests/fixtures/sample_match.json`
- Create: `src/tests/fixtures/sample_tracking.jsonl`

These fixtures must be valid SkillCorner V3 format (parseable by kloppy) but minimal for fast tests.

- [ ] **Step 1: Create sample_match.json**

6 players (3 home, 3 away). Home jerseys 1, 11, 17. Away jerseys 7, 9, 23.

```json
{
  "id": 9999999,
  "home_team_score": 2,
  "away_team_score": 1,
  "date_time": "2025-01-15T19:00:00Z",
  "status": "closed",
  "pitch_length": 105,
  "pitch_width": 68,
  "ball": {"trackable_object": 55},
  "home_team_side": ["right_to_left", "left_to_right"],
  "home_team": {
    "id": 100,
    "name": "Auckland FC",
    "short_name": "Auckland",
    "acronym": "AKL"
  },
  "away_team": {
    "id": 200,
    "name": "Wellington Phoenix FC",
    "short_name": "Wellington",
    "acronym": "WEL"
  },
  "home_team_kit": {
    "id": 1, "team_id": 100,
    "season": {"id": 95, "start_year": 2024, "end_year": 2025, "name": "2024/2025"},
    "name": "Home", "jersey_color": "#001489", "number_color": "#FFFFFF"
  },
  "away_team_kit": {
    "id": 2, "team_id": 200,
    "season": {"id": 95, "start_year": 2024, "end_year": 2025, "name": "2024/2025"},
    "name": "Away", "jersey_color": "#FFC72C", "number_color": "#000000"
  },
  "home_team_coach": {"id": 1, "first_name": "John", "last_name": "Smith"},
  "away_team_coach": {"id": 2, "first_name": "Jane", "last_name": "Doe"},
  "home_team_playing_time": {"minutes_tip": 32.5, "minutes_otip": 12.5},
  "away_team_playing_time": {"minutes_tip": 12.5, "minutes_otip": 32.5},
  "competition_edition": {
    "id": 870,
    "competition": {"id": 61, "area": "Australia", "name": "A-League Men", "gender": "male", "age_group": "adult"},
    "season": {"id": 95, "start_year": 2024, "end_year": 2025, "name": "2024/2025"},
    "name": "A-League Men 2024/2025"
  },
  "competition_round": {"id": 1, "name": "Round 10", "round_number": 10, "potential_overtime": false},
  "stadium": {"id": 500, "name": "Go Media Stadium", "city": "Auckland", "capacity": 25000},
  "referees": [
    {"id": 301, "first_name": "Alex", "last_name": "King", "referee_role": 1, "start_time": "00:00:00", "end_time": null, "replaced_by": null, "trackable_object": 60}
  ],
  "match_periods": [
    {"period": 1, "name": "First half", "start_frame": 10, "end_frame": 30, "duration_frames": 20, "duration_minutes": 45.0},
    {"period": 2, "name": "Second half", "start_frame": 40, "end_frame": 60, "duration_frames": 20, "duration_minutes": 45.0}
  ],
  "players": [
    {
      "id": 1001, "first_name": "Hiroshi", "last_name": "Tanaka", "short_name": "H. Tanaka",
      "birthday": "1998-03-15", "gender": "male",
      "team_id": 100, "team_player_id": 5001, "trackable_object": 101,
      "number": 1,
      "player_role": {"id": 0, "name": "Goalkeeper", "acronym": "GK", "position_group": "Goalkeeper"},
      "start_time": "00:00:00", "end_time": null,
      "yellow_card": 0, "red_card": 0, "injured": false, "goal": 0, "own_goal": 0
    },
    {
      "id": 1002, "first_name": "Marcus", "last_name": "Webb", "short_name": "M. Webb",
      "birthday": "2000-07-22", "gender": "male",
      "team_id": 100, "team_player_id": 5002, "trackable_object": 102,
      "number": 11,
      "player_role": {"id": 12, "name": "Left Wing", "acronym": "LW", "position_group": "Forward"},
      "start_time": "00:00:00", "end_time": null,
      "yellow_card": 1, "red_card": 0, "injured": false, "goal": 1, "own_goal": 0
    },
    {
      "id": 1003, "first_name": "Diego", "last_name": "Morales", "short_name": "D. Morales",
      "birthday": "1996-11-01", "gender": "male",
      "team_id": 100, "team_player_id": 5003, "trackable_object": 103,
      "number": 17,
      "player_role": {"id": 15, "name": "Striker", "acronym": "CF", "position_group": "Forward"},
      "start_time": "00:00:00", "end_time": null,
      "yellow_card": 0, "red_card": 0, "injured": false, "goal": 2, "own_goal": 0
    },
    {
      "id": 2001, "first_name": "Liam", "last_name": "O'Connor", "short_name": "L. O'Connor",
      "birthday": "1999-01-10", "gender": "male",
      "team_id": 200, "team_player_id": 6001, "trackable_object": 201,
      "number": 7,
      "player_role": {"id": 13, "name": "Right Wing", "acronym": "RW", "position_group": "Forward"},
      "start_time": "00:00:00", "end_time": null,
      "yellow_card": 0, "red_card": 0, "injured": false, "goal": 1, "own_goal": 0
    },
    {
      "id": 2002, "first_name": "Ravi", "last_name": "Patel", "short_name": "R. Patel",
      "birthday": "1997-05-30", "gender": "male",
      "team_id": 200, "team_player_id": 6002, "trackable_object": 202,
      "number": 9,
      "player_role": {"id": 15, "name": "Striker", "acronym": "CF", "position_group": "Forward"},
      "start_time": "00:00:00", "end_time": null,
      "yellow_card": 0, "red_card": 0, "injured": false, "goal": 0, "own_goal": 0
    },
    {
      "id": 2003, "first_name": "James", "last_name": "Mitchell", "short_name": "J. Mitchell",
      "birthday": "2001-09-18", "gender": "male",
      "team_id": 200, "team_player_id": 6003, "trackable_object": 203,
      "number": 23,
      "player_role": {"id": 0, "name": "Goalkeeper", "acronym": "GK", "position_group": "Goalkeeper"},
      "start_time": "00:00:00", "end_time": null,
      "yellow_card": 0, "red_card": 0, "injured": false, "goal": 0, "own_goal": 0
    }
  ]
}
```

- [ ] **Step 2: Create sample_tracking.jsonl**

3 frames: one pre-match (null period/timestamp), two active period-1 frames. Player IDs match `trackable_object` values from match JSON.

```jsonl
{"frame": 0, "timestamp": null, "period": null, "ball_data": {"x": null, "y": null, "z": null, "is_detected": null}, "possession": {"player_id": null, "group": null}, "image_corners_projection": {"x_top_left": null, "y_top_left": null, "x_bottom_left": null, "y_bottom_left": null, "x_bottom_right": null, "y_bottom_right": null, "x_top_right": null, "y_top_right": null}, "player_data": []}
{"frame": 10, "timestamp": "00:00:00.00", "period": 1, "ball_data": {"x": 0.0, "y": -0.31, "z": 0.2, "is_detected": true}, "possession": {"player_id": 101, "group": "home team"}, "image_corners_projection": {"x_top_left": -31.67, "y_top_left": 39.0, "x_bottom_left": -8.84, "y_bottom_left": -28.38, "x_bottom_right": 7.9, "y_bottom_right": -28.25, "x_top_right": 28.27, "y_top_right": 39.0}, "player_data": [{"player_id": 101, "x": -41.97, "y": -0.61, "is_detected": true}, {"player_id": 102, "x": -10.5, "y": 15.3, "is_detected": true}, {"player_id": 103, "x": 5.2, "y": -3.8, "is_detected": false}, {"player_id": 201, "x": 20.1, "y": 8.7, "is_detected": true}, {"player_id": 202, "x": 35.0, "y": -12.4, "is_detected": true}, {"player_id": 203, "x": 42.5, "y": 0.3, "is_detected": true}]}
{"frame": 11, "timestamp": "00:00:00.10", "period": 1, "ball_data": {"x": 1.2, "y": -0.15, "z": 0.1, "is_detected": true}, "possession": {"player_id": 102, "group": "home team"}, "image_corners_projection": {"x_top_left": -31.50, "y_top_left": 39.0, "x_bottom_left": -8.70, "y_bottom_left": -28.40, "x_bottom_right": 8.1, "y_bottom_right": -28.20, "x_top_right": 28.40, "y_top_right": 39.0}, "player_data": [{"player_id": 101, "x": -41.97, "y": -0.62, "is_detected": true}, {"player_id": 102, "x": -9.8, "y": 15.1, "is_detected": true}, {"player_id": 103, "x": 5.5, "y": -3.5, "is_detected": false}, {"player_id": 201, "x": 19.8, "y": 8.9, "is_detected": true}, {"player_id": 202, "x": 34.7, "y": -12.1, "is_detected": true}, {"player_id": 203, "x": 42.5, "y": 0.2, "is_detected": true}]}
```

---

## Task 2: Clean Up TwoLayerMapping

**Files:**
- Modify: `src/deidentify/mapping.py:91-104`
- Modify: `src/tests/test_mapping.py`

Remove `to_column_rename_map()` — dead code after Metrica removal. The rest of TwoLayerMapping stays intact for future use with private data.

- [ ] **Step 1: Remove `to_column_rename_map` method**

Delete the `to_column_rename_map` method from `TwoLayerMapping` in `src/deidentify/mapping.py` (lines 91-104).

- [ ] **Step 2: Remove its tests**

Delete these two test methods from `TestTwoLayerMapping` in `src/tests/test_mapping.py`:
- `test_column_rename_map`
- `test_column_rename_map_away`

- [ ] **Step 3: Run mapping tests**

Run: `python -m pytest src/tests/test_mapping.py -v`
Expected: All remaining tests PASS

---

## Task 3: SkillCorner Format Reader (TDD)

**Files:**
- Create: `src/formats/skillcorner.py`
- Create: `src/tests/test_skillcorner_format.py`

- [ ] **Step 1: Write failing tests**

```python
import json
from pathlib import Path

import pytest

from formats.skillcorner import read_match_json, read_tracking_jsonl

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestReadMatchJson:
    def setup_method(self) -> None:
        self.match = read_match_json(FIXTURES_DIR / "sample_match.json")

    def test_returns_dict(self) -> None:
        assert isinstance(self.match, dict)

    def test_has_players(self) -> None:
        assert len(self.match["players"]) == 6

    def test_has_teams(self) -> None:
        assert self.match["home_team"]["name"] == "Auckland FC"
        assert self.match["away_team"]["name"] == "Wellington Phoenix FC"

    def test_has_pitch_dimensions(self) -> None:
        assert self.match["pitch_length"] == 105
        assert self.match["pitch_width"] == 68


class TestReadTrackingJsonl:
    def setup_method(self) -> None:
        self.frames = read_tracking_jsonl(FIXTURES_DIR / "sample_tracking.jsonl")

    def test_returns_list_of_dicts(self) -> None:
        assert isinstance(self.frames, list)
        assert all(isinstance(f, dict) for f in self.frames)

    def test_frame_count(self) -> None:
        assert len(self.frames) == 3

    def test_pre_match_frame_has_null_period(self) -> None:
        assert self.frames[0]["period"] is None
        assert self.frames[0]["player_data"] == []

    def test_active_frame_has_player_data(self) -> None:
        assert self.frames[1]["period"] == 1
        assert len(self.frames[1]["player_data"]) == 6
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest src/tests/test_skillcorner_format.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement readers**

Create `src/formats/skillcorner.py`:

```python
"""SkillCorner V3 format: match metadata JSON + tracking JSONL (10 fps)."""

from __future__ import annotations

import json
from pathlib import Path


def read_match_json(path: Path) -> dict:
    """Read a SkillCorner V3 match metadata JSON file."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_tracking_jsonl(path: Path) -> list[dict]:
    """Read a SkillCorner V3 tracking JSONL file (one JSON object per frame)."""
    frames: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                frames.append(json.loads(line))
    return frames
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest src/tests/test_skillcorner_format.py -v`
Expected: PASS

---

## Task 4: Extract Player Jerseys + Writer (TDD)

**Files:**
- Modify: `src/formats/skillcorner.py`
- Modify: `src/tests/test_skillcorner_format.py`

- [ ] **Step 1: Write failing tests**

Add to `src/tests/test_skillcorner_format.py`:

```python
from formats.skillcorner import extract_player_jerseys, write_match_json, write_tracking_jsonl


class TestExtractPlayerJerseys:
    def test_extracts_sorted_jerseys_by_team(self) -> None:
        match = read_match_json(FIXTURES_DIR / "sample_match.json")
        jerseys = extract_player_jerseys(match)
        assert jerseys["home"] == [1, 11, 17]
        assert jerseys["away"] == [7, 9, 23]


class TestWriteOutput:
    def test_write_match_json(self, tmp_path: Path) -> None:
        match = read_match_json(FIXTURES_DIR / "sample_match.json")
        out = tmp_path / "match.json"
        write_match_json(match, out)
        reloaded = read_match_json(out)
        assert reloaded["id"] == match["id"]
        assert len(reloaded["players"]) == len(match["players"])

    def test_write_tracking_jsonl(self, tmp_path: Path) -> None:
        frames = read_tracking_jsonl(FIXTURES_DIR / "sample_tracking.jsonl")
        out = tmp_path / "tracking.jsonl"
        write_tracking_jsonl(frames, out)
        reloaded = read_tracking_jsonl(out)
        assert len(reloaded) == len(frames)
        assert reloaded[1]["frame"] == frames[1]["frame"]
        assert reloaded[1]["player_data"] == frames[1]["player_data"]
```

- [ ] **Step 2: Implement**

Add to `src/formats/skillcorner.py`:

```python
def extract_player_jerseys(match_data: dict) -> dict[str, list[int]]:
    """Extract sorted jersey numbers per team from match metadata."""
    home_team_id = match_data["home_team"]["id"]
    home: list[int] = []
    away: list[int] = []
    for player in match_data["players"]:
        if player["team_id"] == home_team_id:
            home.append(player["number"])
        else:
            away.append(player["number"])
    return {"home": sorted(home), "away": sorted(away)}


def write_match_json(match_data: dict, path: Path) -> None:
    """Write a SkillCorner V3 match metadata JSON file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(match_data, f, indent=2, ensure_ascii=False)


def write_tracking_jsonl(frames: list[dict], path: Path) -> None:
    """Write a SkillCorner V3 tracking JSONL file."""
    with open(path, "w", encoding="utf-8") as f:
        for frame in frames:
            f.write(json.dumps(frame, ensure_ascii=False) + "\n")
```

- [ ] **Step 3: Run all tests**

Run: `python -m pytest src/tests/test_skillcorner_format.py -v`
Expected: All PASS

---

## Task 5: Validation CLI (TDD)

**Files:**
- Modify: `src/formats/skillcorner.py`
- Modify: `src/tests/test_skillcorner_format.py`

The `pining-ingest` CLI validates SkillCorner V3 files and optionally copies them to an output directory. No de-identification — data is redistributed as-is.

- [ ] **Step 1: Write failing test**

```python
from formats.skillcorner import validate_game


class TestValidateGame:
    def test_valid_game(self) -> None:
        result = validate_game(
            match_path=FIXTURES_DIR / "sample_match.json",
            tracking_path=FIXTURES_DIR / "sample_tracking.jsonl",
        )
        assert result["valid"] is True
        assert result["match_id"] == 9999999
        assert result["player_count"] == 6
        assert result["frame_count"] == 3

    def test_copies_to_output_dir(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        validate_game(
            match_path=FIXTURES_DIR / "sample_match.json",
            tracking_path=FIXTURES_DIR / "sample_tracking.jsonl",
            output_dir=output_dir,
        )
        assert (output_dir / "match.json").exists()
        assert (output_dir / "tracking.jsonl").exists()
```

- [ ] **Step 2: Implement `validate_game` and `main`**

Add to `src/formats/skillcorner.py`:

```python
import argparse
import shutil


def validate_game(
    match_path: Path,
    tracking_path: Path,
    output_dir: Path | None = None,
) -> dict:
    """Validate a SkillCorner V3 game and optionally copy to output directory.

    Returns validation summary with match_id, player_count, frame_count.
    """
    match_data = read_match_json(match_path)
    frames = read_tracking_jsonl(tracking_path)

    result = {
        "valid": True,
        "match_id": match_data["id"],
        "player_count": len(match_data["players"]),
        "frame_count": len(frames),
    }

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        write_match_json(match_data, output_dir / "match.json")
        write_tracking_jsonl(frames, output_dir / "tracking.jsonl")

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate SkillCorner V3 tracking data")
    parser.add_argument("match", type=Path, help="Path to match metadata JSON")
    parser.add_argument("tracking", type=Path, help="Path to tracking JSONL")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Copy validated files to this directory")
    args = parser.parse_args()

    result = validate_game(args.match, args.tracking, args.output_dir)
    print(f"Match {result['match_id']}: {result['player_count']} players, {result['frame_count']} frames — OK")
```

- [ ] **Step 3: Run all tests**

Run: `python -m pytest src/tests/test_skillcorner_format.py -v`
Expected: All PASS

---

## Task 6: Clean Up and Update Config

**Files:**
- Delete: `src/formats/metrica.py`
- Delete: `src/tests/test_metrica_format.py`
- Delete: `src/tests/fixtures/sample_tracking.csv`
- Modify: `src/tests/conftest.py`
- Modify: `src/formats/convert.py`
- Modify: `src/formats/__init__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Delete old files**

```bash
rm src/formats/metrica.py
rm src/tests/test_metrica_format.py
rm src/tests/fixtures/sample_tracking.csv
```

- [ ] **Step 2: Update conftest.py**

Replace `sample_tracking_csv` fixture with SkillCorner fixtures:

```python
import pytest
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
NAME_POOLS_DIR = Path(__file__).parent.parent.parent / "name_pools"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def name_pools_dir() -> Path:
    return NAME_POOLS_DIR


@pytest.fixture
def sample_match_json(fixtures_dir: Path) -> Path:
    return fixtures_dir / "sample_match.json"


@pytest.fixture
def sample_tracking_jsonl(fixtures_dir: Path) -> Path:
    return fixtures_dir / "sample_tracking.jsonl"
```

- [ ] **Step 3: Update convert.py scaffolding**

Replace `respovision_to_metrica` references with `respovision_to_skillcorner`. Keep functions as `NotImplementedError` stubs. Update docstrings to reference SkillCorner V3 format as the output target.

- [ ] **Step 4: Update formats/__init__.py**

```python
"""Provider format readers/writers (SkillCorner V3 JSON/JSONL, Respo.Vision JSON future)."""
```

- [ ] **Step 5: Update pyproject.toml entry point**

Change:
```toml
pining-ingest = "formats.metrica:main"
```
To:
```toml
pining-ingest = "formats.skillcorner:main"
```

- [ ] **Step 6: Run full test suite**

Run: `python -m pytest src/tests/ -v`
Expected: All tests PASS

- [ ] **Step 7: Run linting and type checks**

Run: `python -m ruff check src/ && python -m ruff format --check src/ && python -m pyright src/`
Expected: Clean (no errors)

---

## Task 7: Update Documentation and Add NOTICE

**Files:**
- Create: `NOTICE`
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `terraform/docs/setup.md`
- Modify: `src/publish/hf_push.py`

Note: Historical docs (`docs/plans/`, `docs/specs/`) are left as-is — they document past decisions. Mock API test files use "metrica" as a generic provider name string, which is fine since the API is provider-agnostic.

- [ ] **Step 1: Create NOTICE file**

```
This project redistributes data from the following sources:

SkillCorner Open Data
  Repository: https://github.com/SkillCorner/opendata
  License: MIT
  Copyright (c) 2020 SkillCorner

  Permission is hereby granted, free of charge, to any person obtaining a copy
  of this software and associated documentation files (the "Software"), to deal
  in the Software without restriction, including without limitation the rights
  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
  copies of the Software, and to permit persons to whom the Software is
  furnished to do so, subject to the following conditions:

  The above copyright notice and this permission notice shall be included in all
  copies or substantial portions of the Software.
```

- [ ] **Step 2: Update CLAUDE.md**

Key changes:
- Architecture: `src/formats/` → "provider format readers/writers (SkillCorner V3 JSON/JSONL, Respo.Vision JSON future)"
- De-identification: note it exists for future private data, not used for SkillCorner redistribution
- CLI entry points: `pining-ingest` → "validate SkillCorner V3 files, optionally copy to output directory"
- Keep Respo.Vision as a future format

- [ ] **Step 3: Update README.md**

Update all references. Key lines to change:
- Description: reference SkillCorner V3 format and MIT-licensed source data
- Clarify: redistribution is as-is (no de-identification of open data), de-identification reserved for private data
- Format table: "SkillCorner V3 JSON/JSONL" as the implemented format
- API examples: update provider name in curl examples
- CLI usage: `pining-ingest match.json tracking.jsonl` (not CSV)
- Credit SkillCorner and link to NOTICE

- [ ] **Step 4: Update ARCHITECTURE.md**

Replace all format-specific references with SkillCorner V3. Keep Respo.Vision as future format. Document the two modes: as-is redistribution (SkillCorner) vs de-identification (future private data).

- [ ] **Step 5: Update terraform/docs/setup.md**

Update "upload data" examples to use SkillCorner artifacts (match.json, tracking.jsonl). Keep API endpoint table unchanged (provider-agnostic).

- [ ] **Step 6: Update HuggingFace dataset card in `src/publish/hf_push.py`**

Update `DATASET_CARD_TEMPLATE`:
- Data format: SkillCorner V3 (match JSON + tracking JSONL at 10fps)
- License: MIT (redistributed as-is from SkillCorner open data)
- Source acknowledgment: "Tracking data from SkillCorner open data (MIT license). See NOTICE."
- Usage snippet: show loading match JSON + tracking JSONL

- [ ] **Step 7: Run full test suite one final time**

Run: `python -m pytest src/tests/ -v && python -m ruff check src/ && python -m pyright src/`
Expected: All clean

---

## Task 8: Final Verification and Commit

**Files:** All modified/created/deleted files from Tasks 1–7

- [ ] **Step 1: Verify no stale references**

Run: `grep -r "metrica" src/ --include="*.py" -l` (should return zero results)
Run: `grep -r "metrica" CLAUDE.md README.md ARCHITECTURE.md` (should return zero results)
Run: `grep -ri "Metrica" pyproject.toml` (should return zero results)

- [ ] **Step 2: Run full CI-equivalent checks**

```bash
python -m ruff check src/
python -m ruff format --check src/
python -m pyright src/
python -m pytest src/tests/ -v --tb=short
```

All must pass.

- [ ] **Step 3: Stage and commit**

```bash
git add -A
git commit -m "feat: add SkillCorner V3 format for tracking data redistribution

Add SkillCorner V3 format handler (match JSON + tracking JSONL at 10fps)
for as-is redistribution of MIT-licensed open tracking data. Includes
format reader/writer, validation CLI, and proper NOTICE attribution.
De-identification system preserved for future private data use cases.
Respo.Vision format scaffolding preserved for future use.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```
