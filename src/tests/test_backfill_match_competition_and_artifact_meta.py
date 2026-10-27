"""Backfill of competition/season fields and artifact_meta (spec §5.9, §6). In-memory S3; invented data.

Two passes (owner ruling, spec §5.9): pass 1 reads, checks and hashes every selected provider and
writes nothing; any per-entry error stops the run before any write. Pass 2 writes each provider's
matches.json with IfMatch; a write error stops only that provider.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

FIXTURES = Path(__file__).parent / "fixtures"
_NOW = "2099-01-01T00:00:00Z"
_OLD_TS = "2001-01-01T00:00:00Z"
_FIELDS = ("competition_id", "competition_name", "season_id", "season_name")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _statsbomb_meta(match_id: int = 8888001, competition_id: object = 555001) -> bytes:
    return json.dumps(
        {
            "match_id": match_id,
            "match_date": "2099-07-15",
            "competition": {"competition_id": competition_id, "competition_name": "Synthetic Continental Cup"},
            "season": {"season_id": 777, "season_name": "2099"},
            "home_team": {"home_team_name": "Northmoor Synthetic"},
            "away_team": {"away_team_name": "Southford Invented"},
        }
    ).encode()


def _sc_meta(match_id: int = 9000002) -> bytes:
    meta = json.loads((FIXTURES / "skillcorner_v3_meta_competition_synthetic.json").read_text(encoding="utf-8"))
    meta["id"] = match_id
    return json.dumps(meta).encode()


def _gradient_meta(match_id: str = "990001") -> bytes:
    meta = json.loads((FIXTURES / "gradientsports_metadata_synthetic.json").read_text(encoding="utf-8"))
    meta[0]["id"] = match_id
    return json.dumps(meta).encode()


def _idsse_meta(match_id: str = "DFL-MAT-SYN001") -> bytes:
    data = (FIXTURES / "idsse_matchinformation_synthetic.xml").read_bytes()
    return data.replace(b'MatchId="DFL-MAT-SYN001"', f'MatchId="{match_id}"'.encode())


# (provider, visibility, entry id, {artifact key: filename}, metadata-bytes builder, expected four fields)
CASES = {
    "gradientsports": (
        "gradientsports",
        "private",
        "990001",
        {"metadata": "metadata.json", "tracking": "tracking.jsonl.bz2"},
        _gradient_meta,
        {"competition_id": "77", "competition_name": "Synthetic Cup", "season_name": "2099"},
    ),
    "idsse": (
        "idsse",
        "public",
        "DFL-MAT-SYN001",
        {"metadata": "metadata.xml", "tracking": "tracking.xml"},
        _idsse_meta,
        {
            "competition_id": "DFL-COM-000099",
            "competition_name": "Synthetic League",
            "season_id": "DFL-SEA-SYN001",
            "season_name": "2099/2100",
        },
    ),
    "skillcorner-public": (
        "skillcorner",
        "public",
        "9000002",
        {"9000002_match": "9000002_match.json", "9000002_tracking_extrapolated": "9000002_tracking_extrapolated.jsonl"},
        _sc_meta,
        {
            "competition_id": "301",
            "competition_name": "Synthetic Premier",
            "season_id": "701",
            "season_name": "2099/2100",
        },
    ),
    "skillcorner-private": (
        "skillcorner",
        "private",
        "9000002",
        {"metadata": "metadata.json", "tracking": "tracking.parquet"},
        _sc_meta,
        {
            "competition_id": "301",
            "competition_name": "Synthetic Premier",
            "season_id": "701",
            "season_name": "2099/2100",
        },
    ),
    "statsbomb": (
        "statsbomb",
        "private",
        "8888001",
        {"metadata": "metadata.json", "events": "events.json.gz"},
        _statsbomb_meta,
        {
            "competition_id": "555001",
            "competition_name": "Synthetic Continental Cup",
            "season_id": "777",
            "season_name": "2099",
        },
    ),
}


def _meta_key(artifacts: dict[str, str]) -> str:
    return next(k for k in artifacts if k == "metadata" or k.endswith("_match"))


class FakeS3:
    """In-memory S3 double: ETags, IfMatch on put_object (412), NoSuchKey, injectable write failures."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.puts: list[str] = []
        self.bump_before_put: set[str] = set()  # another writer changes the key just before our put
        self.fail_put: dict[str, str] = {}  # key -> ClientError code

    def etag(self, key: str) -> str:
        return '"' + hashlib.md5(self.store[key]).hexdigest() + '"'  # noqa: S324 - a test ETag, not security

    def get_object(self, Bucket, Key):
        if Key not in self.store:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.store[Key]), "ETag": self.etag(Key)}

    def put_object(self, Bucket, Key, Body, IfMatch=None, **kw):
        self.puts.append(Key)
        if Key in self.fail_put:
            raise ClientError({"Error": {"Code": self.fail_put[Key]}}, "PutObject")
        if Key in self.bump_before_put:
            self.store[Key] = self.store[Key] + b" "
        if IfMatch is not None and Key in self.store and IfMatch != self.etag(Key):
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.store[Key] = Body


def _prefix(provider: str, visibility: str, entry_id: str) -> str:
    return f"{provider}/_private/{entry_id}" if visibility == "private" else f"{provider}/{entry_id}"


def _seed_case(s3: FakeS3, case: str, *, meta_bytes: bytes | None = None, entry_extra: dict | None = None) -> dict:
    provider, visibility, entry_id, artifacts, meta_builder, _want = CASES[case]
    entry_id = (entry_extra or {}).get("id", entry_id)
    prefix = _prefix(provider, visibility, entry_id)
    meta_key = _meta_key(artifacts)
    for key, filename in artifacts.items():
        body = (meta_bytes or meta_builder()) if key == meta_key else f"synthetic {key} bytes".encode()
        s3.store[f"{prefix}/{filename}"] = body
    entry = {
        "id": entry_id,
        "artifacts": artifacts,
        "visibility": visibility,
        "updated_at": _OLD_TS,
        "date": "2099-07-01",
        "home": "Synthetic Home",
        "away": "Invented Away",
        "provenance": "original",
        "source": {"name": "Synthetic Source", "url": "", "licence": "Invented"},
        "format_version": 2,
        "x_note": "kept verbatim",
        **(entry_extra or {}),
    }
    index_key = f"{provider}/matches.json"
    index = json.loads(s3.store[index_key]) if index_key in s3.store else {"provider": provider, "matches": []}
    index["matches"].append(entry)
    s3.store[index_key] = json.dumps(index, indent=2).encode()
    return entry


def _index(s3: FakeS3, provider: str) -> list[dict]:
    return json.loads(s3.store[f"{provider}/matches.json"])["matches"]


@pytest.fixture
def bf(load_script):
    return load_script("backfill_match_competition_and_artifact_meta")


class _NonClosingBytesIO(io.BytesIO):
    def close(self) -> None:
        pass


def _run_main(bf, monkeypatch, s3: FakeS3, *argv: str) -> tuple[int, str]:
    import boto3

    out = io.StringIO()
    monkeypatch.setattr(boto3, "client", lambda *_a, **_k: s3)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "argv", ["backfill_match_competition_and_artifact_meta", "--bucket", "b", *argv])
    rc = bf.main()
    return rc, out.getvalue()


# --- per entry -------------------------------------------------------------------------------


@pytest.mark.parametrize("case", list(CASES))
def test_sets_four_fields_and_artifact_meta_from_stored_objects(bf, case):
    from collections import Counter

    s3 = FakeS3()
    entry = _seed_case(s3, case)
    provider, _visibility, _entry_id, artifacts, _builder, want = CASES[case]
    new = bf.backfill_entry(s3, "b", provider, entry, _NOW, Counter())
    assert {k: new[k] for k in _FIELDS if k in new} == want
    assert set(new["artifact_meta"]) == set(artifacts)


def test_hashes_equal_stored_bytes(bf):
    from collections import Counter

    s3 = FakeS3()
    entry = _seed_case(s3, "statsbomb")
    new = bf.backfill_entry(s3, "b", "statsbomb", entry, _NOW, Counter())
    for key, filename in entry["artifacts"].items():
        stored = s3.store[f"statsbomb/_private/8888001/{filename}"]
        assert new["artifact_meta"][key] == {"sha256": _sha(stored), "size": len(stored)}


def test_other_fields_copied_unchanged(bf):
    from collections import Counter

    s3 = FakeS3()
    entry = _seed_case(s3, "skillcorner-private")
    new = bf.backfill_entry(s3, "b", "skillcorner", entry, _NOW, Counter())
    for key in ("date", "home", "away", "artifacts", "visibility", "provenance", "source", "format_version", "x_note"):
        assert new[key] == entry[key]


def test_gradientsports_season_name_only(bf):
    from collections import Counter

    s3 = FakeS3()
    entry = _seed_case(s3, "gradientsports")
    new = bf.backfill_entry(s3, "b", "gradientsports", entry, _NOW, Counter())
    assert new["season_name"] == json.loads(_gradient_meta())[0]["season"]
    assert "season_id" not in new


def test_object_key_tiers(bf):
    entry = {"id": "m1", "visibility": "private"}
    assert bf.object_key("statsbomb", entry, "metadata.json") == "statsbomb/_private/m1/metadata.json"
    assert bf.object_key("idsse", {"id": "m2", "visibility": "public"}, "metadata.xml") == "idsse/m2/metadata.xml"


# --- whole runs through main() ----------------------------------------------------------------


def test_updated_at_only_on_changed_entries(bf, monkeypatch):
    s3 = FakeS3()
    _seed_case(s3, "statsbomb")
    rc, _ = _run_main(bf, monkeypatch, s3, "--provider", "statsbomb", "--apply")
    assert rc == 0
    done = _index(s3, "statsbomb")[0]
    assert done["updated_at"] != _OLD_TS
    # A second provider entry that is already complete keeps its updated_at; a new one gets the run time.
    completed = {**done, "updated_at": _OLD_TS}
    s3.store["statsbomb/matches.json"] = json.dumps({"provider": "statsbomb", "matches": [completed]}).encode()
    _seed_case(s3, "statsbomb", meta_bytes=_statsbomb_meta(8888002), entry_extra={"id": "8888002"})
    rc, _ = _run_main(bf, monkeypatch, s3, "--provider", "statsbomb", "--apply")
    assert rc == 0
    by_id = {e["id"]: e for e in _index(s3, "statsbomb")}
    assert by_id["8888001"]["updated_at"] == _OLD_TS
    assert by_id["8888002"]["updated_at"] != _OLD_TS


def test_second_run_changes_nothing(bf, monkeypatch):
    s3 = FakeS3()
    _seed_case(s3, "skillcorner-public")
    assert _run_main(bf, monkeypatch, s3, "--provider", "skillcorner", "--apply")[0] == 0
    after_first = s3.store["skillcorner/matches.json"]
    rc, text = _run_main(bf, monkeypatch, s3, "--provider", "skillcorner", "--apply")
    assert rc == 0
    assert "changed=0" in text
    assert s3.store["skillcorner/matches.json"] == after_first


def test_dry_run_writes_nothing_and_reports_types(bf, monkeypatch):
    s3 = FakeS3()
    _seed_case(s3, "statsbomb")
    _seed_case(s3, "gradientsports")
    rc, text = _run_main(bf, monkeypatch, s3, "--provider", "statsbomb", "--provider", "gradientsports")
    assert rc == 0
    assert s3.puts == []
    prepared = bf.prepare_provider(s3, "b", "statsbomb", _NOW)
    assert prepared.report.id_types == {"int": 3}
    assert bf.prepare_provider(s3, "b", "gradientsports", _NOW).report.id_types == {"str": 2}
    assert "int" in text and "str" in text


def test_report_starts_with_script_sha256(bf, monkeypatch):
    s3 = FakeS3()
    _seed_case(s3, "statsbomb")
    _rc, text = _run_main(bf, monkeypatch, s3, "--provider", "statsbomb")
    assert text.splitlines()[0].endswith(bf.script_sha256())
    assert bf.script_sha256() == _sha(Path(bf.__file__).read_bytes())


@pytest.mark.parametrize(
    ("case", "wrong"),
    [
        ("gradientsports", _gradient_meta("990009")),
        ("idsse", _idsse_meta("DFL-MAT-SYN009")),
        ("skillcorner-public", _sc_meta(9000009)),
        ("skillcorner-private", _sc_meta(9000009)),
        ("statsbomb", _statsbomb_meta(8888009)),
    ],
)
def test_match_id_mismatch_fails_loudly(bf, monkeypatch, case, wrong):
    s3 = FakeS3()
    clean = "idsse" if case != "idsse" else "statsbomb"
    _seed_case(s3, clean)
    _seed_case(s3, case, meta_bytes=wrong)
    provider, _vis, entry_id, *_rest = CASES[case]
    prepared = bf.prepare_provider(s3, "b", provider, _NOW)
    assert entry_id in prepared.report.error
    wrong_id = next(i for i in ("990009", "DFL-MAT-SYN009", "9000009", "8888009") if i in prepared.report.error)
    assert wrong_id
    rc, text = _run_main(bf, monkeypatch, s3, "--provider", CASES[clean][0], "--provider", provider, "--apply")
    assert rc == 1
    assert [k for k in s3.puts if k.endswith("matches.json")] == []
    assert "run stopped after pass 1: no provider written" in text


def test_float_id_stops_run_before_any_write(bf, monkeypatch):
    s3 = FakeS3()
    _seed_case(s3, "gradientsports")  # provider A: clean
    _seed_case(s3, "statsbomb", meta_bytes=_statsbomb_meta(competition_id=49.0))  # provider B
    rc, text = _run_main(bf, monkeypatch, s3, "--provider", "gradientsports", "--provider", "statsbomb", "--apply")
    assert rc == 1
    assert s3.puts == []
    assert "run stopped after pass 1: no provider written" in text
    assert "float" in text
    assert bf.prepare_provider(s3, "b", "statsbomb", _NOW).report.error


def test_missing_object_stops_run_before_any_write(bf, monkeypatch):
    s3 = FakeS3()
    _seed_case(s3, "gradientsports")  # provider A: clean
    _seed_case(s3, "statsbomb")
    del s3.store["statsbomb/_private/8888001/events.json.gz"]  # one stored object of provider B is missing
    rc, text = _run_main(bf, monkeypatch, s3, "--provider", "gradientsports", "--provider", "statsbomb", "--apply")
    assert rc == 1
    assert s3.puts == []
    assert "NoSuchKey" in text


def test_put_uses_ifmatch(bf):
    s3 = FakeS3()
    _seed_case(s3, "idsse")
    etag = s3.etag("idsse/matches.json")
    seen: list[object] = []
    real_put = s3.put_object

    def spy(**kw):
        seen.append(kw.get("IfMatch"))
        return real_put(**kw)

    s3.put_object = spy  # type: ignore[method-assign]
    prepared = bf.prepare_provider(s3, "b", "idsse", _NOW)
    bf.write_provider(s3, "b", prepared)
    assert seen == [etag]
    assert prepared.report.written


def test_412_on_one_provider_others_written(bf, monkeypatch):
    s3 = FakeS3()
    _seed_case(s3, "gradientsports")  # provider A
    _seed_case(s3, "idsse")  # provider B
    s3.bump_before_put.add("gradientsports/matches.json")
    rc, text = _run_main(bf, monkeypatch, s3, "--provider", "gradientsports", "--provider", "idsse", "--apply")
    assert rc == 1
    assert "conflict (412)" in text
    assert "artifact_meta" not in _index(s3, "gradientsports")[0]
    assert "artifact_meta" in _index(s3, "idsse")[0]


def test_pass2_other_write_error_reported_others_written(bf, monkeypatch):
    s3 = FakeS3()
    _seed_case(s3, "gradientsports")  # provider A
    _seed_case(s3, "idsse")  # provider B
    s3.fail_put["gradientsports/matches.json"] = "AccessDenied"
    rc, text = _run_main(bf, monkeypatch, s3, "--provider", "gradientsports", "--provider", "idsse", "--apply")
    assert rc == 1
    assert "artifact_meta" in _index(s3, "idsse")[0]
    lines = {line.split()[1]: line for line in text.splitlines() if line.startswith("provider ")}
    assert "written=False" in lines["gradientsports"] and "write failed" in lines["gradientsports"]
    assert "written=True" in lines["idsse"]


def test_write_provider_never_raises(bf):
    s3 = FakeS3()
    _seed_case(s3, "idsse")
    prepared = bf.prepare_provider(s3, "b", "idsse", _NOW)
    s3.put_object = MagicMock(side_effect=OSError("network down"))  # type: ignore[method-assign]
    bf.write_provider(s3, "b", prepared)
    assert prepared.report.error == "write failed: OSError: network down"
    assert not prepared.report.written


def test_default_providers_from_providers_json(bf, monkeypatch):
    s3 = FakeS3()
    _seed_case(s3, "idsse")
    s3.store["providers.json"] = json.dumps({"providers": ["idsse"]}).encode()
    rc, text = _run_main(bf, monkeypatch, s3)
    assert rc == 0
    assert "provider idsse" in text


# --- parity with the upload path ---------------------------------------------------------------


@pytest.mark.parametrize("case", list(CASES))
def test_parity_with_upload_game(bf, tmp_path, case):
    from collections import Counter

    from formats.provider_metadata import read_metadata_identity
    from mock_api.upload import upload_game

    provider, visibility, entry_id, artifacts, builder, _want = CASES[case]
    meta_key = _meta_key(artifacts)
    staging = tmp_path / "stage"
    staging.mkdir()
    for key, filename in artifacts.items():
        (staging / filename).write_bytes(builder() if key == meta_key else f"synthetic {key} bytes".encode())
    competition = read_metadata_identity(provider, visibility, builder()).competition

    mock_s3 = MagicMock()
    mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
    mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey()
    with patch("mock_api.upload.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_s3
        upload_game(staging, provider, entry_id, "b", visibility=visibility, **competition.as_kwargs())
    put = next(c for c in mock_s3.put_object.call_args_list if c.kwargs["Key"] == f"{provider}/matches.json")
    uploaded = json.loads(put.kwargs["Body"].decode())["matches"][0]

    s3 = FakeS3()
    _seed_case(s3, case)
    backfilled = bf.backfill_entry(s3, "b", provider, _index(s3, provider)[0], _NOW, Counter())
    assert {k: uploaded.get(k) for k in _FIELDS} == {k: backfilled.get(k) for k in _FIELDS}
    assert uploaded["artifact_meta"] == backfilled["artifact_meta"]


def test_absent_source_id_reported_as_absent_not_nonetype(bf):
    """B-IMPL-01: an id the source does not give is counted as "absent", so the dry-run's type report
    shows only real source types (int, str, float, ...) and absent ids apart from them."""
    from collections import Counter

    meta = json.loads(_statsbomb_meta())
    del meta["season"]["season_id"]
    s3 = FakeS3()
    entry = _seed_case(s3, "statsbomb", meta_bytes=json.dumps(meta).encode())
    id_types: Counter[str] = Counter()
    new = bf.backfill_entry(s3, "b", "statsbomb", entry, _NOW, id_types)
    assert id_types == {"int": 2, "absent": 1}
    assert "season_id" not in new
