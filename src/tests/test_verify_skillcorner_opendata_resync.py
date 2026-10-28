"""Tests for the opendata re-sync verify (ADR 0015). Synthetic ids and bytes only.

scripts/ is not a package; the `load_script` fixture loads the script by path. The pure checks
take injected digest callables, so no network or S3 is touched here.
"""

from __future__ import annotations

import sys

import pytest

_COMMIT = "4340d274572876239c154c90bc507a9b3250a656"  # pragma: allowlist secret (public git SHA)


@pytest.fixture
def verify(load_script):
    return load_script("verify_skillcorner_opendata_resync")


def _artifacts(mid: str) -> dict[str, str]:
    return {
        f"{mid}_match": f"{mid}_match.json",
        f"{mid}_tracking_extrapolated": f"{mid}_tracking_extrapolated.jsonl",
        f"{mid}_dynamic_events": f"{mid}_dynamic_events.csv",
        f"{mid}_phases_of_play": f"{mid}_phases_of_play.csv",
    }


def _digests(mid: str) -> dict[str, dict]:
    return {key: {"sha256": f"{i:064x}", "size": 10 + i} for i, key in enumerate(_artifacts(mid))}


def _entry(mid: str, *, revision: str | None = _COMMIT, meta: dict | None = None) -> dict:
    entry = {
        "id": mid,
        "artifacts": _artifacts(mid),
        "visibility": "public",
        "updated_at": "2099-01-01T00:00:00Z",
        "source": {"name": "Synthetic Open Data"},
        "artifact_meta": meta if meta is not None else _digests(mid),
    }
    if revision is not None:
        entry["source"]["revision"] = revision
    return entry


# ----------------------------------------------------------------- check_revision_stamped


def test_verify_passes_when_revision_stamped(verify):
    matches = [_entry("9100001"), _entry("9100002")]
    assert verify.check_revision_stamped(matches, _COMMIT) == []


def test_verify_fails_when_revision_missing(verify):
    matches = [_entry("9100001", revision=None)]
    problems = verify.check_revision_stamped(matches, _COMMIT)
    assert len(problems) == 1
    assert "9100001" in problems[0]


def test_verify_fails_when_revision_wrong(verify):
    matches = [_entry("9100001", revision="0" * 40)]
    assert len(verify.check_revision_stamped(matches, _COMMIT)) == 1


# ----------------------------------------------------------------- check_artifact_identity


def test_verify_artifact_identity_passes_when_all_match(verify):
    mid = "9100001"
    entry = _entry(mid)
    digs = _digests(mid)
    problems = verify.check_artifact_identity(entry, lambda key: digs[key], lambda key: digs[key])
    assert problems == []


def test_verify_fails_when_stored_sha_differs_from_pin(verify):
    mid = "9100001"
    entry = _entry(mid)
    digs = _digests(mid)
    bad = dict(digs)
    bad_key = f"{mid}_dynamic_events"
    bad = {**digs, bad_key: {"sha256": "f" * 64, "size": digs[bad_key]["size"]}}
    problems = verify.check_artifact_identity(entry, lambda key: bad[key], lambda key: digs[key])
    assert any(bad_key in p for p in problems)


def test_verify_fails_when_stored_bytes_differ_from_artifact_meta(verify):
    mid = "9100001"
    # artifact_meta claims a different sha than both stored and pin agree on
    meta = _digests(mid)
    stored_pin = _digests(mid)
    bad_key = f"{mid}_phases_of_play"
    meta[bad_key] = {"sha256": "a" * 64, "size": meta[bad_key]["size"]}
    entry = _entry(mid, meta=meta)
    problems = verify.check_artifact_identity(entry, lambda key: stored_pin[key], lambda key: stored_pin[key])
    assert any(bad_key in p for p in problems)


def test_verify_fails_on_size_mismatch(verify):
    mid = "9100001"
    entry = _entry(mid)
    digs = _digests(mid)
    bad_key = f"{mid}_match"
    stored = {**digs, bad_key: {"sha256": digs[bad_key]["sha256"], "size": 99999}}
    problems = verify.check_artifact_identity(entry, lambda key: stored[key], lambda key: digs[key])
    assert any(bad_key in p for p in problems)


def test_verify_tracking_hashed_from_stored_bytes(verify):
    mid = "9100001"
    entry = _entry(mid)
    digs = _digests(mid)
    stored_calls: list[str] = []

    def stored_digest(key: str) -> dict:
        stored_calls.append(key)
        return digs[key]

    verify.check_artifact_identity(entry, stored_digest, lambda key: digs[key])
    assert f"{mid}_tracking_extrapolated" in stored_calls  # tracking is hashed from the stored object


def test_verify_fails_when_stored_tracking_differs_from_pin(verify):
    # The stored tracking bytes hash to something other than the pinned pointer oid (§6.6 item 4):
    # the mismatch must be reported from the bytes, not glossed over because tracking is LFS.
    mid = "9100001"
    entry = _entry(mid)
    digs = _digests(mid)
    track = f"{mid}_tracking_extrapolated"
    stored = {**digs, track: {"sha256": "d" * 64, "size": digs[track]["size"]}}
    problems = verify.check_artifact_identity(entry, lambda key: stored[key], lambda key: digs[key])
    assert any(track in p for p in problems)


def test_verify_fails_when_stored_matches_meta_but_not_pin(verify):
    # artifact_meta AGREES with the stored bytes, but neither matches the pinned upstream file.
    # Only the stored-vs-pin conjunct catches this — the exact stale-content drift this cycle guards.
    mid = "9100001"
    stored = _digests(mid)
    bad_key = f"{mid}_dynamic_events"
    stored[bad_key] = {"sha256": "e" * 64, "size": 55}  # stored == artifact_meta, both drifted off the pin
    entry = _entry(mid, meta=stored)
    pin = _digests(mid)  # the pin keeps the original (different) digest
    problems = verify.check_artifact_identity(entry, lambda key: stored[key], lambda key: pin[key])
    assert any(bad_key in p for p in problems)


# ----------------------------------------------------------------- check_listing


def test_verify_listing_passes_when_all_expected_present(verify):
    ids = ("9100001", "9100002", "9100003")
    matches = [{"id": i, "visibility": "public"} for i in ids]
    assert verify.check_listing(matches, ids) == []


def test_verify_listing_fails_when_an_expected_id_is_absent(verify):
    ids = ("9100001", "9100002", "9100003")
    matches = [{"id": "9100001", "visibility": "public"}, {"id": "9100002", "visibility": "public"}]
    problems = verify.check_listing(matches, ids)
    assert any("9100003" in p for p in problems)


def test_verify_listing_fails_on_wrong_count(verify):
    ids = ("9100001", "9100002")
    matches = [{"id": i, "visibility": "public"} for i in (*ids, "9100099")]
    assert verify.check_listing(matches, ids) != []


def test_verify_main_flags_a_dropped_out_entry(verify, monkeypatch):
    # Driving main proves check_listing is wired in: a listing missing an expected id must exit non-zero.
    ids = ("9100001", "9100002")
    monkeypatch.setattr(verify, "EXPECTED_PUBLIC_IDS", ids)
    present = _entry("9100001")  # valid: stamped + artifact_meta equals the stored/pin digests below
    digs = _digests("9100001")
    monkeypatch.setattr(verify, "get_json", lambda *a, **k: {"matches": [present]})
    monkeypatch.setattr(verify, "stored_digest_for", lambda *a, **k: lambda _mid, key: digs[key])
    monkeypatch.setattr(verify, "pin_digest_for", lambda _mid: lambda key: digs[key])
    monkeypatch.setattr(sys, "argv", ["verify_resync", "--api", "http://x", "--public-token", "t"])
    assert verify.main() == 1  # 9100002 dropped out of the listing -> check_listing fails
