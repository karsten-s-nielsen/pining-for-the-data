import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
NAME_POOLS_DIR = Path(__file__).parent.parent.parent / "name_pools"
_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def name_pools_dir() -> Path:
    return NAME_POOLS_DIR


@pytest.fixture(scope="session")
def load_script():
    """Return a loader that imports scripts/<name>.py as a module.

    scripts/ is not a package, so tests load script modules by path. Note:
    loading a script executes its top-level imports (e.g.
    ``from mock_api.upload import upload_game``), so even "pure logic" tests
    transitively import the boto3-backed module. That's fine — no AWS calls
    occur at import time.
    """

    def _load(name: str):
        path = _SCRIPTS_DIR / f"{name}.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"cannot load script module {name}"
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    return _load


# --- StatsBomb synthetic bundle (spec 2026-08-12) -------------------------------
# Every id, name, date, body metric and rating below is INVENTED wholesale. Nothing
# is copied — or lightly adapted — from a delivery. A real birth date, height and
# club held together by a renamed player is still identifying, so a "synthetic"
# fixture built by renaming is not synthetic. No licensed (id -> entity) tuple, and
# no real person's attributes, are committed.


@pytest.fixture
def sb_match_row() -> dict:
    """A de-pivoted match row as statsbombpy flattens it."""
    return {
        "match_id": 9999999,
        "match_date": "2026-01-01",
        "kick_off": "20:00:00.000",
        "competition": "Wakanda - Vibranium League",
        "season": "2026",
        "home_team": "Alpha City",
        "away_team": "Beta United",
        "home_score": 2.0,
        "away_score": 1.0,
        "attendance": None,
        "behind_closed_doors": False,
        "neutral_ground": False,
        "collection_status": "Complete",
        "play_status": "Normal",
        "match_status": "available",
        "match_status_360": "available",
        "last_updated": "2026-01-02T00:00:00.000000",
        "last_updated_360": "2026-01-02T00:00:00.000000",
        "match_week": 15,
        "competition_stage": "Regular Season",
        "stadium": "Alpha Arena",
        "referee": "Sam Whistle",
        "home_managers": "Dana Coach",
        "away_managers": "Robin Boss",
        "data_version": "1.1.0",
        "shot_fidelity_version": "2",
        "xy_fidelity_version": "2",
    }


@pytest.fixture
def sb_competition_row() -> dict:
    return {
        "competition_id": 33,
        "country_name": "Wakanda",
        "competition_name": "Vibranium League",
        "season_id": 902,
        "season_name": "2026",
    }


@pytest.fixture
def sb_events() -> list[dict]:
    """Coherent event stream: two periods, a Half End, exactly two teams."""
    return [
        {"id": "e1", "period": 1, "type": {"name": "Pass"}, "team": {"id": 90001, "name": "Alpha City"}},
        {"id": "e2", "period": 2, "type": {"name": "Pass"}, "team": {"id": 90007, "name": "Beta United"}},
        {"id": "e3", "period": 2, "type": {"name": "Half End"}, "team": {"id": 90001, "name": "Alpha City"}},
    ]


@pytest.fixture
def sb_lineups() -> list[dict]:
    """A WHOLE delivery: both squads, away block first as the real feed orders them.

    Both team blocks are present deliberately. With only one, `team_gender` resolves
    to None for the missing side and `home_team_gender` is null in every end-to-end
    assertion without anything noticing.

    Every value here is invented — ids, names, dates, body metrics, jersey numbers,
    country and the proprietary rating block. None is derived from any delivery.
    """
    return [
        {
            "team_id": 90007,
            "team_name": "Beta United",
            "lineup": [
                {
                    "player_id": 77001,
                    "player_name": "Rosalind Amara Fenwick",
                    "player_nickname": "Roz Fenwick",
                    "birth_date": "2001-02-03",
                    "player_gender": "female",
                    "player_height": 168.5,
                    "player_weight": 64.125,
                    "jersey_number": 99,
                    "country": {"id": 993, "name": "Wakanda"},
                    "skills": {"HOPS": {"rating": 0.5137, "raw_rating": 123.4567}},
                    "stats": [],
                    "positions": [
                        {"position_id": 91, "position": "Center Forward", "from": "00:45:00.000", "from_period": 2},
                        {"position_id": 92, "position": "Right Wing", "from": "00:00:00.000", "from_period": 1},
                    ],
                },
                {
                    # No nickname — the full name must land in `nickname`, unsplit.
                    "player_id": 77004,
                    "player_name": "Ingrid Beatrix Halvorsen",
                    "player_nickname": "",
                    "birth_date": "1999-11-12",
                    "player_gender": "female",
                    "player_height": 181.0,
                    "player_weight": 71.875,
                    "jersey_number": 90,
                    "country": {"id": 993, "name": "Wakanda"},
                    "positions": [],
                },
            ],
        },
        {
            # The HOME side, listed second — see the docstring.
            "team_id": 90001,
            "team_name": "Alpha City",
            "lineup": [
                {
                    "player_id": 77002,
                    "player_name": "Perrin Solveig Ashgrove",
                    "player_nickname": "Perry Ashgrove",
                    "player_gender": "female",
                    "country": {"id": 993, "name": "Wakanda"},
                    "positions": [],
                },
            ],
        },
    ]


@pytest.fixture
def sb_bundle_dir(
    tmp_path: Path,
    sb_match_row: dict,
    sb_competition_row: dict,
    sb_events: list[dict],
    sb_lineups: list[dict],
) -> Callable[..., Path]:
    """Write a complete synthetic StatsBomb bundle; return a factory.

    Calling the returned factory with no arguments writes the canonical bundle and
    returns its root. Pass `overrides={"frames.json": [...]}` to replace one file —
    used by the pre-flight tests to inject an incoherent delivery.
    """

    def _build(overrides: dict[str, object] | None = None) -> Path:
        root = tmp_path / "sb_bundle"
        root.mkdir(parents=True, exist_ok=True)
        payloads: dict[str, object] = {
            "matches.json": {col: {"7": val} for col, val in sb_match_row.items()},
            "competitions.json": {col: {"14": val} for col, val in sb_competition_row.items()},
            "events.json": sb_events,
            "frames.json": [{"event_uuid": "e1"}],
            "lineups.json": sb_lineups,
        }
        payloads.update(overrides or {})
        for name, payload in payloads.items():
            (root / name).write_text(json.dumps(payload), encoding="utf-8")
        return root

    return _build
