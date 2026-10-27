"""Provider dispatch for a stored metadata artifact (spec §5.2). Invented fixture values only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from formats.competition_season import CompetitionSeason
from formats.provider_metadata import MetadataIdentity, metadata_artifact_key, read_metadata_identity

FIXTURES = Path(__file__).parent / "fixtures"
_SC_COMPETITION = CompetitionSeason("301", "Synthetic Premier", "701", "2099/2100")


def test_metadata_artifact_key():
    assert metadata_artifact_key("statsbomb", {"artifacts": {"metadata": "metadata.json", "events": "e.json.gz"}}) == (
        "metadata"
    )
    public_sc = {"artifacts": {"5_match": "5_match.json", "5_tracking_extrapolated": "5_t.jsonl"}}
    assert metadata_artifact_key("skillcorner", public_sc) == "5_match"


def test_metadata_artifact_key_zero_match_keys_refused():
    with pytest.raises(ValueError, match="_match"):
        metadata_artifact_key("skillcorner", {"artifacts": {"5_tracking_extrapolated": "5_t.jsonl"}})


def test_metadata_artifact_key_two_match_keys_refused():
    entry = {"artifacts": {"5_match": "5_match.json", "6_match": "6_match.json"}}
    with pytest.raises(ValueError, match="_match"):
        metadata_artifact_key("skillcorner", entry)


def test_metadata_artifact_key_missing_metadata_refused():
    with pytest.raises(ValueError, match="metadata"):
        metadata_artifact_key("idsse", {"artifacts": {"events": "events.xml"}})


@pytest.mark.parametrize(
    ("provider", "visibility", "fixture", "want"),
    [
        (
            "gradientsports",
            "private",
            "gradientsports_metadata_synthetic.json",
            MetadataIdentity("990001", CompetitionSeason("77", "Synthetic Cup", None, "2099")),
        ),
        (
            "idsse",
            "public",
            "idsse_matchinformation_synthetic.xml",
            MetadataIdentity(
                "DFL-MAT-SYN001",
                CompetitionSeason("DFL-COM-000099", "Synthetic League", "DFL-SEA-SYN001", "2099/2100"),
            ),
        ),
        (
            "skillcorner",
            "public",
            "skillcorner_v3_meta_competition_synthetic.json",
            MetadataIdentity("9000002", _SC_COMPETITION),
        ),
        (
            "skillcorner",
            "private",
            "skillcorner_v3_meta_competition_synthetic.json",
            MetadataIdentity("9000002", _SC_COMPETITION),
        ),
    ],
)
def test_read_metadata_identity_dispatch(provider, visibility, fixture, want):
    assert read_metadata_identity(provider, visibility, (FIXTURES / fixture).read_bytes()) == want


def test_read_metadata_identity_statsbomb(sbo_match):
    got = read_metadata_identity("statsbomb", "private", json.dumps(sbo_match).encode("utf-8"))
    assert got == MetadataIdentity("8888888", CompetitionSeason("555555", "Continental Cup", "777", "2026"))


def test_read_metadata_identity_unknown_provider_refused():
    with pytest.raises(ValueError, match="nosuchprovider"):
        read_metadata_identity("nosuchprovider", "public", b"{}")


def test_raw_id_values_keep_source_types():
    from formats.provider_metadata import raw_id_values

    body = json.dumps(
        {"match_id": 8888001, "competition": {"competition_id": 49.0}, "season": {"season_id": 777}}
    ).encode()
    got = raw_id_values("statsbomb", "private", body)
    assert got == {"match_id": 8888001, "competition_id": 49.0, "season_id": 777}
    assert type(got["match_id"]) is int
    assert type(got["competition_id"]) is float


def test_raw_id_values_per_provider():
    from formats.provider_metadata import raw_id_values

    gradient = raw_id_values(
        "gradientsports", "private", (FIXTURES / "gradientsports_metadata_synthetic.json").read_bytes()
    )
    assert gradient == {"match_id": "990001", "competition_id": "77"}  # no season id in the source
    idsse = raw_id_values("idsse", "public", (FIXTURES / "idsse_matchinformation_synthetic.xml").read_bytes())
    assert idsse == {"match_id": "DFL-MAT-SYN001", "competition_id": "DFL-COM-000099", "season_id": "DFL-SEA-SYN001"}
    sc = raw_id_values(
        "skillcorner", "public", (FIXTURES / "skillcorner_v3_meta_competition_synthetic.json").read_bytes()
    )
    assert sc == {"match_id": 9000002, "competition_id": 301, "season_id": 701}
