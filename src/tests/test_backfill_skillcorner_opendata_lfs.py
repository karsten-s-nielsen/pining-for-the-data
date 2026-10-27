"""Tests for the opendata LFS backfill's pure decision helper (synthetic data only).

scripts/ is not a package; the `load_script` fixture loads the script by path. No S3 or network
is touched here — only the pure `select_repairs` predicate is exercised.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def backfill(load_script):
    return load_script("backfill_skillcorner_opendata_lfs")


_POINTER_HEAD = b"version https://git-lfs.github.com/spec/v1\noid sha256:"
_REAL_HEAD = b'{"frame": 0, "timestamp": null, "period": null}'


class TestSelectRepairs:
    def test_selects_only_pointer_bodies(self, backfill) -> None:
        probes = [
            ("1874553", _POINTER_HEAD),  # pointer (a 10-100 MB blob -> 133-byte pointer today)
            ("1886347", _REAL_HEAD),  # real blob -> skip
            ("2016236", _POINTER_HEAD),  # pointer
        ]
        assert backfill.select_repairs(probes) == ["1874553", "2016236"]

    def test_empty_when_all_real(self, backfill) -> None:
        assert backfill.select_repairs([("1886347", _REAL_HEAD)]) == []

    def test_ignores_size_only_looks_at_body(self, backfill) -> None:
        # A 134-byte pointer (a >=100 MB blob) must still be selected — no byte-count gate.
        big = _POINTER_HEAD + b"a" * 64 + b"\nsize 123456789\n"  # 9-digit size
        assert backfill.select_repairs([("9999999", big)]) == ["9999999"]


# --- artifact_meta refresh and the IfMatch index write (spec §5.8a) --------------------------
# Invented match ids only.

import hashlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

from botocore.exceptions import ClientError  # noqa: E402

from canonical.models import MatchEntry  # noqa: E402

_MID = "9100001"
_OTHER = "9100002"
_NOW = "2099-01-01T00:00:00Z"
_OLD_TS = "2001-01-01T00:00:00Z"
_BLOB = b'{"frame": 0}\n{"frame": 1}\n'
_TRACKING_KEY = f"{_MID}_tracking_extrapolated"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _artifacts(mid: str) -> dict[str, str]:
    return {
        f"{mid}_match": f"{mid}_match.json",
        f"{mid}_tracking_extrapolated": f"{mid}_tracking_extrapolated.jsonl",
        f"{mid}_dynamic_events": f"{mid}_dynamic_events.csv",
    }


def _objects(mid: str) -> dict[str, bytes]:
    return {
        f"skillcorner/{mid}/{mid}_match.json": b'{"id": %s}' % mid.encode(),
        f"skillcorner/{mid}/{mid}_tracking_extrapolated.jsonl": _pointer(_BLOB),
        f"skillcorner/{mid}/{mid}_dynamic_events.csv": b"index\n1\n",
    }


def _pointer(blob: bytes) -> bytes:
    return f"version https://git-lfs.github.com/spec/v1\noid sha256:{_sha(blob)}\nsize {len(blob)}\n".encode()


def _entry(mid: str, *, with_meta: bool, objects: dict[str, bytes] | None = None) -> dict:
    entry = {"id": mid, "artifacts": _artifacts(mid), "visibility": "public", "updated_at": _OLD_TS, "x_note": "keep"}
    if with_meta:
        objs = objects or _objects(mid)
        entry["artifact_meta"] = {
            k: {"sha256": _sha(objs[f"skillcorner/{mid}/{f}"]), "size": len(objs[f"skillcorner/{mid}/{f}"])}
            for k, f in entry["artifacts"].items()
        }
    return entry


class TestRefreshEntries:
    def test_refresh_sets_hash_of_new_bytes_and_updated_at(self, backfill) -> None:
        stored = _entry(_MID, with_meta=True)
        index = {"provider": "skillcorner", "matches": [stored]}
        out = backfill.refresh_entries(index, {_MID: _BLOB}, lambda key: pytest.fail(f"hashed {key}"), _NOW)
        entry = out["matches"][0]
        assert entry["artifact_meta"][_TRACKING_KEY] == {"sha256": _sha(_BLOB), "size": len(_BLOB)}
        assert entry["updated_at"] == _NOW
        others = {k: v for k, v in entry.items() if k not in ("artifact_meta", "updated_at")}
        assert others == {k: v for k, v in stored.items() if k not in ("artifact_meta", "updated_at")}
        for key in (f"{_MID}_match", f"{_MID}_dynamic_events"):
            assert entry["artifact_meta"][key] == stored["artifact_meta"][key]

    def test_refresh_untouched_entry_unchanged(self, backfill) -> None:
        untouched = _entry(_OTHER, with_meta=True)
        index = {"provider": "skillcorner", "matches": [_entry(_MID, with_meta=True), untouched]}
        out = backfill.refresh_entries(index, {_MID: _BLOB}, lambda key: pytest.fail(f"hashed {key}"), _NOW)
        assert out["matches"][1] == untouched

    def test_refresh_entry_without_meta_gets_complete_meta(self, backfill) -> None:
        from mock_api.artifact_meta import ArtifactDigest

        objs = _objects(_MID)
        hashed: list[str] = []

        def digest_of(key: str):
            hashed.append(key)
            return ArtifactDigest(_sha(objs[key]), len(objs[key]))

        index = {"provider": "skillcorner", "matches": [_entry(_MID, with_meta=False)]}
        entry = backfill.refresh_entries(index, {_MID: _BLOB}, digest_of, _NOW)["matches"][0]
        assert sorted(hashed) == sorted(
            [f"skillcorner/{_MID}/{_MID}_match.json", f"skillcorner/{_MID}/{_MID}_dynamic_events.csv"]
        )
        assert set(entry["artifact_meta"]) == set(entry["artifacts"])
        assert entry["artifact_meta"][_TRACKING_KEY] == {"sha256": _sha(_BLOB), "size": len(_BLOB)}
        MatchEntry.model_validate(entry)


class _LfsFakeS3:
    """In-memory S3 double with ETags, IfMatch on put_object, Range GET, upload_file and head_object."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.store = dict(objects)
        self.puts: list[dict] = []
        self.bump_etag_after_read = False  # simulate another writer between read and write

    def _etag(self, key: str) -> str:
        return '"' + hashlib.md5(self.store[key]).hexdigest() + '"'  # noqa: S324 - a test ETag, not security

    def get_object(self, Bucket, Key, Range=None):
        data = self.store[Key]
        if Range:
            start, end = (int(x) for x in Range.removeprefix("bytes=").split("-"))
            data = data[start : end + 1]
        etag = self._etag(Key)
        if Key.endswith("matches.json") and self.bump_etag_after_read:
            self.store[Key] = self.store[Key] + b" "
        return {"Body": io.BytesIO(data), "ETag": etag}

    def put_object(self, Bucket, Key, Body, IfMatch=None, **kw):
        self.puts.append({"Key": Key, "IfMatch": IfMatch})
        if IfMatch is not None and Key in self.store and IfMatch != self._etag(Key):
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.store[Key] = Body

    def upload_file(self, filename, Bucket, Key):
        self.store[Key] = Path(filename).read_bytes()

    def head_object(self, Bucket, Key):
        return {"ContentLength": len(self.store[Key])}


def _seed(index_entries: list[dict], *, pointer: bool = True) -> _LfsFakeS3:
    objects = _objects(_MID)
    if not pointer:
        objects[f"skillcorner/{_MID}/{_MID}_tracking_extrapolated.jsonl"] = _BLOB
    objects["skillcorner/matches.json"] = json.dumps({"provider": "skillcorner", "matches": index_entries}).encode()
    return _LfsFakeS3(objects)


class _NonClosingBytesIO(io.BytesIO):
    def close(self) -> None:
        pass


def _run_main(backfill, load_script, monkeypatch, s3: _LfsFakeS3, *argv: str) -> tuple[int, str]:
    import boto3

    od = load_script("upload_skillcorner_opendata")
    monkeypatch.setattr(od, "fetch_resolved", lambda rel: _BLOB)
    monkeypatch.setattr(boto3, "client", lambda *_a, **_k: s3)
    raw = _NonClosingBytesIO()  # main() rewraps sys.stdout.buffer, so its output lands here
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="utf-8"))
    monkeypatch.setattr(sys, "argv", ["backfill_skillcorner_opendata_lfs", "--bucket", "b", *argv])
    rc = backfill.main()
    sys.stdout.flush()
    return rc, raw.getvalue().decode("utf-8")


class TestMain:
    def test_apply_writes_index_with_ifmatch(self, backfill, load_script, monkeypatch) -> None:
        s3 = _seed([_entry(_MID, with_meta=True)])
        etag_before = s3._etag("skillcorner/matches.json")
        rc, _ = _run_main(backfill, load_script, monkeypatch, s3, "--apply")
        assert rc == 0
        index_puts = [p for p in s3.puts if p["Key"] == "skillcorner/matches.json"]
        assert index_puts == [{"Key": "skillcorner/matches.json", "IfMatch": etag_before}]
        entry = json.loads(s3.store["skillcorner/matches.json"])["matches"][0]
        assert entry["artifact_meta"][_TRACKING_KEY] == {"sha256": _sha(_BLOB), "size": len(_BLOB)}
        assert entry["updated_at"] != _OLD_TS
        assert s3.store[f"skillcorner/{_MID}/{_MID}_tracking_extrapolated.jsonl"] == _BLOB

    def test_main_exit_codes(self, backfill, load_script, monkeypatch) -> None:
        clean = _seed([_entry(_MID, with_meta=True)], pointer=False)
        assert _run_main(backfill, load_script, monkeypatch, clean)[0] == 0
        assert clean.puts == []
        dry = _seed([_entry(_MID, with_meta=True)])
        assert _run_main(backfill, load_script, monkeypatch, dry)[0] == 0
        assert dry.puts == []
        assert dry.store[f"skillcorner/{_MID}/{_MID}_tracking_extrapolated.jsonl"] == _pointer(_BLOB)

    def test_apply_412_after_replace_reports_and_exits_1(self, backfill, load_script, monkeypatch) -> None:
        s3 = _seed([_entry(_MID, with_meta=True)])
        index_before = s3.store["skillcorner/matches.json"]
        s3.bump_etag_after_read = True  # another writer changes matches.json after our read
        rc, text = _run_main(backfill, load_script, monkeypatch, s3, "--apply")
        assert rc == 1
        assert s3.store[f"skillcorner/{_MID}/{_MID}_tracking_extrapolated.jsonl"] == _BLOB  # object replaced
        assert s3.store["skillcorner/matches.json"].rstrip(b" ") == index_before  # index not written
        assert f"skillcorner/{_MID}/{_MID}_tracking_extrapolated.jsonl" in text
        assert _sha(_BLOB) in text
