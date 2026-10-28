"""Tests for the SkillCorner opendata re-sync (ADR 0015). Synthetic ids and bytes only.

scripts/ is not a package; the `load_script` fixture loads the script by path. The pure core
(plan_match, refresh_entry, the preflights) takes injected values; main is driven with an
in-memory S3 double and a monkeypatched fetch_raw, so no network or real S3 is touched.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys

import pytest
from botocore.exceptions import ClientError

PROVIDER = "skillcorner"
_REV = "b" * 40
_NOW = "2099-01-01T00:00:00Z"
_OLD = "2001-01-01T00:00:00Z"


@pytest.fixture
def resync(load_script):
    return load_script("resync_skillcorner_opendata")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pointer(blob: bytes) -> bytes:
    return f"version https://git-lfs.github.com/spec/v1\noid sha256:{_sha(blob)}\nsize {len(blob)}\n".encode()


_DYN = b"event,x\npass,0.5\n"
_PHASE = b"phase,start\n1,0\n"


# Per-match upstream bytes (invented). tracking is served as an LFS pointer on the raw host.
def _upstream(mid: str, *, dyn: bytes = _DYN, phase: bytes = _PHASE) -> dict[str, bytes]:
    return {
        f"matches/{mid}/{mid}_match.json": b'{"id": %s}' % mid.encode(),
        f"matches/{mid}/{mid}_tracking_extrapolated.jsonl": _pointer(b'{"frame": 0}\n'),
        f"matches/{mid}/{mid}_dynamic_events.csv": dyn,
        f"matches/{mid}/{mid}_phases_of_play.csv": phase,
    }


def _artifacts(mid: str) -> dict[str, str]:
    return {
        f"{mid}_match": f"{mid}_match.json",
        f"{mid}_tracking_extrapolated": f"{mid}_tracking_extrapolated.jsonl",
        f"{mid}_dynamic_events": f"{mid}_dynamic_events.csv",
        f"{mid}_phases_of_play": f"{mid}_phases_of_play.csv",
    }


def _digests_from_upstream(mid: str, upstream: dict[str, bytes]) -> dict[str, dict]:
    """The {artifact_key: {sha256,size}} the re-sync derives when it stages mid from upstream."""
    out: dict[str, dict] = {}
    for key, filename in _artifacts(mid).items():
        raw = upstream[f"matches/{mid}/{filename}"]
        if raw.startswith(b"version https://git-lfs"):
            lines = raw.decode().splitlines()
            oid = next(line_.split("oid sha256:")[1] for line_ in lines if "oid sha256:" in line_)
            size = int(next(line_.split("size ")[1] for line_ in lines if line_.startswith("size ")))
            out[key] = {"sha256": oid, "size": size}
        else:
            out[key] = {"sha256": _sha(raw), "size": len(raw)}
    return out


def _entry(mid: str, upstream: dict[str, bytes], *, with_meta: bool = True, with_source: bool = True) -> dict:
    entry: dict = {
        "id": mid,
        "artifacts": _artifacts(mid),
        "visibility": "public",
        "updated_at": _OLD,
        "provenance": "redistributed",
        "home": "Asgard Athletic",
        "away": "Krypton City",
        "date": "2099-03-01",
    }
    if with_source:
        entry["source"] = {"name": "Synthetic Open Data", "url": "https://example.test", "licence": "MIT"}
    if with_meta:
        entry["artifact_meta"] = _digests_from_upstream(mid, upstream)
    return entry


# --------------------------------------------------------------------------- plan_match (pure)


def test_resync_skips_put_when_sha_matches(resync):
    mid = "9100001"
    up = _upstream(mid)
    plan = resync.plan_match(_entry(mid, up), _digests_from_upstream(mid, up))
    assert plan["stop_reason"] is None
    assert plan["to_put"] == []


def test_resync_puts_when_sha_differs(resync):
    mid = "9100001"
    up = _upstream(mid)
    entry = _entry(mid, up)  # meta matches the ORIGINAL upstream
    drifted = _digests_from_upstream(mid, _upstream(mid, dyn=b"event,x\npass,0.9\n"))  # dynamic_events changed
    plan = resync.plan_match(entry, drifted)
    assert plan["stop_reason"] is None
    assert plan["to_put"] == [f"{mid}_dynamic_events"]


def test_resync_unexpected_match_or_tracking_drift_reports_and_stops(resync):
    mid = "9100001"
    up = _upstream(mid)
    entry = _entry(mid, up)
    staged = _digests_from_upstream(mid, up)
    staged[f"{mid}_match"] = {"sha256": "f" * 64, "size": 123}  # match drifted -> unexpected
    plan = resync.plan_match(entry, staged)
    assert plan["to_put"] == []
    assert "match" in plan["stop_reason"]


def test_resync_missing_artifact_meta_fallback_puts(resync):
    mid = "9100001"
    up = _upstream(mid)
    entry = _entry(mid, up, with_meta=False)
    plan = resync.plan_match(entry, _digests_from_upstream(mid, up))
    assert plan["stop_reason"] is None
    assert sorted(plan["to_put"]) == sorted([f"{mid}_dynamic_events", f"{mid}_phases_of_play"])


def test_resync_entry_without_source_reports_and_stops(resync):
    mid = "9100001"
    up = _upstream(mid)
    plan = resync.plan_match(_entry(mid, up, with_source=False), _digests_from_upstream(mid, up))
    assert plan["to_put"] == []
    assert "source" in plan["stop_reason"]


def test_resync_missing_staged_key_reports(resync):
    mid = "9100001"
    up = _upstream(mid)
    staged = _digests_from_upstream(mid, up)
    del staged[f"{mid}_phases_of_play"]  # a vanished upstream file
    plan = resync.plan_match(_entry(mid, up), staged)
    assert plan["to_put"] == []
    assert "missing" in plan["stop_reason"]


# --------------------------------------------------------------------------- refresh_entry (pure)


def test_resync_carries_entry_fields(resync):
    mid = "9100001"
    up = _upstream(mid)
    entry = _entry(mid, up)
    new_meta = {f"{mid}_dynamic_events": {"sha256": "c" * 64, "size": 7}}
    out = resync.refresh_entry(entry, new_meta, _REV, _NOW)
    assert out["source"]["revision"] == _REV
    assert out["artifact_meta"][f"{mid}_dynamic_events"] == {"sha256": "c" * 64, "size": 7}
    assert out["updated_at"] == _NOW
    # every other field carried verbatim
    for field in ("home", "away", "date", "provenance", "visibility", "id"):
        assert out[field] == entry[field]
    for field in ("name", "url", "licence"):
        assert out["source"][field] == entry["source"][field]


# --------------------------------------------------------------------------- preflights


def test_preflight_media_sha_asserts_206(resync):
    resync.preflight_media_sha(lambda: 206)  # no raise
    with pytest.raises(RuntimeError):
        resync.preflight_media_sha(lambda: 200)


class _IfMatchHonouringS3:
    def __init__(self):
        self.store = {"skillcorner/matches.json": b'{"matches": []}'}

    def _etag(self, key):
        return '"' + hashlib.md5(self.store[key]).hexdigest() + '"'  # noqa: S324 - test ETag

    def get_object(self, Bucket, Key, Range=None):
        return {"Body": io.BytesIO(self.store[Key]), "ETag": self._etag(Key), "VersionId": "v1"}

    def put_object(self, Bucket, Key, Body, IfMatch=None, **kw):
        if IfMatch is not None and IfMatch != self._etag(Key):
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.store[Key] = Body


class _IfMatchIgnoringS3(_IfMatchHonouringS3):
    def put_object(self, Bucket, Key, Body, IfMatch=None, **kw):
        self.store[Key] = Body  # ignores IfMatch entirely


def test_preflight_if_match_requires_412(resync):
    resync.preflight_if_match(_IfMatchHonouringS3(), "b", "skillcorner/matches.json")  # no raise
    with pytest.raises(RuntimeError):
        resync.preflight_if_match(_IfMatchIgnoringS3(), "b", "skillcorner/matches.json")


# --------------------------------------------------------------------------- main (FakeS3)


_PREFLIGHT_SENTINEL = '"resync-preflight-stale-etag"'


class _FakeS3:
    def __init__(self, objects: dict[str, bytes]):
        self.store = dict(objects)
        self.content_types: dict[str, str] = {}  # per-key stored Content-Type, when the object has one
        self.puts: list[dict] = []
        self.fail_put_keys: set[str] = set()  # keys whose (real) put_object raises 412 once

    def _etag(self, key):
        return '"' + hashlib.md5(self.store[key]).hexdigest() + '"'  # noqa: S324 - test ETag

    def get_object(self, Bucket, Key, Range=None):
        data = self.store[Key]
        if Range:
            start, end = (int(x) for x in Range.removeprefix("bytes=").split("-"))
            data = data[start : end + 1]
        version = "ver-" + hashlib.md5(data).hexdigest()[:8]  # noqa: S324 - test VersionId
        out = {"Body": io.BytesIO(data), "ETag": self._etag(Key), "VersionId": version}
        if Key in self.content_types:
            out["ContentType"] = self.content_types[Key]
        return out

    def put_object(self, Bucket, Key, Body, IfMatch=None, ContentType=None, **kw):
        self.puts.append({"Key": Key, "IfMatch": IfMatch, "ContentType": ContentType})
        # The preflight probe uses a known-stale sentinel ETag: evaluate the precondition normally
        # (stale -> 412) but never consume fail_put_keys, which target the real conditional writes.
        if IfMatch == _PREFLIGHT_SENTINEL:
            if Key in self.store and IfMatch != self._etag(Key):
                raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
            self.store[Key] = Body
            return
        if Key in self.fail_put_keys:
            self.fail_put_keys.discard(Key)
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        if IfMatch is not None and Key in self.store and IfMatch != self._etag(Key):
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.store[Key] = Body


def _seed(entries: list[dict], upstreams: dict[str, dict[str, bytes]]) -> _FakeS3:
    objects: dict[str, bytes] = {
        "skillcorner/matches.json": json.dumps({"provider": PROVIDER, "matches": entries}).encode()
    }
    for mid, up in upstreams.items():
        for filename in _artifacts(mid).values():
            raw = up[f"matches/{mid}/{filename}"]
            # the stored object equals the upstream bytes, except tracking is a resolved blob, not a pointer
            stored = b'{"frame": 0}\n' if raw.startswith(b"version https://git-lfs") else raw
            objects[f"{PROVIDER}/{mid}/{filename}"] = stored
    return _FakeS3(objects)


class _NonClosingBytesIO(io.BytesIO):
    def close(self):
        pass


def _run_main(resync, load_script, monkeypatch, s3, fetch_raw, *argv):
    import boto3

    monkeypatch.setattr(resync, "fetch_raw", fetch_raw)
    monkeypatch.setattr(resync, "media_status", lambda *a, **k: 206)
    monkeypatch.setattr(boto3, "client", lambda *a, **k: s3)
    raw = _NonClosingBytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="utf-8"))
    monkeypatch.setattr(sys, "argv", ["resync_skillcorner_opendata", "--bucket", "b", *argv])
    rc = resync.main()
    sys.stdout.flush()
    return rc, raw.getvalue().decode("utf-8")


def _fetch_raw_for(upstreams: dict[str, dict[str, bytes]]):
    merged: dict[str, bytes] = {}
    for up in upstreams.values():
        merged.update(up)

    def fetch(rel: str) -> bytes:
        if rel not in merged:
            raise FileNotFoundError(rel)
        return merged[rel]

    return fetch


def test_resync_puts_changed_csv_with_ifmatch(resync, load_script, monkeypatch):
    mid = "9100001"
    up = _upstream(mid, dyn=b"event,x\npass,0.9\n")  # upstream dynamic differs from stored
    stored_up = _upstream(mid)  # what the stored objects + meta reflect
    s3 = _seed([_entry(mid, stored_up)], {mid: stored_up})
    obj_key = f"{PROVIDER}/{mid}/{mid}_dynamic_events.csv"
    etag_before = s3._etag(obj_key)
    rc, _text = _run_main(resync, load_script, monkeypatch, s3, _fetch_raw_for({mid: up}), "--apply")
    assert rc == 0
    csv_puts = [p for p in s3.puts if p["Key"] == obj_key]
    assert csv_puts == [{"Key": obj_key, "IfMatch": etag_before, "ContentType": "text/csv"}]
    assert s3.store[obj_key] == up[f"matches/{mid}/{mid}_dynamic_events.csv"]
    entry = json.loads(s3.store["skillcorner/matches.json"])["matches"][0]
    assert entry["source"]["revision"] == resync.UPSTREAM_COMMIT
    assert entry["updated_at"] != _OLD


def test_resync_two_matches_refresh_etag(resync, load_script, monkeypatch):
    a, b = "9100001", "9100002"
    up_a = _upstream(a, dyn=b"event,x\npass,0.9\n")
    up_b = _upstream(b, phase=b"phase,start\n2,9\n")
    stored_a, stored_b = _upstream(a), _upstream(b)
    s3 = _seed([_entry(a, stored_a), _entry(b, stored_b)], {a: stored_a, b: stored_b})
    rc, _text = _run_main(resync, load_script, monkeypatch, s3, _fetch_raw_for({a: up_a, b: up_b}), "--apply")
    assert rc == 0
    # exclude the preflight_if_match probe (a stale-ETag put that is rejected, not a real write)
    index_puts = [
        p for p in s3.puts if p["Key"] == "skillcorner/matches.json" and p["IfMatch"] != '"resync-preflight-stale-etag"'
    ]
    assert len(index_puts) == 2  # one real write per match; neither 412 because the ETag is re-read
    entries = {e["id"]: e for e in json.loads(s3.store["skillcorner/matches.json"])["matches"]}
    assert entries[a]["source"]["revision"] == resync.UPSTREAM_COMMIT
    assert entries[b]["source"]["revision"] == resync.UPSTREAM_COMMIT


def test_resync_412_on_artifact_aborts_only_that_match(resync, load_script, monkeypatch):
    a, b = "9100001", "9100002"
    up_a = _upstream(a, dyn=b"event,x\npass,0.9\n")
    up_b = _upstream(b, dyn=b"event,x\npass,0.7\n")
    stored_a, stored_b = _upstream(a), _upstream(b)
    s3 = _seed([_entry(a, stored_a), _entry(b, stored_b)], {a: stored_a, b: stored_b})
    s3.fail_put_keys = {f"{PROVIDER}/{a}/{a}_dynamic_events.csv"}  # match a's CSV put 412s
    _rc, text = _run_main(resync, load_script, monkeypatch, s3, _fetch_raw_for({a: up_a, b: up_b}), "--apply")
    assert a in text  # a reported
    entries = {e["id"]: e for e in json.loads(s3.store["skillcorner/matches.json"])["matches"]}
    assert entries[a]["source"].get("revision") is None  # a not stamped (aborted)
    assert entries[b]["source"]["revision"] == resync.UPSTREAM_COMMIT  # b still done


def test_resync_412_mid_match_aborts_and_reports(resync, load_script, monkeypatch):
    # BOTH csv roles drift, so one match issues two artifact PUTs; the SECOND 412s (a partial write).
    mid = "9100001"
    up = _upstream(mid, dyn=b"event,x\npass,0.9\n", phase=b"phase,start\n2,9\n")
    stored_up = _upstream(mid)
    s3 = _seed([_entry(mid, stored_up)], {mid: stored_up})
    dyn_key = f"{PROVIDER}/{mid}/{mid}_dynamic_events.csv"
    phase_key = f"{PROVIDER}/{mid}/{mid}_phases_of_play.csv"
    s3.fail_put_keys = {phase_key}  # the second CSV PUT 412s
    rc, text = _run_main(resync, load_script, monkeypatch, s3, _fetch_raw_for({mid: up}), "--apply")
    assert rc == 1  # a stopped match -> non-zero exit
    assert mid in text and "sha256=" in text and "size=" in text  # report carries key + sha256 + size
    assert phase_key in text  # the object that 412'd is named
    assert dyn_key in text  # the object already re-put this match is named (rollback aid, "as the backfill does")
    dyn_sha = _sha(up[f"matches/{mid}/{mid}_dynamic_events.csv"])
    assert f"sha256={dyn_sha}" in text  # the re-put CSV's OWN digest is in the written list, not just the failed one
    assert s3.store[dyn_key] == up[f"matches/{mid}/{mid}_dynamic_events.csv"]  # first CSV re-put (partial)
    index_puts = [p for p in s3.puts if p["Key"] == "skillcorner/matches.json" and p["IfMatch"] != _PREFLIGHT_SENTINEL]
    assert index_puts == []  # the index was NOT written for the aborted match
    entry = json.loads(s3.store["skillcorner/matches.json"])["matches"][0]
    assert entry["source"].get("revision") is None  # not stamped


def test_resync_412_on_index_write_aborts_only_that_match(resync, load_script, monkeypatch):
    a, b = "9100001", "9100002"
    up_a = _upstream(a, dyn=b"event,x\npass,0.9\n")
    up_b = _upstream(b, dyn=b"event,x\npass,0.7\n")
    stored_a, stored_b = _upstream(a), _upstream(b)
    s3 = _seed([_entry(a, stored_a), _entry(b, stored_b)], {a: stored_a, b: stored_b})
    s3.fail_put_keys = {"skillcorner/matches.json"}  # match a's index write 412s (consumed once)
    rc, text = _run_main(resync, load_script, monkeypatch, s3, _fetch_raw_for({a: up_a, b: up_b}), "--apply")
    assert rc == 1
    assert "index NOT written" in text
    assert f"{PROVIDER}/{a}/{a}_dynamic_events.csv" in text  # a's already-re-put artifact named for rollback
    entries = {e["id"]: e for e in json.loads(s3.store["skillcorner/matches.json"])["matches"]}
    assert entries[a]["source"].get("revision") is None  # a's index not written
    assert entries[b]["source"]["revision"] == resync.UPSTREAM_COMMIT  # b still converges


def test_resync_preserves_existing_content_type(resync, load_script, monkeypatch):
    # The re-PUT must not change an artifact's stored Content-Type (Hyrum): a consumer may depend on it.
    mid = "9100001"
    up = _upstream(mid, dyn=b"event,x\npass,0.9\n")
    stored_up = _upstream(mid)
    s3 = _seed([_entry(mid, stored_up)], {mid: stored_up})
    dyn_key = f"{PROVIDER}/{mid}/{mid}_dynamic_events.csv"
    s3.content_types[dyn_key] = "application/vnd.ms-excel"  # whatever the original load happened to set
    rc, _text = _run_main(resync, load_script, monkeypatch, s3, _fetch_raw_for({mid: up}), "--apply")
    assert rc == 0
    csv_puts = [p for p in s3.puts if p["Key"] == dyn_key]
    assert csv_puts and all(p["ContentType"] == "application/vnd.ms-excel" for p in csv_puts)


def test_resync_entry_assembly_failure_reports_and_stops(resync, load_script, monkeypatch):
    # An entry whose artifact_meta lacks a protected key makes the assembled MatchEntry invalid; the run
    # must report-and-keep that match, never crash mid-loop (defense-in-depth; cannot occur at this pin).
    mid = "9100001"
    stored_up = _upstream(mid)
    entry = _entry(mid, stored_up)
    del entry["artifact_meta"][f"{mid}_tracking_extrapolated"]  # meta keys no longer equal artifacts keys
    s3 = _seed([entry], {mid: stored_up})
    rc, text = _run_main(resync, load_script, monkeypatch, s3, _fetch_raw_for({mid: _upstream(mid)}), "--apply")
    assert rc == 1
    assert "assembly failed" in text
    out = json.loads(s3.store["skillcorner/matches.json"])["matches"][0]
    assert out["source"].get("revision") is None  # not stamped; no partial write


def test_resync_refresh_refuses_a_forbidden_field_change(resync, monkeypatch):
    # refresh_entry must refuse (raise) if any step would change a field other than
    # artifact_meta / source.revision / updated_at — the carry guard (spec §6.8).
    mid = "9100001"
    up = _upstream(mid)
    entry = _entry(mid, up)

    def tampering_carry(new, stored):  # a downstream helper that alters a forbidden field
        out = dict(new)
        out["home"] = "TAMPERED"
        return out

    monkeypatch.setattr(resync, "carry_updated_at", tampering_carry)
    with pytest.raises(ValueError, match="other than"):
        resync.refresh_entry(entry, {}, _REV, _NOW)


def test_resync_vanished_file_reports_and_keeps(resync, load_script, monkeypatch):
    mid = "9100001"
    up = _upstream(mid, dyn=b"event,x\npass,0.9\n")
    del up[f"matches/{mid}/{mid}_phases_of_play.csv"]  # vanished upstream
    stored_up = _upstream(mid)
    s3 = _seed([_entry(mid, stored_up)], {mid: stored_up})
    kept = dict(s3.store)
    _rc, text = _run_main(resync, load_script, monkeypatch, s3, _fetch_raw_for({mid: up}), "--apply")
    assert mid in text
    # no object deleted; the entry is not stamped (match stopped)
    assert set(s3.store) >= set(kept)
    entry = json.loads(s3.store["skillcorner/matches.json"])["matches"][0]
    assert entry["source"].get("revision") is None


def test_resync_dry_run_writes_nothing(resync, load_script, monkeypatch):
    mid = "9100001"
    up = _upstream(mid, dyn=b"event,x\npass,0.9\n")
    stored_up = _upstream(mid)
    s3 = _seed([_entry(mid, stored_up)], {mid: stored_up})
    before = dict(s3.store)
    rc, _text = _run_main(resync, load_script, monkeypatch, s3, _fetch_raw_for({mid: up}))  # no --apply
    assert rc == 0
    # dry-run mutates nothing: the only put attempted is the rejected preflight probe (412, no write)
    assert s3.store == before
    real_puts = [p for p in s3.puts if p["IfMatch"] != '"resync-preflight-stale-etag"']
    assert real_puts == []


def test_resync_dry_run_prints_field_diff(resync, load_script, monkeypatch):
    # The dry-run must show WHAT each entry would change (spec §6.3 step 4 / §7 step 2), not only the plan.
    mid = "9100001"
    up = _upstream(mid, dyn=b"event,x\npass,0.9\n")  # dynamic_events drifts
    stored_up = _upstream(mid)
    s3 = _seed([_entry(mid, stored_up)], {mid: stored_up})
    rc, text = _run_main(resync, load_script, monkeypatch, s3, _fetch_raw_for({mid: up}))  # no --apply
    assert rc == 0
    assert "diff source:" in text  # source.revision would be stamped
    assert "diff updated_at:" in text  # updated_at would bump
