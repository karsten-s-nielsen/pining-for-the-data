"""Streamed hashing of artifact bytes and the staging-directory artifact map (spec §5.4)."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from mock_api.artifact_meta import (
    ArtifactDigest,
    artifact_name,
    hash_file,
    hash_stream,
    staged_artifact_meta,
    staged_files,
)

_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


@pytest.mark.parametrize("size", [0, 1, 7, 8, 9, 1000])
def test_hash_stream_chunk_boundaries_and_empty(size):
    payload = bytes(i % 251 for i in range(size))
    got = hash_stream(io.BytesIO(payload), 8)
    assert got == (hashlib.sha256(payload).hexdigest(), size)
    if size == 0:
        assert got == ArtifactDigest(_EMPTY_SHA256, 0)


def test_hash_file_matches_bytes(tmp_path: Path):
    path = tmp_path / "blob.bin"
    path.write_bytes(b"synthetic artifact bytes\x00\xff")
    assert hash_file(path) == (hashlib.sha256(b"synthetic artifact bytes\x00\xff").hexdigest(), 26)


def test_digest_as_dict():
    assert ArtifactDigest("ab" * 32, 5).as_dict() == {"sha256": "ab" * 32, "size": 5}


def test_staged_files_skips_dotfiles_and_dirs(tmp_path: Path):
    (tmp_path / "a.json").write_text("{}")
    (tmp_path / ".DS_Store").write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    assert staged_files(tmp_path) == [tmp_path / "a.json"]


def test_staged_files_sorted(tmp_path: Path):
    for name in ("c.txt", "a.txt", "b.txt"):
        (tmp_path / name).write_text(name)
    assert [p.name for p in staged_files(tmp_path)] == ["a.txt", "b.txt", "c.txt"]


def test_artifact_name_strips_all_extensions():
    assert artifact_name("tracking.jsonl.bz2") == "tracking"
    assert artifact_name("5_match.json") == "5_match"


def test_staged_artifact_meta_keys_use_artifact_name(tmp_path: Path):
    (tmp_path / "tracking.jsonl.bz2").write_bytes(b"abc")
    (tmp_path / ".hidden").write_bytes(b"zzz")
    assert staged_artifact_meta(tmp_path) == {
        "tracking": {"sha256": hashlib.sha256(b"abc").hexdigest(), "size": 3},
    }
