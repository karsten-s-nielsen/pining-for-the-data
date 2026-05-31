import os
import re as _re
import tempfile
from pathlib import Path

import pytest

from formats.idsse import (
    MatchInfo,
    artifact_key_for_filename,
    group_files_by_match,
    is_complete,
    local_match_date,
    match_id_from_filename,
    read_match_information,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

MI = "DFL_02_01_matchinformation_DFL-COM-000001_DFL-MAT-ABC123.xml"
EV = "DFL_03_02_events_raw_DFL-COM-000001_DFL-MAT-ABC123.xml"
PO = "DFL_04_03_positions_raw_observed_DFL-COM-000001_DFL-MAT-ABC123.xml"


class TestFilenameParsing:
    def test_match_id_extracted(self) -> None:
        assert match_id_from_filename(MI) == "DFL-MAT-ABC123"

    def test_match_id_absent_returns_none(self) -> None:
        assert match_id_from_filename("competitions.csv") is None

    def test_role_keys(self) -> None:
        assert artifact_key_for_filename(MI) == "metadata"
        assert artifact_key_for_filename(EV) == "events"
        assert artifact_key_for_filename(PO) == "tracking"

    def test_role_key_unknown_returns_none(self) -> None:
        assert artifact_key_for_filename("DFL_99_99_other_DFL-MAT-ABC123.xml") is None


class TestGrouping:
    def test_groups_triplet_by_match(self) -> None:
        groups = group_files_by_match([MI, EV, PO])
        assert groups == {"DFL-MAT-ABC123": {"metadata": MI, "events": EV, "tracking": PO}}

    def test_complete_and_incomplete(self) -> None:
        groups = group_files_by_match([MI, EV, PO])
        assert is_complete(groups["DFL-MAT-ABC123"]) is True
        assert is_complete({"metadata": MI}) is False

    def test_unrecognized_files_skipped(self) -> None:
        groups = group_files_by_match([MI, "README.txt", "players.csv"])
        assert list(groups) == ["DFL-MAT-ABC123"]


class TestLocalMatchDate:
    def test_afternoon_utc_same_date(self) -> None:
        # 13:31 UTC -> 15:31 CEST, same calendar day
        assert local_match_date("2023-05-27T13:31:08.640+00:00") == "2023-05-27"

    def test_late_utc_rolls_into_next_berlin_day(self) -> None:
        # 22:30 UTC -> 00:30 CEST next day: proves Berlin conversion is applied
        assert local_match_date("2099-08-15T22:30:00.000+00:00") == "2099-08-16"

    def test_naive_timestamp_rejected(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            local_match_date("2099-08-15T18:30:00")


class TestReadMatchInformation:
    def setup_method(self) -> None:
        self.info = read_match_information(FIXTURES_DIR / "idsse_matchinformation_synthetic.xml")

    def test_returns_matchinfo(self) -> None:
        assert isinstance(self.info, MatchInfo)

    def test_fields(self) -> None:
        assert self.info.match_id == "DFL-MAT-SYN001"
        assert self.info.home == "Test Home FC"
        assert self.info.away == "Test Away FC"
        assert self.info.date == "2099-08-15"  # 18:31 UTC -> 20:31 CEST, same day

    def test_missing_general_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.xml"
        bad.write_text("<PutDataRequest><MatchInformation/></PutDataRequest>", encoding="utf-8")
        with pytest.raises(ValueError, match="General"):
            read_match_information(bad)


@pytest.mark.skipif(not os.environ.get("IDSSE_E2E"), reason="set IDSSE_E2E=1 to run network-gated figshare e2e")
def test_reader_against_real_matchinformation(load_script) -> None:
    """Fetch one real ~12 KB matchinformation file and assert the reader extracts well-formed values.

    Asserts SHAPE only (regex / YYYY-MM-DD / non-empty) — no hardcoded real DFL identifiers.
    """
    loader = load_script("upload_idsse_bundesliga")  # shared conftest fixture (review R3)
    listing = loader.fetch_file_listing()
    entry = next(e for e in listing if "matchinformation" in e["name"])

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "metadata.xml"
        loader.download_verified(entry, dest)
        info = read_match_information(dest)

    assert _re.fullmatch(r"DFL-MAT-[A-Z0-9]+", info.match_id)
    assert _re.fullmatch(r"\d{4}-\d{2}-\d{2}", info.date)
    assert info.home and info.away
