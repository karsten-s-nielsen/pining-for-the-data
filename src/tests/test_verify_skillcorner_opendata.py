"""Tests for the public opendata load-verification's pure checks (no network)."""

from __future__ import annotations

import pytest


@pytest.fixture
def verify(load_script):
    return load_script("verify_skillcorner_opendata_load")


def _public_entry(mid: str = "3001", **overrides) -> dict:
    entry = {
        "id": mid,
        "visibility": "public",
        "provenance": "redistributed",
        "artifacts": {
            f"{mid}_match": f"{mid}_match.json",
            f"{mid}_tracking_extrapolated": f"{mid}_tracking_extrapolated.jsonl",
            f"{mid}_dynamic_events": f"{mid}_dynamic_events.csv",
            f"{mid}_phases_of_play": f"{mid}_phases_of_play.csv",
        },
    }
    entry.update(overrides)
    return entry


class TestOpendataArtifactKeys:
    def test_derives_four_id_prefixed_keys(self, verify) -> None:
        assert verify.opendata_artifact_keys("3001") == {
            "3001_match",
            "3001_tracking_extrapolated",
            "3001_dynamic_events",
            "3001_phases_of_play",
        }


class TestVerifyListing:
    def test_ok_when_count_and_ids_match(self, verify) -> None:
        matches = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        assert verify.verify_listing(matches, 3, ("a", "b")) == []

    def test_flags_wrong_count(self, verify) -> None:
        problems = verify.verify_listing([{"id": "a"}], 3, ())
        assert any("expected 3, got 1" in p for p in problems)

    def test_flags_missing_new_id(self, verify) -> None:
        problems = verify.verify_listing([{"id": "a"}], 1, ("zzz",))
        assert any("zzz missing" in p for p in problems)


class TestCheckPublicMatch:
    def test_clean_entry_has_no_problems(self, verify) -> None:
        assert verify.check_public_match(_public_entry(), lambda key: 302) == []

    def test_flags_wrong_visibility_provenance_and_format_version(self, verify) -> None:
        entry = _public_entry(visibility="private", provenance="original", format_version=2)
        joined = " ".join(verify.check_public_match(entry, lambda key: 302))
        assert "visibility != public" in joined
        assert "provenance != redistributed" in joined
        assert "format_version should be absent" in joined

    def test_flags_missing_artifact_key(self, verify) -> None:
        entry = _public_entry()
        del entry["artifacts"]["3001_phases_of_play"]
        assert any("!= expected" in p for p in verify.check_public_match(entry, lambda key: 302))

    def test_flags_artifact_that_does_not_serve(self, verify) -> None:
        problems = verify.check_public_match(_public_entry(), lambda key: 404)
        assert any("returned 404" in p for p in problems)
