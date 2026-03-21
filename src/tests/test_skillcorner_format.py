from pathlib import Path

from formats.skillcorner import (
    extract_player_jerseys,
    read_match_json,
    read_tracking_jsonl,
    validate_game,
    write_match_json,
    write_tracking_jsonl,
)

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
