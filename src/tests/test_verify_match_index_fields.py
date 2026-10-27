"""Comparison logic of the live index verify (spec §5.11, §6). Invented data; no network."""

from __future__ import annotations

import urllib.error

import pytest

from formats.competition_season import CompetitionSeason
from formats.provider_metadata import MetadataIdentity

_COMPETITION = CompetitionSeason("301", "Synthetic Premier", "701", "2099/2100")
_ENTRY = {
    "id": "9000002",
    "artifacts": {"metadata": "metadata.json", "tracking": "tracking.parquet"},
    "artifact_meta": {"metadata": {"sha256": "a" * 64, "size": 10}, "tracking": {"sha256": "b" * 64, "size": 20}},
    "competition_id": "301",
    "competition_name": "Synthetic Premier",
    "season_id": "701",
    "season_name": "2099/2100",
}


@pytest.fixture
def vf(load_script):
    return load_script("verify_match_index_fields")


def test_check_entry_passes_on_equal_values(vf):
    assert vf.check_entry(_ENTRY, MetadataIdentity("9000002", _COMPETITION)) == []


@pytest.mark.parametrize("field", ["competition_id", "competition_name", "season_id", "season_name"])
def test_check_entry_reports_each_mismatch(vf, field):
    problems = vf.check_entry({**_ENTRY, field: "999"}, MetadataIdentity("9000002", _COMPETITION))
    assert len(problems) == 1
    assert field in problems[0]
    assert "9000002" in problems[0]


def test_check_entry_reports_match_id_mismatch(vf):
    problems = vf.check_entry(_ENTRY, MetadataIdentity("9000009", _COMPETITION))
    assert len(problems) == 1
    assert "9000009" in problems[0]


def test_check_entry_reports_key_set_mismatch(vf):
    meta = {"metadata": _ENTRY["artifact_meta"]["metadata"]}
    problems = vf.check_entry({**_ENTRY, "artifact_meta": meta}, MetadataIdentity("9000002", _COMPETITION))
    assert len(problems) == 1
    assert "artifact_meta" in problems[0]


def test_check_entry_reports_missing_artifact_meta(vf):
    entry = {k: v for k, v in _ENTRY.items() if k != "artifact_meta"}
    problems = vf.check_entry(entry, MetadataIdentity("9000002", _COMPETITION))
    assert len(problems) == 1
    assert "artifact_meta" in problems[0]


def test_absent_field_must_be_absent_in_identity(vf):
    entry = {k: v for k, v in _ENTRY.items() if k != "season_id"}
    assert (
        vf.check_entry(
            entry, MetadataIdentity("9000002", CompetitionSeason("301", "Synthetic Premier", None, "2099/2100"))
        )
        == []
    )
    problems = vf.check_entry(entry, MetadataIdentity("9000002", _COMPETITION))
    assert len(problems) == 1
    assert "season_id" in problems[0]


def test_compare_digest_equal_passes(vf):
    from mock_api.artifact_meta import ArtifactDigest

    assert vf.compare_digest("tracking", {"sha256": "b" * 64, "size": 20}, ArtifactDigest("b" * 64, 20)) == []


def test_compare_digest_sha_mismatch_reported(vf):
    from mock_api.artifact_meta import ArtifactDigest

    problems = vf.compare_digest("tracking", {"sha256": "b" * 64, "size": 20}, ArtifactDigest("c" * 64, 20))
    assert len(problems) == 1
    assert "tracking" in problems[0]
    assert "b" * 64 in problems[0] and "c" * 64 in problems[0]


def test_compare_digest_size_mismatch_reported(vf):
    from mock_api.artifact_meta import ArtifactDigest

    problems = vf.compare_digest("tracking", {"sha256": "b" * 64, "size": 20}, ArtifactDigest("b" * 64, 21))
    assert len(problems) == 1
    assert "tracking" in problems[0] and "20" in problems[0] and "21" in problems[0]


def test_size_from_range_206(vf):
    assert vf.size_from_range(206, "bytes 0-0/419430400", lambda: pytest.fail("no full fetch on 206")) == 419430400


def test_size_from_range_416_empty_body_is_zero(vf):
    assert vf.size_from_range(416, None, lambda: b"") == 0


def test_size_from_range_416_nonempty_body_fails(vf):
    with pytest.raises(ValueError, match="416"):
        vf.size_from_range(416, None, lambda: b"x")


def test_size_from_range_other_status_fails(vf):
    with pytest.raises(ValueError, match="200"):
        vf.size_from_range(200, None, lambda: b"")


def test_size_from_range_206_without_total_fails(vf):
    with pytest.raises(ValueError, match="Content-Range"):
        vf.size_from_range(206, "bytes 0-0/*", lambda: b"")


def test_rate_limiter_spacing(vf):
    now = [0.0]
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    limiter = vf.RateLimiter(8.0, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        limiter.wait()
    assert now[0] >= 0.25
    assert now[0] < 0.26


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://invalid.example/", code, "synthetic", {}, None)  # type: ignore[arg-type]


def test_with_retry_429_then_ok(vf):
    outcomes: list[object] = [_http_error(429), _http_error(503), "ok"]
    slept: list[float] = []

    def call() -> str:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return str(outcome)

    assert vf.with_retry(call, sleep=slept.append) == "ok"
    assert slept == [1, 2]


def test_with_retry_gives_up_after_attempts(vf):
    calls: list[int] = []

    def call() -> str:
        calls.append(1)
        raise _http_error(500)

    with pytest.raises(urllib.error.HTTPError):
        vf.with_retry(call, attempts=5, sleep=lambda _s: None)
    assert len(calls) == 5


def test_with_retry_does_not_retry_4xx(vf):
    calls: list[int] = []

    def call() -> str:
        calls.append(1)
        raise _http_error(404)

    with pytest.raises(urllib.error.HTTPError):
        vf.with_retry(call, sleep=lambda _s: pytest.fail("no retry on 404"))
    assert len(calls) == 1
