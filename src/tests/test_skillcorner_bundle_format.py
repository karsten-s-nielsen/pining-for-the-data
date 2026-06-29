"""Unit tests for formats.skillcorner_bundle (pure reader; synthetic data only)."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

import pytest

from canonical.models import PlayerRecord
from formats.skillcorner_bundle import (
    MatchInfo,
    discover_matches,
    is_complete,
    load_meta,
    local_match_date,
    match_info,
    missing_artifacts,
    players_from_meta,
    source_files,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_bundle(root: Path, match_id: str, *, drop: Collection[str] = ()) -> None:
    """Create a synthetic bundle tree with the 5 artifact subdirs for one match.

    `drop` names role keys whose source file should be omitted (to test completeness).
    """
    role_to_relpath = {
        "tracking": ("tracking", ".json"),
        "events": ("dynamic", ".parquet"),
        "freeze_frames": ("freeze", ".parquet"),
        "metadata": ("meta", ".json"),
        "physical": ("physical", ".parquet"),
    }
    for role, (subdir, ext) in role_to_relpath.items():
        d = root / subdir
        d.mkdir(parents=True, exist_ok=True)
        if role not in drop:
            (d / f"{match_id}{ext}").write_text("x", encoding="utf-8")


def _meta_with_players(players: list[dict]) -> dict:
    return {
        "id": 1,
        "date_time": "2023-08-12T19:30:00Z",
        "home_team": {"short_name": "H"},
        "away_team": {"short_name": "A"},
        "players": players,
    }


class TestLocalMatchDate:
    def test_z_suffix_evening_same_local_date(self) -> None:
        # 19:30 UTC -> 21:30 Europe/Madrid (CEST), same calendar day
        assert local_match_date("2023-08-12T19:30:00Z") == "2023-08-12"

    def test_offset_form_supported(self) -> None:
        assert local_match_date("2023-08-12T19:30:00+00:00") == "2023-08-12"

    def test_late_utc_rolls_into_next_madrid_day(self) -> None:
        # 23:30 UTC -> 01:30 CEST next day: proves Madrid conversion is applied
        assert local_match_date("2099-08-15T23:30:00Z") == "2099-08-16"

    def test_naive_timestamp_rejected(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            local_match_date("2099-08-15T18:30:00")


class TestMatchInfo:
    def test_match_info_from_meta_dict(self) -> None:
        meta = {
            "id": 1234567,
            "date_time": "2023-08-12T19:30:00Z",
            "home_team": {"id": 1, "name": "Home Town FC", "short_name": "Home FC"},
            "away_team": {"id": 2, "name": "Away City CF", "short_name": "Away CF"},
        }
        info = match_info(meta)
        assert isinstance(info, MatchInfo)
        assert info.match_id == "1234567"  # coerced to str
        assert info.date == "2023-08-12"
        assert info.home == "Home FC"  # short_name preferred
        assert info.away == "Away CF"

    def test_match_info_falls_back_to_name_when_no_short_name(self) -> None:
        meta = {
            "id": 9,
            "date_time": "2023-08-12T19:30:00Z",
            "home_team": {"id": 1, "name": "Home Town FC"},
            "away_team": {"id": 2, "name": "Away City CF"},
        }
        info = match_info(meta)
        assert info.home == "Home Town FC"
        assert info.away == "Away City CF"

    def test_match_info_missing_fields_raises(self) -> None:
        with pytest.raises(ValueError, match="missing required meta"):
            match_info({"id": 9, "home_team": {"name": "x"}})


class TestDiscovery:
    def test_discovers_match_ids_from_meta_dir(self, tmp_path: Path) -> None:
        _make_bundle(tmp_path, "1000002")
        _make_bundle(tmp_path, "1000001")
        assert discover_matches(tmp_path) == ["1000001", "1000002"]  # sorted

    def test_missing_meta_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="meta"):
            discover_matches(tmp_path)


class TestCompleteness:
    def test_complete_match(self, tmp_path: Path) -> None:
        _make_bundle(tmp_path, "1000001")
        assert missing_artifacts(tmp_path, "1000001") == []
        assert is_complete(tmp_path, "1000001") is True

    def test_incomplete_match_reports_missing_roles(self, tmp_path: Path) -> None:
        _make_bundle(tmp_path, "1000001", drop={"tracking", "physical"})
        assert sorted(missing_artifacts(tmp_path, "1000001")) == ["physical", "tracking"]
        assert is_complete(tmp_path, "1000001") is False

    def test_source_files_maps_every_role(self, tmp_path: Path) -> None:
        _make_bundle(tmp_path, "1000001")
        files = source_files(tmp_path, "1000001")
        assert set(files) == {"tracking", "events", "freeze_frames", "metadata", "physical"}
        assert files["tracking"].name == "1000001.json"
        assert files["events"].name == "1000001.parquet"


class TestPlayersFromMeta:
    def test_maps_fields(self) -> None:
        meta = _meta_with_players(
            [
                {
                    "id": 688,
                    "first_name": "Test",
                    "last_name": "Player",
                    "short_name": "T. Player",
                    "birthday": "1989-08-14",
                    "player_role": {"id": 5, "position_group": "Midfield", "name": "LDM", "acronym": "LDM"},
                }
            ]
        )
        records = players_from_meta(meta)
        assert len(records) == 1
        r = records[0]
        assert r == {
            "id": "688",
            "firstName": "Test",
            "lastName": "Player",
            "nickname": "T. Player",
            "dob": "1989-08-14",
            "position": "LDM",
            "positionGroupType": "Midfield",
        }
        PlayerRecord.model_validate({**r, "visibility": "private", "updated_at": "2026-01-01T00:00:00Z"})

    def test_blank_name_falls_back_to_nickname_only(self) -> None:
        meta = _meta_with_players(
            [{"id": 1, "first_name": "", "last_name": "", "short_name": "Solo", "birthday": "2000-01-01"}]
        )
        r = players_from_meta(meta)[0]
        assert r["firstName"] is None and r["lastName"] is None and r["nickname"] == "Solo"
        PlayerRecord.model_validate({**r, "visibility": "private", "updated_at": "2026-01-01T00:00:00Z"})

    def test_skips_entries_without_id(self) -> None:
        meta = _meta_with_players([{"first_name": "No", "last_name": "Id", "short_name": "NI"}])
        assert players_from_meta(meta) == []

    def test_no_players_key_returns_empty(self) -> None:
        assert players_from_meta({"id": 1}) == []


class TestLoadMetaFixture:
    def test_load_and_parse_synthetic_fixture(self) -> None:
        meta = load_meta(FIXTURES_DIR / "skillcorner_bundle_meta_synthetic.json")
        info = match_info(meta)
        assert info.match_id == "9000001"
        assert info.date == "2099-08-15"
        assert info.home == "Synthetic Home FC"
        assert info.away == "Synthetic Away CF"
        players = players_from_meta(meta)
        assert {p["id"] for p in players} == {"7000001", "7000002"}
