"""Tests for the canonical-load verification's pure check (no network)."""

from __future__ import annotations

import pytest


@pytest.fixture
def verify(load_script):
    return load_script("verify_skillcorner_canonical_load")


def _canonical_entry():
    return {
        "id": "m1",
        "visibility": "private",
        "format_version": 2,
        "artifacts": {
            "tracking": "tracking.parquet",
            "events": "events.parquet",
            "physical": "physical.parquet",
            "metadata": "metadata.json",
        },
    }


def _status_present_except_freeze(role):
    return 404 if role == "freeze_frames" else 302


def test_canonical_match_has_no_problems(verify):
    assert verify.check_match_canonical(_canonical_entry(), _status_present_except_freeze) == []


def test_legacy_match_is_flagged(verify):
    legacy = {
        "id": "m2",
        "visibility": "private",
        "artifacts": {"tracking": "tracking.json.gz", "freeze_frames": "freeze_frames.parquet"},
    }
    problems = verify.check_match_canonical(legacy, lambda role: 302)
    joined = " ".join(problems)
    assert "format_version != 2" in joined
    assert "not .parquet" in joined
    assert "freeze_frames still listed" in joined


def test_freeze_that_does_not_404_is_flagged(verify):
    problems = verify.check_match_canonical(_canonical_entry(), lambda role: 302)  # freeze also 302
    assert any("freeze_frames does not 404" in p for p in problems)
