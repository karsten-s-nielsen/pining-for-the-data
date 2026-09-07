"""Tests for scripts/upload_statsbomb_open.py (no real S3, no real network)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from unittest.mock import patch

import pytest


def test_fetch_reads_source_dir_without_network(tmp_path, load_script) -> None:
    mod = load_script("upload_statsbomb_open")
    src = tmp_path / "clone"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "x.json").write_text(json.dumps({"ok": 1}), encoding="utf-8")
    with patch.object(mod.urllib.request, "urlopen") as urlopen:
        got = mod.fetch_json("sub/x.json", cache_dir=tmp_path / "cache", source_dir=src)
    assert got == {"ok": 1}
    urlopen.assert_not_called()


def test_fetch_uses_cache_without_refetch(tmp_path, load_script) -> None:
    mod = load_script("upload_statsbomb_open")
    cache = tmp_path / "cache"
    (cache / "sub").mkdir(parents=True)
    (cache / "sub" / "x.json").write_text(json.dumps({"cached": 1}), encoding="utf-8")
    with patch.object(mod.urllib.request, "urlopen") as urlopen:
        got = mod.fetch_json("sub/x.json", cache_dir=cache, source_dir=None)
    assert got == {"cached": 1}
    urlopen.assert_not_called()


def test_fetch_failure_raises_with_url(tmp_path, load_script) -> None:
    mod = load_script("upload_statsbomb_open")
    with patch.object(mod.urllib.request, "urlopen", side_effect=OSError("boom")):
        with pytest.raises(RuntimeError, match=r"events/1\.json"):
            mod.fetch_json("events/1.json", cache_dir=tmp_path / "cache", source_dir=None)


def test_uploads_private_redistributed_with_index_fields(
    tmp_path, load_script, sbo_match, sbo_events, sbo_frames, sbo_lineups
) -> None:
    mod = load_script("upload_statsbomb_open")
    with (
        patch.object(mod, "upload_game") as game,
        patch.object(mod, "upload_players") as players,
    ):
        mid, n_new, n_skip = mod.upload_open_match(
            sbo_match,
            sbo_events,
            sbo_frames,
            sbo_lineups,
            bucket="test-bucket",
            known_ids=set(),
            dry_run=False,
        )
    assert mid == "8888888"
    assert (n_new, n_skip) == (2, 0)
    kwargs = game.call_args.kwargs
    assert kwargs["provider"] == "statsbomb"
    assert kwargs["visibility"] == "private"
    assert kwargs["provenance"] == "redistributed"
    assert kwargs["game_id"] == "8888888"
    assert kwargs["date"] == "2026-07-15"
    assert kwargs["source_name"] == "StatsBomb"
    assert kwargs["source_licence"].startswith("StatsBomb Public Data User Agreement")
    assert players.call_args.kwargs["visibility"] == "private"


def test_stages_four_role_aligned_artifacts(
    tmp_path, load_script, sbo_match, sbo_events, sbo_frames, sbo_lineups
) -> None:
    mod = load_script("upload_statsbomb_open")
    seen: list[str] = []
    with (
        patch.object(
            mod, "upload_game", side_effect=lambda **k: seen.extend(sorted(p.name for p in k["game_dir"].iterdir()))
        ),
        patch.object(mod, "upload_players"),
    ):
        mod.upload_open_match(
            sbo_match, sbo_events, sbo_frames, sbo_lineups, bucket="b", known_ids=set(), dry_run=False
        )
    assert seen == ["events.json.gz", "freeze_frames.json.gz", "metadata.json", "roster.json"]


def test_staged_gz_artifacts_roundtrip(load_script, sbo_match, sbo_events, sbo_frames, sbo_lineups) -> None:
    mod = load_script("upload_statsbomb_open")
    captured: dict = {}

    def _capture(**k) -> None:
        game_dir = k["game_dir"]
        captured["events"] = json.loads(gzip.decompress((game_dir / "events.json.gz").read_bytes()).decode("utf-8"))
        captured["frames"] = json.loads(
            gzip.decompress((game_dir / "freeze_frames.json.gz").read_bytes()).decode("utf-8")
        )

    with (
        patch.object(mod, "upload_game", side_effect=_capture),
        patch.object(mod, "upload_players"),
    ):
        mod.upload_open_match(
            sbo_match, sbo_events, sbo_frames, sbo_lineups, bucket="b", known_ids=set(), dry_run=False
        )
    assert captured["events"] == sbo_events
    assert captured["frames"] == sbo_frames


def test_orphan_frame_blocks_upload(load_script, sbo_match, sbo_events, sbo_lineups) -> None:
    mod = load_script("upload_statsbomb_open")
    bad_frames = [{"event_uuid": "orphan", "freeze_frame": [{"teammate": True}]}]
    with (
        patch.object(mod, "upload_game") as game,
        patch.object(mod, "upload_players") as players,
    ):
        with pytest.raises(ValueError, match="unknown event id"):
            mod.upload_open_match(
                sbo_match, sbo_events, bad_frames, sbo_lineups, bucket="b", known_ids=set(), dry_run=False
            )
    game.assert_not_called()
    players.assert_not_called()


def test_skips_known_player_uploads_only_new(load_script, sbo_match, sbo_events, sbo_frames, sbo_lineups) -> None:
    mod = load_script("upload_statsbomb_open")
    known = {"70001"}  # pretend 70001 already came from the commercial load
    # upload_players writes into a context-managed temp dir that is cleaned up before
    # upload_open_match returns, so read input_file DURING the mocked call, not after.
    uploaded: dict = {}

    def _snapshot(**kwargs) -> None:
        uploaded.update(json.loads(kwargs["input_file"].read_text(encoding="utf-8")))

    with (
        patch.object(mod, "upload_game"),
        patch.object(mod, "upload_players", side_effect=_snapshot) as players,
    ):
        _mid, n_new, n_skip = mod.upload_open_match(
            sbo_match,
            sbo_events,
            sbo_frames,
            sbo_lineups,
            bucket="b",
            known_ids=known,
            dry_run=False,
        )
    assert (n_new, n_skip) == (1, 1)
    players.assert_called_once()
    # only the new id (70002) reaches upload_players
    assert [p["id"] for p in uploaded["players"]] == ["70002"]
    assert known == {"70001", "70002"}  # known_ids grew (intra-run dedup)


def test_intra_run_dedup_second_match_skips_shared_id(
    load_script, sbo_match, sbo_events, sbo_frames, sbo_lineups
) -> None:
    mod = load_script("upload_statsbomb_open")
    known: set[str] = set()
    with patch.object(mod, "upload_game"), patch.object(mod, "upload_players"):
        mod.upload_open_match(
            sbo_match, sbo_events, sbo_frames, sbo_lineups, bucket="b", known_ids=known, dry_run=False
        )
        second = dict(sbo_match)
        second["match_id"] = 8888890
        _mid, n_new, n_skip = mod.upload_open_match(
            second,
            sbo_events,
            sbo_frames,
            sbo_lineups,
            bucket="b",
            known_ids=known,
            dry_run=False,
        )
    assert (n_new, n_skip) == (0, 2)  # both players already added by the first match


def test_dry_run_makes_no_upload_calls(load_script, sbo_match, sbo_events, sbo_frames, sbo_lineups) -> None:
    mod = load_script("upload_statsbomb_open")
    with (
        patch.object(mod, "upload_game") as game,
        patch.object(mod, "upload_players") as players,
    ):
        mod.upload_open_match(sbo_match, sbo_events, sbo_frames, sbo_lineups, bucket="b", known_ids=set(), dry_run=True)
    game.assert_not_called()
    players.assert_not_called()


def test_run_skips_defective_match_and_continues(
    monkeypatch, load_script, sbo_match, sbo_events, sbo_frames, sbo_lineups
) -> None:
    mod = load_script("upload_statsbomb_open")
    good = dict(sbo_match)
    good["match_id"] = 8888888
    bad = dict(sbo_match)
    bad["match_id"] = 8888889
    season = [good, bad]  # both competition 555555, both match_status_360 "available"

    def fake_fetch(rel, cache_dir, source_dir):
        if rel.startswith("matches/"):
            return season
        if rel == "three-sixty/8888889.json":
            raise json.JSONDecodeError("Expecting ',' delimiter", "doc", 100)
        if rel.startswith("events/"):
            return sbo_events
        if rel.startswith("three-sixty/"):
            return sbo_frames
        if rel.startswith("lineups/"):
            return sbo_lineups
        raise AssertionError(f"unexpected fetch: {rel}")

    monkeypatch.setattr(mod, "fetch_json", fake_fetch)
    with (
        patch.object(mod, "load_known_player_ids", return_value=set()),
        patch.object(mod, "upload_game") as game,
        patch.object(mod, "upload_players"),
    ):
        defective = mod.run([(555555, 777)], bucket="b", cache_dir=Path("x"), source_dir=None, dry_run=False)

    assert game.call_count == 1  # only the good match was uploaded; the defective one was skipped
    assert [mid for mid, _ in defective] == ["8888889"]
    assert "JSONDecodeError" in defective[0][1]


def test_run_propagates_upload_errors_not_swallowed_as_defect(
    monkeypatch, load_script, sbo_match, sbo_events, sbo_frames, sbo_lineups
) -> None:
    # an upload-phase failure must PROPAGATE, never be mis-reported as a "defective" match
    mod = load_script("upload_statsbomb_open")
    season = [sbo_match]  # one good match (competition 555555)

    def fake_fetch(rel, cache_dir, source_dir):
        if rel.startswith("matches/"):
            return season
        if rel.startswith("events/"):
            return sbo_events
        if rel.startswith("three-sixty/"):
            return sbo_frames
        if rel.startswith("lineups/"):
            return sbo_lineups
        raise AssertionError(f"unexpected fetch: {rel}")

    monkeypatch.setattr(mod, "fetch_json", fake_fetch)
    with (
        patch.object(mod, "load_known_player_ids", return_value=set()),
        patch.object(mod, "upload_game", side_effect=RuntimeError("s3 boom")),
        patch.object(mod, "upload_players"),
    ):
        with pytest.raises(RuntimeError, match="s3 boom"):
            mod.run([(555555, 777)], bucket="b", cache_dir=Path("x"), source_dir=None, dry_run=False)
