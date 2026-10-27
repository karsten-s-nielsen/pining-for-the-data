"""Each of the 8 loaders passes its reader's competition/season values to ``upload_game`` and stages
byte-identical files from the same source on every run (spec §5.8, §6).

All I/O is faked: no S3, no HF, no network. Every source value is invented.
"""

from __future__ import annotations

import bz2
import io
import json
import shutil
import sys
import types
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from formats.competition_season import CompetitionSeason

FIXTURES = Path(__file__).parent / "fixtures"
_KEYS = ("competition_id", "competition_name", "season_id", "season_name")
_SC = {"competition_id": "301", "competition_name": "Synthetic Premier", "season_id": "701", "season_name": "2099/2100"}
_SC_COMPETITION = CompetitionSeason("301", "Synthetic Premier", "701", "2099/2100")


def _fields(kw: dict) -> dict:
    return {k: kw.get(k) for k in _KEYS}


def _sc_meta(match_id: int = 9000002) -> dict:
    meta = json.loads((FIXTURES / "skillcorner_v3_meta_competition_synthetic.json").read_text(encoding="utf-8"))
    meta["id"] = match_id
    return meta


def _frames() -> list[dict]:
    corners = ["x_top_left", "y_top_left", "x_bottom_left", "y_bottom_left"]
    corners += ["x_bottom_right", "y_bottom_right", "x_top_right", "y_top_right"]
    return [
        {
            "frame": 0,
            "timestamp": "00:00:00.00",
            "period": 1,
            "ball_data": {"x": 1.0, "y": 2.0, "z": 0.0, "is_detected": True},
            "possession": {"player_id": 7, "group": "home"},
            "image_corners_projection": dict.fromkeys(corners, 1.0),
            "player_data": [{"x": 3.0, "y": 4.0, "player_id": 101, "is_detected": True}],
        }
    ]


def _parquet(table: dict[str, list[Any]]) -> bytes:
    buf = io.BytesIO()
    pq.write_table(pa.table(table), buf, compression="snappy")
    return buf.getvalue()


@pytest.fixture
def no_network(monkeypatch):
    """Any network access fails the test instead of reaching the network."""

    def _refuse(*_a, **_k):
        raise AssertionError("network access")

    monkeypatch.setattr(urllib.request, "urlopen", _refuse)


# --- Gradient Sports ---------------------------------------------------------------------------


def _gradient_source(root: Path, stem: str = "990001") -> Path:
    for sub in ("Metadata", "Event Data", "Rosters", "Tracking Data"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "gradientsports_metadata_synthetic.json", root / "Metadata" / f"{stem}.json")
    (root / "Event Data" / f"{stem}.json").write_text('[{"event": "synthetic"}]', encoding="utf-8")
    (root / "Rosters" / f"{stem}.json").write_text('[{"player": "synthetic"}]', encoding="utf-8")
    (root / "Tracking Data" / f"{stem}.jsonl.bz2").write_bytes(bz2.compress(b'{"frame": 1}\n'))
    return root


def test_gradient_passes_competition_values(tmp_path, load_script, monkeypatch, upload_recorder):
    mod = load_script("upload_gradient_wc2022")
    fake, calls = upload_recorder
    monkeypatch.setattr(mod, "upload_game", fake)
    mod._upload_one_match(_gradient_source(tmp_path), "990001", "b")

    kw = calls[0][1]
    assert kw["game_id"] == "990001"
    assert (kw["date"], kw["home"], kw["away"]) == ("2099-07-01", "Synthetic Rovers", "Invented Athletic")
    assert kw["competition_id"] == "77"
    assert kw["competition_name"] == "Synthetic Cup"
    assert kw.get("season_id") is None
    assert kw["season_name"] == "2099"


def test_gradient_stages_reproducibly(tmp_path, load_script, monkeypatch, upload_recorder):
    mod = load_script("upload_gradient_wc2022")
    fake, calls = upload_recorder
    monkeypatch.setattr(mod, "upload_game", fake)
    source = _gradient_source(tmp_path)
    mod._upload_one_match(source, "990001", "b")
    mod._upload_one_match(source, "990001", "b")
    assert calls[0][0] == calls[1][0]


def test_gradient_stem_mismatch_fails(tmp_path, load_script, monkeypatch, upload_recorder):
    mod = load_script("upload_gradient_wc2022")
    fake, calls = upload_recorder
    monkeypatch.setattr(mod, "upload_game", fake)
    source = _gradient_source(tmp_path, stem="990002")  # the file says [0].id == "990001"
    with pytest.raises(ValueError, match="990001"):
        mod._upload_one_match(source, "990002", "b")
    assert calls == []


# --- IDSSE -------------------------------------------------------------------------------------


def _run_idsse(mod, monkeypatch, fake) -> None:
    names = [
        "DFL_02_01_matchinformation_DFL-COM-000099_DFL-MAT-SYN001.xml",
        "DFL_03_02_events_raw_DFL-COM-000099_DFL-MAT-SYN001.xml",
        "DFL_04_03_positions_raw_observed_DFL-COM-000099_DFL-MAT-SYN001.xml",
    ]
    listing = [
        {"name": n, "download_url": f"https://invalid.example/{i}", "computed_md5": "x"} for i, n in enumerate(names)
    ]
    xml = (FIXTURES / "idsse_matchinformation_synthetic.xml").read_bytes()

    def fake_download(entry: dict, dest: Path) -> None:
        dest.write_bytes(xml if "matchinformation" in entry["name"] else f"<synthetic {entry['name']}/>".encode())

    monkeypatch.setattr(mod, "fetch_file_listing", lambda *_a, **_k: listing)
    monkeypatch.setattr(mod, "load_manifest", lambda *_a, **_k: {})
    monkeypatch.setattr(mod, "verify_listing", lambda *_a, **_k: None)
    monkeypatch.setattr(mod, "download_verified", fake_download)
    monkeypatch.setattr(mod, "upload_game", fake)
    mod.upload_all(bucket="b")


def test_idsse_passes_competition_values(load_script, monkeypatch, upload_recorder, no_network):
    mod = load_script("upload_idsse_bundesliga")
    fake, calls = upload_recorder
    _run_idsse(mod, monkeypatch, fake)
    assert _fields(calls[0][1]) == {
        "competition_id": "DFL-COM-000099",
        "competition_name": "Synthetic League",
        "season_id": "DFL-SEA-SYN001",
        "season_name": "2099/2100",
    }


def test_idsse_stages_reproducibly(load_script, monkeypatch, upload_recorder, no_network):
    mod = load_script("upload_idsse_bundesliga")
    fake, calls = upload_recorder
    _run_idsse(mod, monkeypatch, fake)
    _run_idsse(mod, monkeypatch, fake)
    assert calls[0][0] == calls[1][0]


# --- SkillCorner raw / parquet / opendata: per-match upload_match ------------------------------


def _raw_sources(match_id: str = "9000002") -> dict[str, bytes]:
    return {
        f"matches/{match_id}.json": json.dumps(_sc_meta(int(match_id))).encode(),
        f"tracking/{match_id}.json": json.dumps(_frames()).encode(),
        f"dynamic_events/{match_id}.json": b"index,x_start\n1,0.5\n2,1.5\n",
    }


def _raw_physical_rows(match_id: str = "9000002") -> list[dict]:
    return [{"match_id": match_id, "player_id": 1, "psv99": 30.0}]


def test_skillcorner_raw_passes_competition_values(load_script, monkeypatch, upload_recorder):
    mod = load_script("upload_skillcorner_raw")
    fake, calls = upload_recorder
    monkeypatch.setattr("mock_api.upload.upload_game", fake)
    src = _raw_sources()
    mod.upload_match(src.__getitem__, "9000002", mod.match_info(_sc_meta()), _raw_physical_rows(), "b")
    assert _fields(calls[0][1]) == _SC
    assert calls[0][1]["format_version"] == 2


def test_skillcorner_raw_stages_reproducibly(load_script, monkeypatch, upload_recorder):
    mod = load_script("upload_skillcorner_raw")
    fake, calls = upload_recorder
    monkeypatch.setattr("mock_api.upload.upload_game", fake)
    src = _raw_sources()
    for _ in range(2):
        mod.upload_match(src.__getitem__, "9000002", mod.match_info(_sc_meta()), _raw_physical_rows(), "b")
    assert calls[0][0] == calls[1][0]


def _parquet_sources(match_id: str = "9000002") -> dict[str, bytes]:
    return {
        f"meta/{match_id}.json": json.dumps(_sc_meta(int(match_id))).encode(),
        f"tracking/{match_id}.json": json.dumps(_frames()).encode(),
        f"dynamic/{match_id}.parquet": _parquet({"index": [1, 2], "x_start": [0.5, 1.5]}),
    }


def _physical_parquet(match_id: str = "9000002") -> bytes:
    return _parquet({"match_id": [match_id], "player_id": [1], "psv99": [30.0]})


def test_skillcorner_parquet_passes_competition_values(load_script, monkeypatch, upload_recorder):
    mod = load_script("upload_skillcorner_parquet")
    fake, calls = upload_recorder
    monkeypatch.setattr("mock_api.upload.upload_game", fake)
    src = _parquet_sources()
    mod.upload_match(src.__getitem__, "9000002", mod.match_info(_sc_meta()), _physical_parquet(), "b")
    assert _fields(calls[0][1]) == _SC
    assert calls[0][1]["format_version"] == 2


def test_skillcorner_parquet_stages_reproducibly(load_script, monkeypatch, upload_recorder):
    mod = load_script("upload_skillcorner_parquet")
    fake, calls = upload_recorder
    monkeypatch.setattr("mock_api.upload.upload_game", fake)
    src = _parquet_sources()
    for _ in range(2):
        mod.upload_match(src.__getitem__, "9000002", mod.match_info(_sc_meta()), _physical_parquet(), "b")
    assert calls[0][0] == calls[1][0]


def _opendata_sources(match_id: str = "9000002") -> dict[str, bytes]:
    base = f"matches/{match_id}/{match_id}"
    return {
        f"{base}_match.json": json.dumps(_sc_meta(int(match_id))).encode(),
        f"{base}_tracking_extrapolated.jsonl": b'{"frame": 0}\n',
        f"{base}_dynamic_events.csv": b"index,x_start\n1,0.5\n",
        f"{base}_phases_of_play.csv": b"index,phase\n1,build_up\n",
    }


def test_skillcorner_opendata_passes_competition_values(load_script, monkeypatch, upload_recorder):
    mod = load_script("upload_skillcorner_opendata")
    fake, calls = upload_recorder
    monkeypatch.setattr("mock_api.upload.upload_game", fake)
    src = _opendata_sources()
    mod.upload_match(src.__getitem__, "9000002", mod.match_info(_sc_meta()), "b")
    assert _fields(calls[0][1]) == _SC
    assert calls[0][1]["visibility"] == "public"


def test_skillcorner_opendata_stages_reproducibly(load_script, monkeypatch, upload_recorder):
    mod = load_script("upload_skillcorner_opendata")
    fake, calls = upload_recorder
    monkeypatch.setattr("mock_api.upload.upload_game", fake)
    src = _opendata_sources()
    for _ in range(2):
        mod.upload_match(src.__getitem__, "9000002", mod.match_info(_sc_meta()), "b")
    assert calls[0][0] == calls[1][0]


# --- SkillCorner Real Madrid (legacy bundle) ---------------------------------------------------


def _rm_bundle(root: Path, match_id: str = "9000002") -> Path:
    bodies = {
        ("tracking", ".json"): json.dumps(_frames()),
        ("dynamic", ".parquet"): "EVENTS-PARQUET-BYTES",
        ("freeze", ".parquet"): "FREEZE-PARQUET-BYTES",
        ("meta", ".json"): json.dumps(_sc_meta(int(match_id))),
        ("physical", ".parquet"): "PHYSICAL-PARQUET-BYTES",
    }
    for (subdir, ext), body in bodies.items():
        (root / subdir).mkdir(parents=True, exist_ok=True)
        (root / subdir / f"{match_id}{ext}").write_text(body, encoding="utf-8")
    return root


def _run_rm(mod, root: Path, monkeypatch, fake) -> None:
    s3 = MagicMock()
    s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
    s3.get_object.side_effect = s3.exceptions.NoSuchKey()
    monkeypatch.setattr(mod.boto3, "client", lambda *_a, **_k: s3)
    monkeypatch.setattr(mod, "upload_game", fake)
    monkeypatch.setattr(mod, "upload_players", lambda **_k: 0)
    mod.upload_all(root, "b")


def test_skillcorner_realmadrid_passes_competition_values(tmp_path, load_script, monkeypatch, upload_recorder):
    mod = load_script("upload_skillcorner_realmadrid")
    fake, calls = upload_recorder
    _run_rm(mod, _rm_bundle(tmp_path), monkeypatch, fake)
    assert _fields(calls[0][1]) == _SC


def test_skillcorner_realmadrid_stages_reproducibly(tmp_path, load_script, monkeypatch, upload_recorder):
    mod = load_script("upload_skillcorner_realmadrid")
    fake, calls = upload_recorder
    root = _rm_bundle(tmp_path)
    _run_rm(mod, root, monkeypatch, fake)
    _run_rm(mod, root, monkeypatch, fake)
    assert calls[0][0] == calls[1][0]


# --- StatsBomb club drop and open data ---------------------------------------------------------


def test_statsbomb_club_passes_competition_values(sb_bundle_dir, load_script, monkeypatch, upload_recorder):
    mod = load_script("upload_statsbomb_club")
    fake, calls = upload_recorder
    monkeypatch.setattr(mod, "upload_game", fake)
    monkeypatch.setattr(mod, "upload_players", lambda **_k: 0)
    mod.upload_bundle(sb_bundle_dir(), "b")
    assert _fields(calls[0][1]) == {
        "competition_id": "33",
        "competition_name": "Vibranium League",
        "season_id": "902",
        "season_name": "2026",
    }


def test_statsbomb_club_stages_reproducibly(sb_bundle_dir, load_script, monkeypatch, upload_recorder):
    mod = load_script("upload_statsbomb_club")
    fake, calls = upload_recorder
    monkeypatch.setattr(mod, "upload_game", fake)
    monkeypatch.setattr(mod, "upload_players", lambda **_k: 0)
    root = sb_bundle_dir()
    mod.upload_bundle(root, "b")
    mod.upload_bundle(root, "b")
    assert calls[0][0] == calls[1][0]


def _run_sb_open(mod, sbo_match, sbo_events, sbo_frames, sbo_lineups) -> None:
    metadata, info, players = mod.validate_open_match(sbo_match, sbo_events, sbo_frames, sbo_lineups)
    mod._stage_and_upload_open_match(
        metadata, info, players, sbo_events, sbo_frames, sbo_lineups, bucket="b", known_ids=set(), dry_run=False
    )


def test_statsbomb_open_passes_competition_values(
    load_script, monkeypatch, upload_recorder, sbo_match, sbo_events, sbo_frames, sbo_lineups
):
    mod = load_script("upload_statsbomb_open")
    fake, calls = upload_recorder
    monkeypatch.setattr(mod, "upload_game", fake)
    monkeypatch.setattr(mod, "upload_players", lambda **_k: 0)
    _run_sb_open(mod, sbo_match, sbo_events, sbo_frames, sbo_lineups)
    assert _fields(calls[0][1]) == {
        "competition_id": "555555",
        "competition_name": "Continental Cup",
        "season_id": "777",
        "season_name": "2026",
    }


def test_statsbomb_open_stages_reproducibly(
    load_script, monkeypatch, upload_recorder, sbo_match, sbo_events, sbo_frames, sbo_lineups
):
    mod = load_script("upload_statsbomb_open")
    fake, calls = upload_recorder
    monkeypatch.setattr(mod, "upload_game", fake)
    monkeypatch.setattr(mod, "upload_players", lambda **_k: 0)
    _run_sb_open(mod, sbo_match, sbo_events, sbo_frames, sbo_lineups)
    _run_sb_open(mod, sbo_match, sbo_events, sbo_frames, sbo_lineups)
    assert calls[0][0] == calls[1][0]


# --- SkillCorner main(): the rewired loop calls upload_match exactly as the inline block did ----


class _Sibling:
    def __init__(self, rfilename: str, size: int) -> None:
        self.rfilename = rfilename
        self.size = size


def _fake_hf(monkeypatch, tmp_path: Path, files: dict[str, bytes]) -> None:
    """Put a fake ``huggingface_hub`` in sys.modules that serves ``files`` (no HF access)."""
    store = tmp_path / "hf"

    def hf_hub_download(repo_id: str, repo_type: str, filename: str) -> str:
        path = store / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(files[filename])
        return str(path)

    class HfApi:
        def dataset_info(self, repo_id: str, files_metadata: bool = False):
            return types.SimpleNamespace(siblings=[_Sibling(name, len(body)) for name, body in files.items()])

    module = types.ModuleType("huggingface_hub")
    module.hf_hub_download = hf_hub_download  # type: ignore[attr-defined]
    module.HfApi = HfApi  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", module)


class _NonClosingBytesIO(io.BytesIO):
    """``main()`` rewraps ``sys.stdout.buffer``; the replaced wrapper's GC must not close the buffer."""

    def close(self) -> None:
        pass


def _patch_common(monkeypatch, load_script, mod) -> list[tuple]:
    """Patch S3, the players path, stdout, and the module's upload_match; return the call log."""
    import boto3

    import mock_api.upload_players

    monkeypatch.setattr(boto3, "client", lambda *_a, **_k: MagicMock())
    monkeypatch.setattr(mock_api.upload_players, "upload_players", lambda **_k: 0)
    rm = load_script("upload_skillcorner_realmadrid")
    monkeypatch.setattr(rm, "public_player_ids", lambda *_a, **_k: set())
    monkeypatch.setattr(rm, "derive_players", lambda *_a, **_k: ([], []))
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(_NonClosingBytesIO(), encoding="utf-8"))
    calls: list[tuple] = []
    monkeypatch.setattr(mod, "upload_match", lambda *args: calls.append(args))
    return calls


def _raw_hf_files() -> dict[str, bytes]:
    files = {"metadata/available_dynamic_event_match_ids.csv": b"match_id\n9000002\n9000003\n"}
    files.update(_raw_sources("9000002"))
    files.update(_raw_sources("9000003"))
    rows = _raw_physical_rows("9000002") + _raw_physical_rows("9000003")
    files["physical/physical.json"] = json.dumps({"results": rows}).encode()
    return files


def _run_main(monkeypatch, mod, argv: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", argv)
    mod.main()


def test_skillcorner_raw_main_calls_upload_match_per_match(tmp_path, load_script, monkeypatch, no_network):
    mod = load_script("upload_skillcorner_raw")
    _fake_hf(monkeypatch, tmp_path, _raw_hf_files())
    calls = _patch_common(monkeypatch, load_script, mod)
    _run_main(monkeypatch, mod, ["upload_skillcorner_raw", "--bucket", "b", "--hf-repo", "synthetic/raw", "--apply"])

    assert [c[1] for c in calls] == ["9000002", "9000003"]
    for _fetch, match_id, info, physical_rows, bucket in calls:
        assert info.match_id == match_id
        assert info.competition == _SC_COMPETITION
        assert physical_rows == _raw_physical_rows(match_id)
        assert bucket == "b"


def test_skillcorner_raw_main_dry_run_calls_no_upload_match(tmp_path, load_script, monkeypatch, no_network):
    mod = load_script("upload_skillcorner_raw")
    _fake_hf(monkeypatch, tmp_path, _raw_hf_files())
    calls = _patch_common(monkeypatch, load_script, mod)
    _run_main(monkeypatch, mod, ["upload_skillcorner_raw", "--bucket", "b", "--hf-repo", "synthetic/raw"])
    assert calls == []


def _parquet_hf_files() -> dict[str, bytes]:
    files = {**_parquet_sources("9000002"), **_parquet_sources("9000003")}
    files["physical/9000002.parquet"] = _physical_parquet("9000002")  # 9000003 ships no physical layer
    return files


def test_skillcorner_parquet_main_calls_upload_match_per_match(tmp_path, load_script, monkeypatch, no_network):
    mod = load_script("upload_skillcorner_parquet")
    _fake_hf(monkeypatch, tmp_path, _parquet_hf_files())
    calls = _patch_common(monkeypatch, load_script, mod)
    _run_main(monkeypatch, mod, ["upload_skillcorner_parquet", "--bucket", "b", "--hf-repo", "synthetic/pq", "--apply"])

    assert [c[1] for c in calls] == ["9000002", "9000003"]
    for _fetch, match_id, info, _physical, bucket in calls:
        assert info.match_id == match_id
        assert info.competition == _SC_COMPETITION
        assert bucket == "b"
    assert calls[0][3] == _physical_parquet("9000002")
    assert calls[1][3] is None


def test_skillcorner_parquet_main_dry_run_calls_no_upload_match(tmp_path, load_script, monkeypatch, no_network):
    mod = load_script("upload_skillcorner_parquet")
    _fake_hf(monkeypatch, tmp_path, _parquet_hf_files())
    calls = _patch_common(monkeypatch, load_script, mod)
    _run_main(monkeypatch, mod, ["upload_skillcorner_parquet", "--bucket", "b", "--hf-repo", "synthetic/pq"])
    assert calls == []


def _patch_opendata(monkeypatch, load_script, mod) -> list[tuple]:
    index = [{"id": 9000004}, {"id": 9000002}, {"id": 9000003}]
    metas = {f"matches/{i}/{i}_match.json": json.dumps(_sc_meta(i)).encode() for i in (9000002, 9000003, 9000004)}

    def http_get(rel: str) -> bytes:
        return json.dumps(index).encode() if rel == "matches.json" else metas[rel]

    def fetch_resolved(*_a, **_k) -> bytes:
        raise AssertionError("fetch_resolved called: upload_match must receive it, not main")

    monkeypatch.setattr(mod, "http_get", http_get)
    monkeypatch.setattr(mod, "live_match_ids", lambda *_a, **_k: {"9000003"})
    monkeypatch.setattr(mod, "private_player_ids", lambda *_a, **_k: set())
    monkeypatch.setattr(mod, "fetch_resolved", fetch_resolved)
    calls = _patch_common(monkeypatch, load_script, mod)
    return calls


def test_opendata_main_calls_upload_match_per_new_match(load_script, monkeypatch, no_network):
    mod = load_script("upload_skillcorner_opendata")
    calls = _patch_opendata(monkeypatch, load_script, mod)
    _run_main(monkeypatch, mod, ["upload_skillcorner_opendata", "--bucket", "b", "--apply"])

    # select_new_matches(discover_match_ids(index), live): numeric order, the live id skipped.
    assert [c[1] for c in calls] == ["9000002", "9000004"]
    for call in calls:
        assert len(call) == 4  # (fetch, match_id, info, bucket): no extra per-match argument
        fetch, match_id, info, bucket = call
        assert fetch is mod.fetch_resolved
        assert info.match_id == match_id
        assert info.competition == _SC_COMPETITION
        assert bucket == "b"


def test_opendata_main_dry_run_calls_no_upload_match(load_script, monkeypatch, no_network):
    mod = load_script("upload_skillcorner_opendata")
    calls = _patch_opendata(monkeypatch, load_script, mod)
    _run_main(monkeypatch, mod, ["upload_skillcorner_opendata", "--bucket", "b"])
    assert calls == []
