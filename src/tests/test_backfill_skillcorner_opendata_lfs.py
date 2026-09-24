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
