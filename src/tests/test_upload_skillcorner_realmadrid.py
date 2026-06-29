"""Tests for scripts/upload_skillcorner_realmadrid.py (no real S3, no real network)."""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Lambda shared dir parity with other upload tests (harmless if unused here).
LAMBDA_SRC = Path(__file__).parent.parent.parent / "terraform" / "modules" / "functions" / "src"
sys.path.insert(0, str(LAMBDA_SRC))


def _valid_meta(match_id: str) -> str:
    return json.dumps(
        {
            "id": int(match_id),
            "date_time": "2023-08-12T19:30:00Z",
            "home_team": {"short_name": "Home FC"},
            "away_team": {"short_name": "Away CF"},
            "players": [{"id": int(match_id), "short_name": "Player"}],
        }
    )


def _make_bundle(root: Path, match_id: str) -> None:
    specs = {
        "tracking": ("tracking", ".json", '{"frames": []}'),
        "events": ("dynamic", ".parquet", "EVENTS-PARQUET-BYTES"),
        "freeze_frames": ("freeze", ".parquet", "FREEZE-PARQUET-BYTES"),
        "metadata": ("meta", ".json", _valid_meta(match_id)),
        "physical": ("physical", ".parquet", "PHYSICAL-PARQUET-BYTES"),
    }
    for _role, (subdir, ext, body) in specs.items():
        d = root / subdir
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{match_id}{ext}").write_text(body, encoding="utf-8")


class TestStageMatch:
    def test_stages_five_artifacts_with_role_names_and_gzips_tracking(self, tmp_path: Path, load_script) -> None:
        mod = load_script("upload_skillcorner_realmadrid")
        root = tmp_path / "src"
        _make_bundle(root, "1000001")
        staging = tmp_path / "stage"
        staging.mkdir()

        mod.stage_match(root, "1000001", staging)

        names = sorted(p.name for p in staging.iterdir())
        assert names == [
            "events.parquet",
            "freeze_frames.parquet",
            "metadata.json",
            "physical.parquet",
            "tracking.json.gz",
        ]
        # velocities is NOT staged
        assert not (staging / "velocities.parquet").exists()
        # tracking is real gzip and round-trips to the original bytes
        with gzip.open(staging / "tracking.json.gz", "rt", encoding="utf-8") as f:
            assert f.read() == '{"frames": []}'
        # parquet artifacts copied verbatim
        assert (staging / "events.parquet").read_text(encoding="utf-8") == "EVENTS-PARQUET-BYTES"


class TestDerivePlayers:
    def test_dedup_across_matches_and_skip_public_ids(self, load_script) -> None:
        mod = load_script("upload_skillcorner_realmadrid")
        meta_a = {
            "players": [
                {"id": 1, "short_name": "One", "first_name": "P", "last_name": "One"},
                {"id": 2, "short_name": "Two", "first_name": "P", "last_name": "Two"},
            ]
        }
        meta_b = {
            "players": [
                {"id": 2, "short_name": "Two", "first_name": "P", "last_name": "Two"},  # dup across matches
                {"id": 3, "short_name": "Three", "first_name": "P", "last_name": "Three"},
            ]
        }
        kept, skipped = mod.derive_players([meta_a, meta_b], skip_ids={"3"})

        assert [r["id"] for r in kept] == ["1", "2"]  # deduped, sorted, id 3 skipped
        assert skipped == ["3"]

    def test_no_skip_ids_keeps_all(self, load_script) -> None:
        mod = load_script("upload_skillcorner_realmadrid")
        meta = {"players": [{"id": 5, "short_name": "Five"}]}
        kept, skipped = mod.derive_players([meta], skip_ids=set())
        assert [r["id"] for r in kept] == ["5"]
        assert skipped == []


class TestPublicPlayerIds:
    def test_reads_public_index_ids(self, load_script) -> None:
        mod = load_script("upload_skillcorner_realmadrid")
        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        body = {"players": [{"id": "1"}, {"id": "2"}]}
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(body).encode())}

        ids = mod.public_player_ids(s3, "bucket")
        assert ids == {"1", "2"}
        s3.get_object.assert_called_once_with(Bucket="bucket", Key="skillcorner/players.json")

    def test_missing_public_index_returns_empty(self, load_script) -> None:
        mod = load_script("upload_skillcorner_realmadrid")
        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        s3.get_object.side_effect = s3.exceptions.NoSuchKey()
        assert mod.public_player_ids(s3, "bucket") == set()


class TestUploadAll:
    def test_orchestrates_uploads_with_expected_kwargs(self, tmp_path: Path, load_script, monkeypatch) -> None:
        mod = load_script("upload_skillcorner_realmadrid")

        # One complete match + one incomplete (missing physical) that must be skipped.
        root = tmp_path
        _make_bundle(root, "1000001")
        _make_bundle(root, "1000002")
        (root / "physical" / "1000002.parquet").unlink()  # make 1000002 incomplete
        # Give 1000001 a real parseable meta with one player.
        (root / "meta" / "1000001.json").write_text(
            json.dumps(
                {
                    "id": 1000001,
                    "date_time": "2023-08-12T19:30:00Z",
                    "home_team": {"short_name": "Home FC"},
                    "away_team": {"short_name": "Away CF"},
                    "players": [
                        {
                            "id": 4242,
                            "short_name": "Star",
                            "first_name": "A",
                            "last_name": "B",
                            "player_role": {"name": "CF", "position_group": "Forward"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        # Stub S3 (only used for public_player_ids) + capture the two upload calls.
        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        s3.get_object.side_effect = s3.exceptions.NoSuchKey()  # no existing public players
        monkeypatch.setattr(mod.boto3, "client", lambda _name: s3)

        game_calls: list[dict] = []
        players_calls: list[dict] = []
        monkeypatch.setattr(mod, "upload_game", lambda **kw: game_calls.append(kw) or ["tracking"])
        monkeypatch.setattr(mod, "upload_players", lambda **kw: players_calls.append(kw) or 1)

        uploaded, n_players, n_skipped = mod.upload_all(root, "test-bucket")

        # Only the complete match uploaded.
        assert uploaded == 1
        assert len(game_calls) == 1
        g = game_calls[0]
        assert g["provider"] == "skillcorner"
        assert g["game_id"] == "1000001"
        assert g["visibility"] == "private"
        assert g["provenance"] == "original"
        assert g["date"] == "2023-08-12"
        assert g["home"] == "Home FC" and g["away"] == "Away CF"
        assert g["source_name"] == "SkillCorner"
        assert g["source_licence"] == "Restricted; redistribution not permitted"

        # Players derived from the complete match and uploaded private from a JSON file.
        assert n_players == 1 and n_skipped == 0
        assert len(players_calls) == 1
        p = players_calls[0]
        assert p["provider"] == "skillcorner" and p["visibility"] == "private"
        assert Path(p["input_file"]).suffix == ".json"

    def test_limit_caps_matches(self, tmp_path: Path, load_script, monkeypatch) -> None:
        mod = load_script("upload_skillcorner_realmadrid")
        for mid in ("1000001", "1000002", "1000003"):
            _make_bundle(tmp_path, mid)

        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        s3.get_object.side_effect = s3.exceptions.NoSuchKey()
        monkeypatch.setattr(mod.boto3, "client", lambda _name: s3)
        monkeypatch.setattr(mod, "upload_game", lambda **kw: ["tracking"])
        monkeypatch.setattr(mod, "upload_players", lambda **kw: 0)

        uploaded, _n_players, _n_skipped = mod.upload_all(tmp_path, "test-bucket", limit=2)
        assert uploaded == 2
