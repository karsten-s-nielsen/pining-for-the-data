"""Tests for the additive relaxed-completeness check used by the PL 24/25 parquet family."""

from __future__ import annotations

from pathlib import Path

from formats.skillcorner_bundle import (
    REQUIRED_ROLES,
    missing_required,
    parquet_role_files,
    partition_ingestible,
)


def _make_pl_match(tmp_path: Path, mid: str) -> Path:
    # PL 24/25 layout: meta/tracking/dynamic present; NO freeze/, NO physical/.
    for sub, ext in [("meta", ".json"), ("tracking", ".json"), ("dynamic", ".parquet")]:
        d = tmp_path / sub
        d.mkdir(exist_ok=True)
        (d / f"{mid}{ext}").write_text("{}", encoding="utf-8")
    return tmp_path


def test_pl_match_complete_without_freeze_or_physical(tmp_path):
    root = _make_pl_match(tmp_path, "500")
    assert missing_required(root, "500") == []  # complete on required roles only


def test_missing_required_reports_absent_required_role(tmp_path):
    root = _make_pl_match(tmp_path, "500")
    (root / "tracking" / "500.json").unlink()
    assert missing_required(root, "500") == ["tracking"]


def test_required_roles_exclude_freeze_and_physical():
    assert REQUIRED_ROLES == {"metadata", "tracking", "events"}


def test_parquet_role_files_maps_layout():
    m = parquet_role_files("500")
    assert m["metadata"] == "meta/500.json"
    assert m["tracking"] == "tracking/500.json"
    assert m["events"] == "dynamic/500.parquet"  # already Parquet (conformed downstream)
    assert m["physical"] == "physical/500.parquet"  # optional
    assert "freeze" not in m and "freeze_frames" not in m  # dropped (ADR 0011)


def _role_size(sizes: dict[tuple[str, str], int]):
    """role_size callable backed by a {(match_id, role): byte_size} dict; absent -> None."""
    return lambda mid, role: sizes.get((mid, role))


def test_partition_keeps_complete_non_empty_matches():
    ids = ["a", "b"]
    sizes = {(i, r): 10 for i in ids for r in ("metadata", "tracking", "events")}
    good, skipped = partition_ingestible(ids, _role_size(sizes))
    assert good == ["a", "b"]
    assert skipped == {}


def test_partition_skips_zero_byte_tracking():
    sizes = {("z", "metadata"): 5, ("z", "tracking"): 0, ("z", "events"): 5}
    good, skipped = partition_ingestible(["z"], _role_size(sizes))
    assert good == []
    assert "empty" in skipped["z"]


def test_partition_skips_missing_events():
    sizes = {("e", "metadata"): 5, ("e", "tracking"): 100}  # no events entry -> None
    good, skipped = partition_ingestible(["e"], _role_size(sizes))
    assert good == []
    assert "events" in skipped["e"]


def test_partition_skips_missing_tracking():
    sizes = {("t", "metadata"): 5, ("t", "events"): 5}  # no tracking entry -> None
    good, skipped = partition_ingestible(["t"], _role_size(sizes))
    assert good == []
    assert "tracking" in skipped["t"]


def test_partition_ignores_optional_physical_and_freeze():
    # physical/freeze absent must NOT cause a skip — only metadata/tracking/events are required
    sizes = {("p", "metadata"): 5, ("p", "tracking"): 100, ("p", "events"): 5}
    good, skipped = partition_ingestible(["p"], _role_size(sizes))
    assert good == ["p"]
    assert skipped == {}
