"""Tests for the additive relaxed-completeness check used by the PL 24/25 parquet family."""

from __future__ import annotations

from pathlib import Path

from formats.skillcorner_bundle import REQUIRED_ROLES, missing_required


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
