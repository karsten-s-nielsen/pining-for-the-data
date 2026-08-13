"""Tests for the StatsBomb verify script's pure, offline-testable checks.

Only `_check_metadata_shape` / `_nested_field_present` are covered: they are the
script's sole logic that needs neither a network nor credentials. The invariant under
test is that NOTHING here raises, whatever the artifact contains — an exception
escaping the `failures` accumulator would abort the per-artifact loop before the
remaining artifacts' public-token 404 leak checks ran, letting one malformed artifact
suppress a licence-boundary assertion.
"""

import json

import pytest

FEED_SHAPED = {
    "competition": {"competition_id": 11, "competition_name": "Invented League"},
    "home_team": {"home_team_id": 90001, "home_team_name": "Alpha City"},
}


@pytest.fixture(scope="module")
def vmod(load_script):
    return load_script("verify_statsbomb_load")  # shared conftest fixture


def _run(vmod, payload: object) -> tuple[list[str], bytes]:
    failures: list[str] = []
    body = json.dumps(payload).encode("utf-8")
    vmod._check_metadata_shape(body, failures)
    return failures, body


def test_well_formed_metadata_passes_and_prints(vmod, capsys) -> None:
    """The unchanged happy path: no failure recorded, the OK line still printed."""
    failures, _ = _run(vmod, FEED_SHAPED)
    assert failures == []
    assert "OK: metadata is a single object in feed shape" in capsys.readouterr().out


@pytest.mark.parametrize("scalar", [7, 1.5, "Invented League", True, None])
def test_scalar_section_records_a_failure_instead_of_raising(vmod, scalar) -> None:
    """`"competition_id" not in 7` raises TypeError; a string silently substring-matches.

    Either way the check must degrade to a recorded failure, never an exception.
    """
    failures, _ = _run(vmod, {**FEED_SHAPED, "competition": scalar})
    assert len(failures) == 1
    assert "metadata.competition" in failures[0]
    assert "expected a JSON object" in failures[0]


def test_string_section_does_not_substring_match_its_way_to_a_pass(vmod, capsys) -> None:
    """A string containing the field name must NOT satisfy the check."""
    failures, _ = _run(vmod, {**FEED_SHAPED, "competition": "competition_id"})
    assert len(failures) == 1
    assert "OK: metadata is a single object in feed shape" not in capsys.readouterr().out


def test_list_section_records_a_failure(vmod) -> None:
    failures, _ = _run(vmod, {**FEED_SHAPED, "home_team": [{"home_team_id": 1}]})
    assert len(failures) == 1
    assert "metadata.home_team is list" in failures[0]


def test_missing_section_records_a_failure(vmod) -> None:
    failures, _ = _run(vmod, {"home_team": FEED_SHAPED["home_team"]})
    assert len(failures) == 1
    assert "metadata.competition is NoneType" in failures[0]


def test_section_present_but_field_absent_records_a_failure(vmod) -> None:
    failures, _ = _run(vmod, {**FEED_SHAPED, "home_team": {"home_team_name": "Alpha City"}})
    assert failures == ["metadata.home_team lacks 'home_team_id' — not nested in feed shape"]


def test_first_failure_wins_no_cascade(vmod) -> None:
    """Both sections broken still yields ONE line — the original elif chain's semantics."""
    failures, _ = _run(vmod, {"competition": 1, "home_team": 2})
    assert len(failures) == 1
    assert "metadata.competition" in failures[0]


def test_non_object_payload_records_a_failure(vmod) -> None:
    failures, _ = _run(vmod, [FEED_SHAPED])
    assert failures == ["metadata artifact is list, expected a JSON object"]


def test_undecodable_json_records_a_failure_instead_of_raising(vmod) -> None:
    failures: list[str] = []
    vmod._check_metadata_shape(b"{not json", failures)
    assert len(failures) == 1
    assert "not decodable JSON" in failures[0]


def test_undecodable_utf8_records_a_failure_instead_of_raising(vmod) -> None:
    """A truncated multi-byte sequence must not escape as UnicodeDecodeError."""
    failures: list[str] = []
    vmod._check_metadata_shape(b"\xff\xfe", failures)
    assert len(failures) == 1
    assert "not decodable JSON" in failures[0]
