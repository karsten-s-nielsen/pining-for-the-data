"""Tests for scripts/upload_statsbomb_club.py (no real S3, no real network)."""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from formats import statsbomb as sb

_PATH_PARAM_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


@pytest.fixture
def staged(tmp_path, sb_bundle_dir, load_script):
    """Stage the canonical synthetic bundle; return (module, staging_dir)."""
    mod = load_script("upload_statsbomb_club")
    bundle = sb.read_bundle(sb_bundle_dir())
    staging = tmp_path / "stage"
    staging.mkdir()
    home_id, away_id = sb.resolve_team_ids(bundle.events, bundle.match)
    metadata = sb.build_metadata(
        bundle.match,
        bundle.competition,
        home_id,
        away_id,
        sb.team_gender(bundle.lineups, home_id),
        sb.team_gender(bundle.lineups, away_id),
    )
    mod.stage_bundle(bundle, staging, metadata)
    return mod, staging


class TestStageBundle:
    def test_stages_four_role_aligned_artifacts(self, staged) -> None:
        _mod, staging = staged
        assert sorted(p.name for p in staging.iterdir()) == [
            "events.json.gz",
            "freeze_frames.json.gz",
            "metadata.json",
            "roster.json",
        ]

    def test_artifact_keys_derived_from_stems_match_the_vocabulary(self, staged) -> None:
        _mod, staging = staged
        # upload_game derives the artifact name as name.split(".", 1)[0].
        keys = sorted(p.name.split(".", 1)[0] for p in staging.iterdir())
        assert keys == ["events", "freeze_frames", "metadata", "roster"]

    def test_artifact_keys_satisfy_the_path_param_regex(self, staged) -> None:
        # MatchEntry._validate_artifact_keys enforces this (models.py:68-78); a key
        # that fails it would be accepted at staging and rejected at upload.
        _mod, staging = staged
        for path in staging.iterdir():
            key = path.name.split(".", 1)[0]
            assert _PATH_PARAM_RE.match(key) and len(key) <= 128

    def test_large_bodies_are_gzipped_and_round_trip(self, staged, sb_events) -> None:
        _mod, staging = staged
        with gzip.open(staging / "events.json.gz", "rt", encoding="utf-8") as f:
            assert json.load(f) == sb_events

    def test_roster_is_staged_verbatim(self, staged) -> None:
        # The proprietary rating block survives in the roster artifact.
        _mod, staging = staged
        roster = json.loads((staging / "roster.json").read_text(encoding="utf-8"))
        assert roster[0]["lineup"][0]["skills"]["HOPS"]["rating"] == 0.5137

    def test_metadata_artifact_is_an_object_in_feed_shape(self, staged) -> None:
        _mod, staging = staged
        md = json.loads((staging / "metadata.json").read_text(encoding="utf-8"))
        assert isinstance(md, dict)
        assert md["competition"]["competition_id"] == 33
        assert md["home_team"]["home_team_id"] == 90001
        assert md["stadium"]["id"] is None

    def test_gender_is_resolved_for_both_sides_end_to_end(self, staged) -> None:
        # The whole-delivery check. With a lineups fixture covering only one team,
        # home_team_gender is null here and nothing else in the suite notices.
        _mod, staging = staged
        md = json.loads((staging / "metadata.json").read_text(encoding="utf-8"))
        assert md["home_team"]["home_team_gender"] == "female"
        assert md["away_team"]["away_team_gender"] == "female"


class TestPreflight:
    def test_orphan_frame_blocks_upload(self, sb_bundle_dir, load_script) -> None:
        mod = load_script("upload_statsbomb_club")
        root = sb_bundle_dir(overrides={"frames.json": [{"event_uuid": "orphan"}]})
        with (
            patch.object(mod, "upload_game") as game,
            patch.object(mod, "upload_players") as players,
        ):
            with pytest.raises(ValueError, match="unknown event id"):
                mod.upload_bundle(root, "test-bucket")
        game.assert_not_called()
        players.assert_not_called()

    def test_invalid_player_blocks_upload_before_any_index_write(self, sb_bundle_dir, sb_lineups, load_script) -> None:
        # A player with no usable name fails PlayerRecord. It must stop the run
        # BEFORE upload_game writes artifacts and indexes.
        mod = load_script("upload_statsbomb_club")
        sb_lineups[0]["lineup"].append({"player_id": 7, "player_name": "", "player_nickname": ""})
        root = sb_bundle_dir(overrides={"lineups.json": sb_lineups})
        with (
            patch.object(mod, "upload_game") as game,
            patch.object(mod, "upload_players") as players,
        ):
            with pytest.raises(ValidationError, match="nickname"):
                mod.upload_bundle(root, "test-bucket")
        game.assert_not_called()
        players.assert_not_called()

    def test_empty_lineups_blocks_upload(self, sb_bundle_dir, load_script) -> None:
        # Regression: `[]` used to sail through — _require_array accepted it,
        # assert_delivery_coherent never saw it, an empty roster was staged, the
        # match was indexed, and `if players:` skipped the catalogue upload.
        mod = load_script("upload_statsbomb_club")
        root = sb_bundle_dir(overrides={"lineups.json": []})
        with (
            patch.object(mod, "upload_game") as game,
            patch.object(mod, "upload_players") as players,
        ):
            with pytest.raises(ValueError, match="zero lineup entries"):
                mod.upload_bundle(root, "test-bucket")
        game.assert_not_called()
        players.assert_not_called()

    def test_lineups_for_the_wrong_teams_block_upload(self, sb_bundle_dir, sb_lineups, load_script) -> None:
        mod = load_script("upload_statsbomb_club")
        sb_lineups[1]["team_id"] = 90002
        root = sb_bundle_dir(overrides={"lineups.json": sb_lineups})
        with (
            patch.object(mod, "upload_game") as game,
            patch.object(mod, "upload_players") as players,
        ):
            with pytest.raises(ValueError, match="contradicts itself"):
                mod.upload_bundle(root, "test-bucket")
        game.assert_not_called()
        players.assert_not_called()

    def test_no_force_flag_exists(self, load_script) -> None:
        mod = load_script("upload_statsbomb_club")
        assert "--force" not in Path(mod.__file__).read_text(encoding="utf-8")


class TestUploadBundle:
    def test_uploads_private_original_with_index_fields(self, sb_bundle_dir, load_script) -> None:
        mod = load_script("upload_statsbomb_club")
        with (
            patch.object(mod, "upload_game") as game,
            patch.object(mod, "upload_players") as players,
        ):
            match_id, n_players = mod.upload_bundle(sb_bundle_dir(), "test-bucket")

        assert match_id == "9999999"
        assert n_players == 3

        kwargs = game.call_args.kwargs
        assert kwargs["provider"] == "statsbomb"
        assert kwargs["game_id"] == "9999999"
        assert kwargs["visibility"] == "private"
        assert kwargs["provenance"] == "original"
        assert kwargs["date"] == "2026-01-01"
        assert kwargs["home"] == "Alpha City"
        assert kwargs["away"] == "Beta United"
        assert kwargs["source_name"] == "StatsBomb"
        assert kwargs["source_licence"] == "Restricted; redistribution not permitted"

        assert players.call_args.kwargs["visibility"] == "private"
        assert players.call_args.kwargs["provider"] == "statsbomb"

    def test_production_path_stages_the_four_artifacts(self, sb_bundle_dir, load_script) -> None:
        # TestStageBundle composes the resolve -> build -> stage sequence itself, so
        # it validates the TEST's composition. This asserts what upload_bundle
        # actually puts in the tempdir, snapshotted before it is torn down.
        mod = load_script("upload_statsbomb_club")
        seen: list[str] = []

        def _snapshot(**kwargs) -> list[str]:
            seen.extend(sorted(p.name for p in kwargs["game_dir"].iterdir()))
            return seen

        with (
            patch.object(mod, "upload_game", side_effect=_snapshot),
            patch.object(mod, "upload_players"),
        ):
            mod.upload_bundle(sb_bundle_dir(), "test-bucket")

        assert seen == ["events.json.gz", "freeze_frames.json.gz", "metadata.json", "roster.json"]
