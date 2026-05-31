# IDSSE Bundesliga Public Redistribution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the IDSSE/Sportec open Bundesliga dataset as a new **public** provider (`idsse`) served through the existing mock provider API, redistributing the raw DFL XML as-is.

**Architecture:** A small `formats/idsse.py` reader parses only the ~12 KB matchinformation XML into index metadata (the positions/events XML are served byte-for-byte, never parsed). A worked ops adapter `scripts/upload_idsse_bundesliga.py` fetches the version-pinned figshare release, verifies it against a committed md5 manifest, groups files into per-match triplets, stages them under role-aligned artifact names, and calls the existing `upload_game` primitive (public tier). A `verify_idsse_load.py` post-load check and attribution updates (NOTICE/README) complete it. No Lambda or Terraform changes — the API is already provider-generic.

**Tech Stack:** Python 3.12, stdlib only for new modules (`xml.etree`, `zoneinfo`, `urllib`, `hashlib`, `json`), `boto3` via the existing `upload_game`, pytest. Spec: `docs/superpowers/specs/2026-05-29-idsse-bundesliga-redistribution-design.md`.

**Reference facts (verified against the live figshare v1 release):**
- Versioned API endpoint: `https://api.figshare.com/v2/articles/28196177/versions/1`; only `v1` exists. File objects expose `name`, `download_url`, `computed_md5`, `supplied_md5`, `size`. The manifest pins `computed_md5` (figshare's hash of the stored bytes). 21 files, ~2.63 GB.
- DFL filename patterns (the figshare `name` field carries hyphenated ids): `DFL_02_01_matchinformation_<COM>_<MAT>.xml`, `DFL_03_02_events_raw_<COM>_<MAT>.xml`, `DFL_04_03_positions_raw_observed_<COM>_<MAT>.xml`. Match id token matches `DFL-MAT-[A-Z0-9]+`.
- matchinformation XML is **namespace-free**: root `<PutDataRequest>` → `<MatchInformation>` → `<General>` with attributes `MatchId`, `KickoffTime` (tz-aware ISO 8601, e.g. `2023-05-27T13:31:08.640+00:00`), `PlannedKickoffTime`, `HomeTeamName`, `GuestTeamName`.
- Role-aligned artifact keys: `metadata` (matchinformation), `events` (events_raw), `tracking` (positions_raw_observed).
- `upload_game` derives each artifact key from the staged filename stem (`name.split(".",1)[0]`), so staging `metadata.xml`/`events.xml`/`tracking.xml` yields keys `metadata`/`events`/`tracking`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/formats/idsse.py` (create) | Reader: filename→match-id + role-key helpers, `group_files_by_match`, `local_match_date`, `read_match_information`. Parses only matchinformation XML. |
| `src/tests/test_idsse_format.py` (create) | Unit tests for the reader + the opt-in network-gated real-XML e2e. |
| `src/tests/fixtures/idsse_matchinformation_synthetic.xml` (create) | Synthetic, namespace-free matchinformation fixture (no real DFL ids). |
| `scripts/upload_idsse_bundesliga.py` (create) | Ops adapter: figshare versioned fetch, `--write-manifest`, manifest verify, grouping/staging, `upload_game` (public). |
| `scripts/idsse_figshare_manifest.json` (create, generated) | Committed expected-file manifest (21 names + md5s) pinning the figshare version. |
| `src/tests/test_upload_idsse.py` (create) | Unit tests for the adapter's pure logic (manifest verify, staging plan) with mocks. |
| `scripts/verify_idsse_load.py` (create) | Post-load verification against the live API (public tier; Range-checked large artifacts). |
| `src/tests/test_verify_idsse_load.py` (create) | Unit test for the Range/size-check helper with a mocked opener. |
| `NOTICE` (modify) | Add IDSSE/DFL CC-BY attribution stanza. |
| `README.md` (modify) | List `idsse` as a public provider with source + licence. |

---

## Task 1: Reader — filename parsing & grouping

**Files:**
- Create: `src/formats/idsse.py`
- Test: `src/tests/test_idsse_format.py`

- [ ] **Step 1: Write the failing tests**

```python
# src/tests/test_idsse_format.py
from formats.idsse import (
    artifact_key_for_filename,
    group_files_by_match,
    is_complete,
    match_id_from_filename,
)

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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest src/tests/test_idsse_format.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'formats.idsse'`

- [ ] **Step 3: Write the minimal implementation**

```python
# src/formats/idsse.py
"""IDSSE/Sportec (DFL) open Bundesliga format — matchinformation XML reader.

Redistributed as-is (raw DFL XML); see
docs/superpowers/specs/2026-05-29-idsse-bundesliga-redistribution-design.md.

This module parses ONLY the small (~12 KB) matchinformation XML to derive index
metadata for matches.json. The positions/events XML are served byte-for-byte and
are never parsed here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

# figshare `name` fields carry hyphenated DFL ids, e.g. DFL-MAT-J03WN1.
_MATCH_ID_RE = re.compile(r"DFL-MAT-[A-Z0-9]+")

# DFL filename role marker -> role-aligned artifact key (Gradient Sports vocabulary).
# Markers are mutually exclusive across the three DFL file types.
_FILE_ROLE_MARKERS: tuple[tuple[str, str], ...] = (
    ("matchinformation", "metadata"),
    ("positions_raw_observed", "tracking"),
    ("events_raw", "events"),
)

REQUIRED_ARTIFACTS: tuple[str, ...] = ("metadata", "events", "tracking")

_BERLIN = ZoneInfo("Europe/Berlin")


@dataclass(frozen=True)
class MatchInfo:
    """Index metadata derived from one matchinformation XML."""

    match_id: str
    date: str  # YYYY-MM-DD, local (Europe/Berlin) match date
    home: str
    away: str


def match_id_from_filename(filename: str) -> str | None:
    """Return the DFL-MAT-… id embedded in a figshare filename, or None."""
    m = _MATCH_ID_RE.search(filename)
    return m.group(0) if m else None


def artifact_key_for_filename(filename: str) -> str | None:
    """Map a DFL filename to its role-aligned artifact key, or None if unrecognized."""
    for marker, key in _FILE_ROLE_MARKERS:
        if marker in filename:
            return key
    return None


def group_files_by_match(filenames: list[str]) -> dict[str, dict[str, str]]:
    """Group filenames into {match_id: {artifact_key: filename}}.

    Files without a recognizable match id AND role marker are skipped.
    """
    groups: dict[str, dict[str, str]] = {}
    for name in filenames:
        match_id = match_id_from_filename(name)
        key = artifact_key_for_filename(name)
        if match_id is None or key is None:
            continue
        groups.setdefault(match_id, {})[key] = name
    return groups


def is_complete(group: dict[str, str]) -> bool:
    """True if a group has all three required artifacts."""
    return all(k in group for k in REQUIRED_ARTIFACTS)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest src/tests/test_idsse_format.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/formats/idsse.py src/tests/test_idsse_format.py
git commit -m "feat(formats): add IDSSE filename parsing and match grouping"
```

---

## Task 2: Reader — matchinformation parse + Berlin-local date

**Files:**
- Modify: `src/formats/idsse.py`
- Create: `src/tests/fixtures/idsse_matchinformation_synthetic.xml`
- Modify: `src/tests/test_idsse_format.py`

- [ ] **Step 1: Create the synthetic fixture (no real DFL identifiers)**

```xml
<!-- src/tests/fixtures/idsse_matchinformation_synthetic.xml -->
<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<PutDataRequest RequestId="SYN-REQ-0001" MessageTime="2099-01-01T00:00:00.000+00:00" TransmissionComplete="true">
  <MatchInformation>
    <General TypeOfSport="Fußball" CompetitionName="Synthetic League" CompetitionId="DFL-COM-000099"
             MatchDay="1" Season="2099/2100" SeasonId="DFL-SEA-SYN001"
             PlannedKickoffTime="2099-08-15T18:30:00.000+00:00" KickoffTime="2099-08-15T18:31:00.000+00:00"
             MatchId="DFL-MAT-SYN001" MatchTitle="Test Home FC:Test Away FC"
             HomeTeamName="Test Home FC" HomeTeamId="DFL-CLU-SYN0H"
             GuestTeamName="Test Away FC" GuestTeamId="DFL-CLU-SYN0G" Result="0:0" />
    <Teams/>
  </MatchInformation>
</PutDataRequest>
```

- [ ] **Step 2: Write the failing tests**

```python
# append to src/tests/test_idsse_format.py
from pathlib import Path

import pytest

from formats.idsse import MatchInfo, local_match_date, read_match_information

FIXTURES_DIR = Path(__file__).parent / "fixtures"


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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest src/tests/test_idsse_format.py -k "LocalMatchDate or ReadMatchInformation" -v`
Expected: FAIL with `ImportError: cannot import name 'local_match_date'`

- [ ] **Step 4: Add the implementation**

```python
# append to src/formats/idsse.py
def local_match_date(kickoff: str) -> str:
    """ISO date (YYYY-MM-DD) of a tz-aware kickoff timestamp, in Europe/Berlin local time.

    DFL stores KickoffTime as a tz-aware ISO 8601 value (e.g. with a +00:00 UTC
    offset). The canonical match date is the *local* date, so we convert to
    Europe/Berlin before taking the date component.
    """
    dt = datetime.fromisoformat(kickoff)
    if dt.tzinfo is None:
        raise ValueError(f"KickoffTime {kickoff!r} is not timezone-aware")
    return dt.astimezone(_BERLIN).date().isoformat()


def read_match_information(path: Path) -> MatchInfo:
    """Parse a DFL matchinformation XML into index metadata.

    Schema (namespace-free): PutDataRequest > MatchInformation > General, with
    attributes MatchId, KickoffTime (tz-aware) / PlannedKickoffTime fallback,
    HomeTeamName, GuestTeamName.
    """
    general = ET.parse(path).getroot().find("./MatchInformation/General")
    if general is None:
        raise ValueError(f"{path}: no MatchInformation/General element")

    match_id = general.get("MatchId")
    home = general.get("HomeTeamName")
    away = general.get("GuestTeamName")
    kickoff = general.get("KickoffTime") or general.get("PlannedKickoffTime")
    if not (match_id and home and away and kickoff):
        raise ValueError(f"{path}: missing required General attributes")

    return MatchInfo(match_id=match_id, date=local_match_date(kickoff), home=home, away=away)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest src/tests/test_idsse_format.py -v`
Expected: PASS (all tests, ~14)

- [ ] **Step 6: Commit**

```bash
git add src/formats/idsse.py src/tests/test_idsse_format.py src/tests/fixtures/idsse_matchinformation_synthetic.xml
git commit -m "feat(formats): parse IDSSE matchinformation into index metadata"
```

---

## Task 3: Adapter — figshare client, manifest verify, `--write-manifest`

**Files:**
- Create: `scripts/upload_idsse_bundesliga.py`
- Test: `src/tests/test_upload_idsse.py`

- [ ] **Step 1: Add a shared script-loader fixture to `conftest.py`**

`scripts/` is not a package, so tests must import script modules by path. Centralize that in **one** fixture (review R3) rather than repeating the `parents[2]` dance in three test files.

```python
# append to src/tests/conftest.py
import importlib.util
import sys

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture(scope="session")
def load_script():
    """Return a loader that imports scripts/<name>.py as a module.

    Note: loading a script executes its top-level imports (e.g.
    `from mock_api.upload import upload_game`), so even "pure logic" tests
    transitively import the boto3-backed module. That's fine — no AWS calls occur
    at import time.
    """

    def _load(name: str):
        path = _SCRIPTS_DIR / f"{name}.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    return _load
```

- [ ] **Step 2: Write the failing tests for the pure logic**

```python
# src/tests/test_upload_idsse.py
import pytest


@pytest.fixture(scope="module")
def mod(load_script):
    return load_script("upload_idsse_bundesliga")


def _listing() -> list[dict]:
    return [
        {"name": "DFL_02_01_matchinformation_DFL-COM-000001_DFL-MAT-ABC123.xml",
         "download_url": "https://x/1", "computed_md5": "aaa", "size": 12000},
        {"name": "DFL_03_02_events_raw_DFL-COM-000001_DFL-MAT-ABC123.xml",
         "download_url": "https://x/2", "computed_md5": "bbb", "size": 500000},
        {"name": "DFL_04_03_positions_raw_observed_DFL-COM-000001_DFL-MAT-ABC123.xml",
         "download_url": "https://x/3", "computed_md5": "ccc", "size": 900000},
    ]


class TestManifest:
    def test_manifest_from_listing(self, mod) -> None:
        manifest = mod.manifest_from_listing(_listing())
        assert manifest["version_url"].endswith("/versions/1")
        assert manifest["files"] == {
            "DFL_02_01_matchinformation_DFL-COM-000001_DFL-MAT-ABC123.xml": "aaa",
            "DFL_03_02_events_raw_DFL-COM-000001_DFL-MAT-ABC123.xml": "bbb",
            "DFL_04_03_positions_raw_observed_DFL-COM-000001_DFL-MAT-ABC123.xml": "ccc",
        }

    def test_verify_matches(self, mod) -> None:
        manifest = mod.manifest_from_listing(_listing())
        # No exception when listing matches the manifest.
        mod.verify_listing(_listing(), manifest)

    def test_verify_detects_md5_drift(self, mod) -> None:
        manifest = mod.manifest_from_listing(_listing())
        drifted = _listing()
        drifted[0]["computed_md5"] = "ZZZ"
        with pytest.raises(ValueError, match="md5 mismatch"):
            mod.verify_listing(drifted, manifest)

    def test_verify_detects_count_drift(self, mod) -> None:
        manifest = mod.manifest_from_listing(_listing())
        with pytest.raises(ValueError, match="file count"):
            mod.verify_listing(_listing()[:2], manifest)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest src/tests/test_upload_idsse.py -v`
Expected: FAIL with `FileNotFoundError` / module load error (script does not exist yet)

- [ ] **Step 4: Write the adapter scaffolding + figshare/manifest logic**

```python
# scripts/upload_idsse_bundesliga.py
"""Upload the IDSSE/Sportec open Bundesliga dataset to the mock provider API (PUBLIC tier).

Pure redistribution of CC-BY-4.0 data sourced directly from the version-pinned
figshare release (not from bronze). See
docs/superpowers/specs/2026-05-29-idsse-bundesliga-redistribution-design.md.

Modes:
  (default)          fetch -> verify against committed manifest -> stage -> upload_game
  --write-manifest   fetch the versioned listing and (re)write scripts/idsse_figshare_manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

# Make src/ importable when run directly from a checkout.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from formats.idsse import REQUIRED_ARTIFACTS, group_files_by_match, is_complete, read_match_information  # noqa: E402
from mock_api.upload import upload_game  # noqa: E402

PROVIDER = "idsse"
ARTICLE_ID = "28196177"
VERSION = 1
VERSION_URL = f"https://api.figshare.com/v2/articles/{ARTICLE_ID}/versions/{VERSION}"
MANIFEST_PATH = _REPO_ROOT / "scripts" / "idsse_figshare_manifest.json"

SOURCE_NAME = "IDSSE — Bassek, Rein, Weber & Memmert (2025); provided by DFL / Sportec Solutions"
SOURCE_URL = f"https://doi.org/10.6084/m9.figshare.{ARTICLE_ID}.v{VERSION}"
SOURCE_LICENCE = "CC-BY 4.0"

# Staged filename per role key (upload_game derives the artifact key from the stem).
_STAGED_FILENAME = {"metadata": "metadata.xml", "events": "events.xml", "tracking": "tracking.xml"}


def _md5_of(listing_entry: dict) -> str:
    # Pin figshare's computed_md5 (its hash of the stored bytes) deterministically.
    # We do NOT fall back to supplied_md5 (review R2): mixing the two fields between
    # --write-manifest time and verify time could flip and raise a false drift error.
    return listing_entry.get("computed_md5") or ""


def fetch_file_listing(version_url: str = VERSION_URL) -> list[dict]:
    """GET the versioned figshare article and return its `files` list."""
    req = urllib.request.Request(version_url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        article = json.loads(resp.read().decode("utf-8"))
    return article["files"]


def manifest_from_listing(listing: list[dict]) -> dict:
    """Build the pinned manifest {version_url, files: {name: md5}} from a listing.

    Refuses to pin a transient/empty md5 (review R2): if figshare has not yet
    computed a file's md5, --write-manifest fails loudly rather than capturing "".
    """
    files: dict[str, str] = {}
    for entry in listing:
        md5 = _md5_of(entry)
        if not md5:
            raise ValueError(f"figshare entry {entry['name']!r} has no computed_md5 yet — refusing to pin")
        files[entry["name"]] = md5
    return {
        "version_url": VERSION_URL,
        # Real (public) DFL filenames + md5s from the CC-BY release are pinned here
        # intentionally — those ids are part of the public dataset (spec §3.1), unlike
        # test fixtures which stay synthetic (review R5).
        "_note": "Real public DFL ids from the CC-BY release; intentional (spec §3.1). Regenerate with --write-manifest.",
        "files": files,
    }


def verify_listing(listing: list[dict], manifest: dict) -> None:
    """Raise ValueError if the live listing diverges from the committed manifest."""
    expected = manifest["files"]
    if len(listing) != len(expected):
        raise ValueError(f"figshare file count {len(listing)} != manifest {len(expected)} — version drift?")
    for entry in listing:
        name = entry["name"]
        if name not in expected:
            raise ValueError(f"unexpected file not in manifest: {name}")
        if _md5_of(entry) != expected[name]:
            raise ValueError(f"md5 mismatch for {name}: live {_md5_of(entry)} != manifest {expected[name]}")


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_manifest(listing: list[dict], path: Path = MANIFEST_PATH) -> None:
    path.write_text(json.dumps(manifest_from_listing(listing), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote manifest with {len(listing)} files to {path}")


def download_verified(entry: dict, dest: Path) -> None:
    """Stream a figshare file to dest, verifying its md5 against the listing entry."""
    expected = _md5_of(entry)
    h = hashlib.md5()  # noqa: S324 - integrity check vs figshare-published md5, not security
    with urllib.request.urlopen(entry["download_url"], timeout=600) as resp, dest.open("wb") as out:
        for chunk in iter(lambda: resp.read(1 << 20), b""):
            h.update(chunk)
            out.write(chunk)
    if h.hexdigest() != expected:
        raise ValueError(f"md5 mismatch after download of {entry['name']}: {h.hexdigest()} != {expected}")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest src/tests/test_upload_idsse.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add scripts/upload_idsse_bundesliga.py src/tests/test_upload_idsse.py src/tests/conftest.py
git commit -m "feat(scripts): IDSSE figshare client + pinned manifest verification"
```

---

## Task 4: Adapter — staging plan + upload orchestration

**Files:**
- Modify: `scripts/upload_idsse_bundesliga.py`
- Modify: `src/tests/test_upload_idsse.py`

- [ ] **Step 1: Write the failing tests (staging plan + orchestration with mocks)**

```python
# append to src/tests/test_upload_idsse.py
def test_complete_matches_filters_incomplete(mod) -> None:
    names = [
        "DFL_02_01_matchinformation_DFL-COM-000001_DFL-MAT-ABC123.xml",
        "DFL_03_02_events_raw_DFL-COM-000001_DFL-MAT-ABC123.xml",
        "DFL_04_03_positions_raw_observed_DFL-COM-000001_DFL-MAT-ABC123.xml",
        "DFL_02_01_matchinformation_DFL-COM-000001_DFL-MAT-NOPE99.xml",  # lone metadata
    ]
    complete = mod.complete_matches(names)
    assert list(complete) == ["DFL-MAT-ABC123"]


def test_upload_all_orchestration(monkeypatch, mod) -> None:
    """fetch->verify->download->stage->upload_game wired correctly, all I/O mocked."""
    listing = _listing()
    manifest = mod.manifest_from_listing(listing)

    monkeypatch.setattr(mod, "fetch_file_listing", lambda *_a, **_k: listing)
    monkeypatch.setattr(mod, "load_manifest", lambda *_a, **_k: manifest)

    # download just writes a stub file with the expected stem name
    def fake_download(entry, dest):
        dest.write_text(f"<xml for {entry['name']}/>", encoding="utf-8")
    monkeypatch.setattr(mod, "download_verified", fake_download)

    # stub read_match_information so it returns deterministic metadata
    monkeypatch.setattr(mod, "read_match_information", lambda _p: _Info())

    calls = []
    monkeypatch.setattr(mod, "upload_game", lambda **kw: calls.append(kw) or list(kw["game_dir"].iterdir()))

    mod.upload_all(bucket="test-bucket", limit=None)

    assert len(calls) == 1
    kw = calls[0]
    assert kw["provider"] == "idsse"
    assert kw["game_id"] == "DFL-MAT-ABC123"
    assert kw["visibility"] == "public"
    assert kw["provenance"] == "redistributed"
    assert kw["date"] == "2099-08-15"
    assert kw["home"] == "Test Home FC"
    assert kw["away"] == "Test Away FC"
    assert kw["source_name"] == mod.SOURCE_NAME
    assert kw["source_licence"] == "CC-BY 4.0"
    staged = sorted(p.name for p in kw["game_dir"].iterdir())
    assert staged == ["events.xml", "metadata.xml", "tracking.xml"]


class _Info:
    match_id = "DFL-MAT-ABC123"
    date = "2099-08-15"
    home = "Test Home FC"
    away = "Test Away FC"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest src/tests/test_upload_idsse.py -k "complete_matches or orchestration" -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'complete_matches'`

- [ ] **Step 3: Add `complete_matches`, `upload_all`, and `main`**

```python
# append to scripts/upload_idsse_bundesliga.py
def complete_matches(filenames: list[str]) -> dict[str, dict[str, str]]:
    """Return only the match groups that have all three required artifacts.

    Logs and drops incomplete groups (review §8 error handling).
    """
    complete: dict[str, dict[str, str]] = {}
    for match_id, group in group_files_by_match(filenames).items():
        if is_complete(group):
            complete[match_id] = group
        else:
            missing = [k for k in REQUIRED_ARTIFACTS if k not in group]
            print(f"WARN: match {match_id} incomplete (missing {missing}) — skipping")
    return complete


def upload_all(bucket: str, limit: int | None = None) -> int:
    """Fetch, verify, stage, and upload all complete matches. Returns matches uploaded."""
    listing = fetch_file_listing()
    verify_listing(listing, load_manifest())
    by_name = {entry["name"]: entry for entry in listing}

    groups = complete_matches([entry["name"] for entry in listing])
    match_ids = sorted(groups)
    if limit:
        match_ids = match_ids[:limit]
    print(f"Uploading {len(match_ids)} IDSSE match(es) to s3://{bucket}/{PROVIDER}/ (public tier)")

    for match_id in match_ids:
        group = groups[match_id]
        with tempfile.TemporaryDirectory(prefix=f"idsse-{match_id}-") as tmp:
            staging = Path(tmp)
            # Fail-fast (review R1): download + parse the ~12 KB metadata BEFORE the
            # hundreds-of-MB events/tracking, so a present-but-malformed metadata XML
            # skips the match without wasting a large download.
            metadata_dest = staging / _STAGED_FILENAME["metadata"]
            download_verified(by_name[group["metadata"]], metadata_dest)
            try:
                info = read_match_information(metadata_dest)
            except ValueError as e:
                print(f"WARN: match {match_id} metadata unparseable ({e}) — skipping")
                continue
            for key in ("events", "tracking"):
                download_verified(by_name[group[key]], staging / _STAGED_FILENAME[key])
            upload_game(
                game_dir=staging,
                provider=PROVIDER,
                game_id=info.match_id,
                bucket=bucket,
                visibility="public",
                provenance="redistributed",
                date=info.date,
                home=info.home,
                away=info.away,
                source_name=SOURCE_NAME,
                source_url=SOURCE_URL,
                source_licence=SOURCE_LICENCE,
            )
    return len(match_ids)


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload IDSSE Bundesliga open data to the mock provider API (public)")
    parser.add_argument("--bucket", help="S3 bucket name (required unless --write-manifest)")
    parser.add_argument("--limit", type=int, default=None, help="Upload only the first N matches (smoke test)")
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="Fetch the versioned figshare listing and (re)write the committed manifest, then exit",
    )
    args = parser.parse_args()

    if args.write_manifest:
        write_manifest(fetch_file_listing())
        return

    if not args.bucket:
        parser.error("--bucket is required (or use --write-manifest)")

    n = upload_all(bucket=args.bucket, limit=args.limit)
    print(f"Done — {n} match(es) uploaded.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest src/tests/test_upload_idsse.py -v`
Expected: PASS (all adapter tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/upload_idsse_bundesliga.py src/tests/test_upload_idsse.py
git commit -m "feat(scripts): IDSSE staging + public-tier upload orchestration"
```

---

## Task 5: Generate & commit the pinned manifest (ops, network)

> Requires internet (figshare). No AWS needed. One-time pin; re-runnable.
>
> **Note (R5):** the manifest legitimately contains real (public) DFL filenames + md5s. That is consistent with the project's test-fixture convention, *not* a violation of it — those ids are part of the public CC-BY release (spec §3.1), whereas synthetic data is required only for *test fixtures*. The manifest's `_note` field records this so the choice is visibly deliberate.

- [ ] **Step 1: Generate the manifest from the versioned listing**

Run: `python scripts/upload_idsse_bundesliga.py --write-manifest`
Expected: `Wrote manifest with 21 files to .../scripts/idsse_figshare_manifest.json`

- [ ] **Step 2: Sanity-check the manifest**

Run: `python -c "import json;d=json.load(open('scripts/idsse_figshare_manifest.json'));print(len(d['files']), d['version_url'])"`
Expected: `21 https://api.figshare.com/v2/articles/28196177/versions/1`

- [ ] **Step 3: Commit the pinned manifest**

```bash
git add scripts/idsse_figshare_manifest.json
git commit -m "chore(scripts): pin IDSSE figshare v1 manifest (21 files)"
```

---

## Task 6: Opt-in network-gated real-XML reader e2e

**Files:**
- Modify: `src/tests/test_idsse_format.py`

- [ ] **Step 1: Add the gated e2e test (asserts shape, not real values — N5)**

```python
# append to src/tests/test_idsse_format.py
import os
import re as _re
import tempfile

import pytest


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
```

- [ ] **Step 2: Verify it is skipped by default**

Run: `python -m pytest src/tests/test_idsse_format.py -v`
Expected: the e2e shows `SKIPPED` (others PASS)

- [ ] **Step 3: Run it once against the network to confirm the real schema**

Run (PowerShell): `$env:IDSSE_E2E=1; python -m pytest src/tests/test_idsse_format.py::test_reader_against_real_matchinformation -v; Remove-Item Env:IDSSE_E2E`
Expected: PASS. If it FAILS, the real DFL schema differs from Task 2's assumptions — correct `read_match_information` and the synthetic fixture to match, then re-run Task 2 tests.

- [ ] **Step 4: Commit**

```bash
git add src/tests/test_idsse_format.py
git commit -m "test(formats): opt-in network-gated IDSSE real-XML reader e2e"
```

---

## Task 7: Post-load verification script

**Files:**
- Create: `scripts/verify_idsse_load.py`
- Test: `src/tests/test_verify_idsse_load.py`

- [ ] **Step 1: Write the failing test for the Range/size helper**

```python
# src/tests/test_verify_idsse_load.py
import pytest


@pytest.fixture(scope="module")
def vmod(load_script):
    return load_script("verify_idsse_load")  # shared conftest fixture (review R3)


def test_parse_content_range_total(vmod) -> None:
    assert vmod.parse_content_range_total("bytes 0-0/123456") == 123456
    assert vmod.parse_content_range_total("") == -1
    assert vmod.parse_content_range_total("bytes */123") == 123
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest src/tests/test_verify_idsse_load.py -v`
Expected: FAIL (module/attr missing)

- [ ] **Step 3: Write the verification script**

```python
# scripts/verify_idsse_load.py
"""Post-load verification for the IDSSE Bundesliga public dataset.

Asserts (public tier — this is open redistributable data, visible to everyone):
  - public-tier /idsse/matches returns EXPECTED_MATCH_COUNT entries
  - public /providers includes 'idsse'
  - a dateFrom/dateTo query returns a non-empty, bounded subset
  - each sampled match serves all three artifacts: metadata fully (200 + non-empty),
    events/tracking via a Range GET (206 + positive total) to avoid downloading GBs
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

EXPECTED_MATCH_COUNT = 7
ARTIFACTS_PER_MATCH = ["metadata", "events", "tracking"]
LARGE_ARTIFACTS = {"events", "tracking"}


def parse_content_range_total(header: str) -> int:
    """Return the total size from a `Content-Range: bytes a-b/total` header, or -1."""
    m = re.search(r"/(\d+)\s*$", header or "")
    return int(m.group(1)) if m else -1


def _get_json(api: str, path: str, token: str) -> dict:
    req = urllib.request.Request(f"{api}{path}", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


class _NoFollow(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


def _presigned_url(api: str, path: str, token: str) -> str:
    """Return the presigned S3 URL from the API's 302 (without following it)."""
    req = urllib.request.Request(f"{api}{path}", headers={"Authorization": f"Bearer {token}"})
    opener = urllib.request.build_opener(_NoFollow)
    try:
        with opener.open(req, timeout=30) as resp:
            raise RuntimeError(f"expected 302 from {path}, got {resp.status}")
    except urllib.error.HTTPError as e:
        if e.code != 302:
            raise
        location = e.headers.get("Location")
        if not location:
            raise RuntimeError(f"302 from {path} but no Location header") from e
        return location


def check_artifact(api: str, path: str, token: str, *, large: bool) -> tuple[int, int]:
    """Return (status, byte_total). Large artifacts use a Range GET (no full download)."""
    location = _presigned_url(api, path, token)
    if large:
        # Range GET — a GET (NOT HEAD): the presigned URL is signed for GET, so HEAD
        # would fail signature validation. bytes=0-0 fetches a single byte; S3 returns
        # 206 with Content-Range: bytes 0-0/<total>.
        s3_req = urllib.request.Request(location, headers={"Range": "bytes=0-0"})
        with urllib.request.urlopen(s3_req, timeout=60) as resp:
            return resp.status, parse_content_range_total(resp.headers.get("Content-Range", ""))
    s3_req = urllib.request.Request(location)
    with urllib.request.urlopen(s3_req, timeout=60) as resp:
        return resp.status, len(resp.read())


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the IDSSE Bundesliga public dataset is loaded correctly")
    parser.add_argument("--api", required=True, help="API base URL (no trailing slash)")
    parser.add_argument("--public-token", required=True)
    args = parser.parse_args()

    failures: list[str] = []

    matches: list[dict] = []
    try:
        body = _get_json(args.api, "/idsse/matches", args.public_token)
        matches = body.get("matches", [])
        if len(matches) != EXPECTED_MATCH_COUNT:
            failures.append(f"public /idsse/matches: expected {EXPECTED_MATCH_COUNT}, got {len(matches)}")
        else:
            print(f"OK: public /idsse/matches = {len(matches)}")
    except Exception as e:  # noqa: BLE001
        failures.append(f"public /idsse/matches: request failed: {e}")

    try:
        providers = _get_json(args.api, "/providers", args.public_token).get("providers", [])
        if "idsse" not in providers:
            failures.append("public /providers: 'idsse' missing")
        else:
            print("OK: public /providers contains 'idsse'")
    except Exception as e:  # noqa: BLE001
        failures.append(f"public /providers: request failed: {e}")

    # Date filter: every match has a date; an open-ended dateFrom must return them all,
    # and an impossible window must return none.
    if matches:
        dates = sorted(m.get("date", "") for m in matches if m.get("date"))
        if dates:
            lo = dates[0]
            try:
                got = _get_json(args.api, f"/idsse/matches?dateFrom={lo}", args.public_token).get("matches", [])
                if len(got) != len(matches):
                    failures.append(f"dateFrom={lo}: expected {len(matches)}, got {len(got)}")
                else:
                    print(f"OK: dateFrom={lo} returns all {len(got)}")
                none = _get_json(args.api, "/idsse/matches?dateTo=1900-01-01", args.public_token).get("matches", [])
                if none:
                    failures.append(f"dateTo=1900-01-01: expected 0, got {len(none)}")
                else:
                    print("OK: dateTo=1900-01-01 returns 0")
            except Exception as e:  # noqa: BLE001
                failures.append(f"date-filter check failed: {e}")
        else:
            failures.append("no match has a 'date' field — date filtering would be broken")

    # Artifact checks on the first match (size-aware).
    if matches:
        mid = matches[0]["id"]
        for artifact in ARTIFACTS_PER_MATCH:
            path = f"/idsse/matches/{mid}/{artifact}"
            try:
                status, total = check_artifact(args.api, path, args.public_token, large=artifact in LARGE_ARTIFACTS)
                ok = (status in (200, 206)) and total > 0
                if ok:
                    print(f"OK: {path} -> {status}, {total}B")
                else:
                    failures.append(f"{path}: status={status}, total={total}")
            except Exception as e:  # noqa: BLE001
                failures.append(f"{path}: failed: {e}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nAll post-conditions pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the unit test to verify it passes**

Run: `python -m pytest src/tests/test_verify_idsse_load.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_idsse_load.py src/tests/test_verify_idsse_load.py
git commit -m "feat(scripts): IDSSE post-load verification (size-aware artifact checks)"
```

---

## Task 8: Attribution — NOTICE + README

**Files:**
- Modify: `NOTICE`
- Modify: `README.md`

- [ ] **Step 1: Inspect current attribution surfaces**

Run: `python -c "print(open('NOTICE', encoding='utf-8').read())"`
Then locate the providers section in `README.md` (search for `skillcorner` / `Provider`).

- [ ] **Step 2: Append an IDSSE stanza to `NOTICE`**

Add (adapt heading style to match the existing SkillCorner stanza):

```text
IDSSE — Integrated spatiotemporal & event data (German Bundesliga)
------------------------------------------------------------------
This product redistributes the IDSSE open dataset, licensed under CC-BY 4.0:

  Bassek, M., Rein, R., Weber, H., & Memmert, D. (2025).
  An integrated dataset of spatiotemporal and event data in elite soccer.
  Scientific Data, 12(1), 195. https://doi.org/10.1038/s41597-025-04505-y

Data provided with the authorization of the Deutsche Fußball Liga (DFL) and
Sportec Solutions, and published under CC-BY 4.0. Source dataset (version 1):
https://doi.org/10.6084/m9.figshare.28196177.v1

Redistributed as-is (raw DFL XML), without modification.
```

- [ ] **Step 3: Add `idsse` to the README provider listing**

In the README's provider/data section, add a row/entry consistent with the existing format, e.g.:

```text
- `idsse` (public) — IDSSE/Sportec open Bundesliga: 7 matches, raw DFL XML (25 fps).
  Artifacts: `metadata`, `events`, `tracking` (the DFL matchinformation / events_raw /
  positions_raw_observed files respectively). Source: Bassek et al. (2025), CC-BY 4.0,
  provided with DFL/Sportec authorization (https://doi.org/10.6084/m9.figshare.28196177.v1).
```

- [ ] **Step 4: Verify docs build/lint cleanly (no broken references)**

Run: `python -m pytest src/tests -q` (ensure no doc-driven tests break) and visually confirm the NOTICE/README render.

- [ ] **Step 5: Commit**

```bash
git add NOTICE README.md
git commit -m "docs: attribute IDSSE/DFL CC-BY data and list the idsse public provider"
```

---

## Task 9: Full local quality gate + final-review

- [ ] **Step 1: Run the full hermetic test suite**

Run: `python -m pytest src/tests -q`
Expected: all pass; the IDSSE e2e shows SKIPPED.

- [ ] **Step 2: Lint and type-check**

Run: `ruff check . ; ruff format --check . ; pyright`
Expected: clean (fix any findings before proceeding).

- [ ] **Step 3: Run the `final-review` skill**

Invoke the `final-review` skill (project pre-commit quality gate, per CLAUDE.md). Address any drift/consistency findings it raises.

- [ ] **Step 4: Commit any fixes**

```bash
git add -A
git commit -m "chore: address final-review findings for IDSSE provider"
```

---

## Task 10: Live load + post-load verification (OPERATOR-RUN — requires AWS)

> This performs the real public-tier upload to S3 and depends on AWS credentials, the
> `PINING_BUCKET`, and network access to figshare (~2.63 GB download/upload). Per the
> project's external-action policy, **pause and run these with the operator**; do not
> assume credentials or proceed unattended.

- [ ] **Step 1: Smoke-test with a single match**

Run: `python scripts/upload_idsse_bundesliga.py --bucket $env:PINING_BUCKET --limit 1`
Expected: 1 match uploaded; `idsse/matches.json` + `providers.json` updated.

- [ ] **Step 2: Full load**

Run: `python scripts/upload_idsse_bundesliga.py --bucket $env:PINING_BUCKET`
Expected: `Done — 7 match(es) uploaded.`

- [ ] **Step 3: Post-load verification**

Run: `python scripts/verify_idsse_load.py --api <API_BASE_URL>/v1 --public-token <PUBLIC_TOKEN>`
Expected: `All post-conditions pass.`

- [ ] **Step 4: Record completion**

Update the project memory / changelog noting the `idsse` public provider is live (7 matches, 21 artifacts).

---

## Self-Review

**Spec coverage:**
- §2/§2.1 figshare-direct provenance → Tasks 3–5 (versioned fetch, no bronze). ✓
- §2.2 version pin + reproducible manifest → Task 3 (`verify_listing`, `--write-manifest`) + Task 5. ✓
- §3/§3.1 raw XML, slug `idsse`, real `DFL-MAT` game_id, role-aligned keys → Tasks 1, 4. ✓
- §4 components (reader, loader, manifest, verify, tests, NOTICE, README) → Tasks 1–4, 7, 8. ✓
- §5 data flow incl. provenance kwarg → Task 4. ✓
- §5.1 large-file streaming → Task 3 `download_verified`. ✓
- §6 date filter parity + Berlin-local date → Task 2 (`local_match_date`) + Task 7 date check. ✓
- §6.1 namespace-free reader + synthetic fixture + opt-in real-XML e2e → Tasks 2, 6. ✓
- §7 attribution: `source.*` in upload + separate `provenance` field + NOTICE + README → Tasks 4, 8. ✓
- §8 error handling (incomplete triplet skip, md5 fail, idempotent re-run) → Tasks 3, 4. ✓
- §9 testing (reader unit, grouping, staging mock, real-XML e2e, verify Range, gate) → Tasks 1–4, 6, 7, 9. ✓
- §10 out of scope (no HF, no /players, no Terraform) → respected; no tasks touch them. ✓

**Placeholder scan:** No `TBD`/`add error handling`/`similar to`/`write tests for the above`. Every code step shows complete code; ops-only steps (Tasks 5, 9, 10) give exact commands with expected output.

**Type consistency:** `MatchInfo(match_id, date, home, away)` used identically in Tasks 2/4/6; `group_files_by_match`→`{match_id:{key:filename}}` consumed by `complete_matches`/`upload_all` with the same shape; artifact keys `metadata`/`events`/`tracking` consistent across reader, `_STAGED_FILENAME`, verify `ARTIFACTS_PER_MATCH`; `upload_game(**kwargs)` names match `src/mock_api/upload.py:upload_game` exactly.

**Commit policy:** Each task ends in a commit step, but this repo gates `git commit` behind the approval sentinel and the maintainer commits explicitly — treat the commit steps as checkpoints to request approval, not to run unattended.
