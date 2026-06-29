# Restricted SkillCorner (Real Madrid) Owner-Tier Ingest — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Commit gate:** This repo gates `git commit` behind a per-call approval sentinel (`~/.claude-git-approval`). Show each commit command and wait for the maintainer's `!touch` before committing. Do not bypass hooks.

**Goal:** Ingest ~99 restricted SkillCorner Real Madrid matches into the existing `skillcorner` provider at owner tier (`visibility="private"`), served unchanged through the mock provider API.

**Architecture:** A pure, stdlib-only reader (`src/formats/skillcorner_bundle.py`) that parses only the small `meta/*.json` and exposes the artifact role→filename map; a worked ops adapter (`scripts/upload_skillcorner_realmadrid.py`) that stages 5 artifacts per match into a fresh temp dir (gzipping the 143 MB tracking JSON as a streamed byte copy), calls the existing `upload_game(..., visibility="private", provenance="original")`, then derives an owner-tier `/players` catalogue from `meta.players` (skip-and-report any id already public) and uploads it via `upload_players`; a post-load verifier; and ADR 0009. No Lambda/Terraform changes.

**Tech Stack:** Python 3.12, stdlib (`json`, `gzip`, `datetime`, `zoneinfo`, `shutil`, `tempfile`, `pathlib`), boto3 (in the adapter only), pytest, ruff, pyright. Spec: `docs/superpowers/specs/2026-06-29-skillcorner-restricted-realmadrid-owner-tier-design.md`.

---

## File Structure

- **Create** `src/formats/skillcorner_bundle.py` — pure reader: `ARTIFACT_SPECS`, `MatchInfo`, `local_match_date`, `discover_matches`, `source_files`, `missing_artifacts`, `is_complete`, `load_meta`, `match_info`, `players_from_meta`.
- **Create** `src/tests/test_skillcorner_bundle_format.py` — reader + player-derivation unit tests.
- **Create** `src/tests/fixtures/skillcorner_bundle_meta_synthetic.json` — synthetic `meta` JSON (no real ids/names).
- **Create** `scripts/upload_skillcorner_realmadrid.py` — owner-tier worked adapter.
- **Create** `src/tests/test_upload_skillcorner_realmadrid.py` — adapter staging/gzip/collision/orchestration tests (mocked S3 + mocked `upload_game`/`upload_players`).
- **Create** `scripts/verify_skillcorner_realmadrid_load.py` — post-load verifier (owner + public token).
- **Create** `docs/decisions/0009-restricted-tier-under-existing-public-provider.md` — ADR.
- **Modify** `docs/decisions/README.md` — add the 0009 index row.
- **Modify** `README.md` — note `skillcorner` now also carries owner-tier restricted matches.
- **Modify** `pyproject.toml` — add ruff per-file-ignores for the new scripts.

---

## Task 1: Reader — date conversion + meta parsing

**Files:**
- Create: `src/formats/skillcorner_bundle.py`
- Test: `src/tests/test_skillcorner_bundle_format.py`

- [ ] **Step 1: Write the failing test**

Create `src/tests/test_skillcorner_bundle_format.py`:

```python
from pathlib import Path

import pytest

from formats.skillcorner_bundle import (
    MatchInfo,
    local_match_date,
    match_info,
)


class TestLocalMatchDate:
    def test_z_suffix_evening_same_local_date(self) -> None:
        # 19:30 UTC -> 21:30 Europe/Madrid (CEST), same calendar day
        assert local_match_date("2023-08-12T19:30:00Z") == "2023-08-12"

    def test_offset_form_supported(self) -> None:
        assert local_match_date("2023-08-12T19:30:00+00:00") == "2023-08-12"

    def test_late_utc_rolls_into_next_madrid_day(self) -> None:
        # 23:30 UTC -> 01:30 CEST next day: proves Madrid conversion is applied
        assert local_match_date("2099-08-15T23:30:00Z") == "2099-08-16"

    def test_naive_timestamp_rejected(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            local_match_date("2099-08-15T18:30:00")


class TestMatchInfo:
    def test_match_info_from_meta_dict(self) -> None:
        meta = {
            "id": 1234567,
            "date_time": "2023-08-12T19:30:00Z",
            "home_team": {"id": 1, "name": "Home Town FC", "short_name": "Home FC"},
            "away_team": {"id": 2, "name": "Away City CF", "short_name": "Away CF"},
        }
        info = match_info(meta)
        assert isinstance(info, MatchInfo)
        assert info.match_id == "1234567"  # coerced to str
        assert info.date == "2023-08-12"
        assert info.home == "Home FC"  # short_name preferred
        assert info.away == "Away CF"

    def test_match_info_falls_back_to_name_when_no_short_name(self) -> None:
        meta = {
            "id": 9,
            "date_time": "2023-08-12T19:30:00Z",
            "home_team": {"id": 1, "name": "Home Town FC"},
            "away_team": {"id": 2, "name": "Away City CF"},
        }
        info = match_info(meta)
        assert info.home == "Home Town FC"
        assert info.away == "Away City CF"

    def test_match_info_missing_fields_raises(self) -> None:
        with pytest.raises(ValueError, match="missing required meta"):
            match_info({"id": 9, "home_team": {"name": "x"}})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest src/tests/test_skillcorner_bundle_format.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'formats.skillcorner_bundle'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/formats/skillcorner_bundle.py`:

```python
"""SkillCorner multi-artifact bundle reader (Soccermatics-course distribution).

Pure, I/O-light reader for the owner-tier restricted SkillCorner Real Madrid data.
Parses ONLY the small ``meta/*.json``; the large tracking/events/freeze/physical
bodies are never read here (the adapter stages them as-is). See
docs/superpowers/specs/2026-06-29-skillcorner-restricted-realmadrid-owner-tier-design.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_MADRID = ZoneInfo("Europe/Madrid")

# (role_key, source_subdir, source_ext, staged_filename)
# upload_game derives the artifact key from the staged filename's stem
# (name.split(".", 1)[0]): tracking.json.gz -> "tracking", events.parquet -> "events".
ARTIFACT_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("tracking", "tracking", ".json", "tracking.json.gz"),
    ("events", "dynamic", ".parquet", "events.parquet"),
    ("freeze_frames", "freeze", ".parquet", "freeze_frames.parquet"),
    ("metadata", "meta", ".json", "metadata.json"),
    ("physical", "physical", ".parquet", "physical.parquet"),
)


@dataclass(frozen=True)
class MatchInfo:
    """Index metadata derived from one meta JSON."""

    match_id: str
    date: str  # YYYY-MM-DD, local (Europe/Madrid) match date
    home: str
    away: str


def local_match_date(timestamp: str) -> str:
    """ISO date (YYYY-MM-DD) of a tz-aware timestamp, in Europe/Madrid local time.

    meta.date_time is a tz-aware ISO 8601 value (e.g. ``...Z`` or ``+00:00``). The
    canonical match date is the *local* date, so convert to Europe/Madrid before
    taking the date component. Mirrors formats/idsse.py:local_match_date.
    """
    dt = datetime.fromisoformat(timestamp)
    if dt.tzinfo is None:
        raise ValueError(f"date_time {timestamp!r} is not timezone-aware")
    return dt.astimezone(_MADRID).date().isoformat()


def _team_label(team: dict | None) -> str | None:
    if not team:
        return None
    return team.get("short_name") or team.get("name")


def match_info(meta: dict) -> MatchInfo:
    """Derive index metadata from a loaded meta dict. Raises on missing fields."""
    match_id = meta.get("id")
    home = _team_label(meta.get("home_team"))
    away = _team_label(meta.get("away_team"))
    timestamp = meta.get("date_time")
    if match_id is None or not home or not away or not timestamp:
        raise ValueError(
            "missing required meta fields (need id, date_time, home_team, away_team)"
        )
    return MatchInfo(match_id=str(match_id), date=local_match_date(timestamp), home=home, away=away)


def load_meta(path: Path) -> dict:
    """Load a meta JSON file into a dict."""
    with path.open(encoding="utf-8") as f:
        return json.load(f)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest src/tests/test_skillcorner_bundle_format.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/formats/skillcorner_bundle.py src/tests/test_skillcorner_bundle_format.py
git commit -m "feat(formats): SkillCorner bundle reader — date + meta parsing"
```

---

## Task 2: Reader — match discovery + artifact completeness

**Files:**
- Modify: `src/formats/skillcorner_bundle.py`
- Test: `src/tests/test_skillcorner_bundle_format.py`

- [ ] **Step 1: Write the failing test**

Append to `src/tests/test_skillcorner_bundle_format.py`:

```python
from formats.skillcorner_bundle import (
    discover_matches,
    is_complete,
    missing_artifacts,
    source_files,
)


def _make_bundle(root: Path, match_id: str, *, drop: set[str] = frozenset()) -> None:
    """Create a synthetic bundle tree with the 5 artifact subdirs for one match.

    `drop` names role keys whose source file should be omitted (to test completeness).
    """
    role_to_relpath = {
        "tracking": ("tracking", ".json"),
        "events": ("dynamic", ".parquet"),
        "freeze_frames": ("freeze", ".parquet"),
        "metadata": ("meta", ".json"),
        "physical": ("physical", ".parquet"),
    }
    for role, (subdir, ext) in role_to_relpath.items():
        d = root / subdir
        d.mkdir(parents=True, exist_ok=True)
        if role not in drop:
            (d / f"{match_id}{ext}").write_text("x", encoding="utf-8")


class TestDiscovery:
    def test_discovers_match_ids_from_meta_dir(self, tmp_path: Path) -> None:
        _make_bundle(tmp_path, "1000002")
        _make_bundle(tmp_path, "1000001")
        assert discover_matches(tmp_path) == ["1000001", "1000002"]  # sorted

    def test_missing_meta_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="meta"):
            discover_matches(tmp_path)


class TestCompleteness:
    def test_complete_match(self, tmp_path: Path) -> None:
        _make_bundle(tmp_path, "1000001")
        assert missing_artifacts(tmp_path, "1000001") == []
        assert is_complete(tmp_path, "1000001") is True

    def test_incomplete_match_reports_missing_roles(self, tmp_path: Path) -> None:
        _make_bundle(tmp_path, "1000001", drop={"tracking", "physical"})
        assert sorted(missing_artifacts(tmp_path, "1000001")) == ["physical", "tracking"]
        assert is_complete(tmp_path, "1000001") is False

    def test_source_files_maps_every_role(self, tmp_path: Path) -> None:
        _make_bundle(tmp_path, "1000001")
        files = source_files(tmp_path, "1000001")
        assert set(files) == {"tracking", "events", "freeze_frames", "metadata", "physical"}
        assert files["tracking"].name == "1000001.json"
        assert files["events"].name == "1000001.parquet"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest src/tests/test_skillcorner_bundle_format.py -k "Discovery or Completeness" -v`
Expected: FAIL with `ImportError: cannot import name 'discover_matches'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/formats/skillcorner_bundle.py`:

```python
def discover_matches(root: Path) -> list[str]:
    """Return sorted match ids (the stems of meta/*.json) under the bundle root."""
    meta_dir = root / "meta"
    if not meta_dir.is_dir():
        raise FileNotFoundError(f"no meta/ directory under {root}")
    return sorted(p.stem for p in meta_dir.glob("*.json"))


def source_files(root: Path, match_id: str) -> dict[str, Path]:
    """Map each role key to its expected source-file Path (existence not checked)."""
    return {
        role: root / subdir / f"{match_id}{ext}"
        for role, subdir, ext, _staged in ARTIFACT_SPECS
    }


def missing_artifacts(root: Path, match_id: str) -> list[str]:
    """Return the role keys whose source file is absent for this match."""
    return [role for role, path in source_files(root, match_id).items() if not path.is_file()]


def is_complete(root: Path, match_id: str) -> bool:
    """True if all 5 ingested artifacts are present for this match."""
    return not missing_artifacts(root, match_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest src/tests/test_skillcorner_bundle_format.py -v`
Expected: PASS (all reader tests so far).

- [ ] **Step 5: Commit**

```bash
git add src/formats/skillcorner_bundle.py src/tests/test_skillcorner_bundle_format.py
git commit -m "feat(formats): SkillCorner bundle discovery + completeness"
```

---

## Task 3: Reader — player derivation from meta.players

**Files:**
- Modify: `src/formats/skillcorner_bundle.py`
- Test: `src/tests/test_skillcorner_bundle_format.py`

- [ ] **Step 1: Write the failing test**

Append to `src/tests/test_skillcorner_bundle_format.py`:

```python
import sys

# PlayerRecord lives in canonical/; ensure src/ is importable (conftest already adds it via pytest rootdir).
from canonical.models import PlayerRecord

from formats.skillcorner_bundle import players_from_meta


def _meta_with_players(players: list[dict]) -> dict:
    return {
        "id": 1,
        "date_time": "2023-08-12T19:30:00Z",
        "home_team": {"short_name": "H"},
        "away_team": {"short_name": "A"},
        "players": players,
    }


class TestPlayersFromMeta:
    def test_maps_fields(self) -> None:
        meta = _meta_with_players([
            {
                "id": 688,
                "first_name": "Test",
                "last_name": "Player",
                "short_name": "T. Player",
                "birthday": "1989-08-14",
                "player_role": {"id": 5, "position_group": "Midfield", "name": "LDM", "acronym": "LDM"},
            }
        ])
        records = players_from_meta(meta)
        assert len(records) == 1
        r = records[0]
        assert r == {
            "id": "688",
            "firstName": "Test",
            "lastName": "Player",
            "nickname": "T. Player",
            "dob": "1989-08-14",
            "position": "LDM",
            "positionGroupType": "Midfield",
        }
        # Must validate against the canonical model.
        PlayerRecord.model_validate({**r, "visibility": "private", "updated_at": "2026-01-01T00:00:00Z"})

    def test_blank_name_falls_back_to_nickname_only(self) -> None:
        meta = _meta_with_players([
            {"id": 1, "first_name": "", "last_name": "", "short_name": "Solo", "birthday": "2000-01-01"}
        ])
        r = players_from_meta(meta)[0]
        assert r["firstName"] is None and r["lastName"] is None and r["nickname"] == "Solo"
        # nickname-only still satisfies the PlayerRecord validator.
        PlayerRecord.model_validate({**r, "visibility": "private", "updated_at": "2026-01-01T00:00:00Z"})

    def test_skips_entries_without_id(self) -> None:
        meta = _meta_with_players([{"first_name": "No", "last_name": "Id", "short_name": "NI"}])
        assert players_from_meta(meta) == []

    def test_no_players_key_returns_empty(self) -> None:
        assert players_from_meta({"id": 1}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest src/tests/test_skillcorner_bundle_format.py -k PlayersFromMeta -v`
Expected: FAIL with `ImportError: cannot import name 'players_from_meta'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/formats/skillcorner_bundle.py`:

```python
def players_from_meta(meta: dict) -> list[dict]:
    """Derive canonical PlayerRecord dicts (without visibility/updated_at) from meta.players.

    meta.players is the self-contained matchday squad list; each entry already
    carries names, birthday, and player_role (position). Empty strings are
    normalised to None so the PlayerRecord ``nickname OR (firstName AND lastName)``
    validator behaves predictably.
    """
    records: list[dict] = []
    for p in meta.get("players", []):
        pid = p.get("id")
        if pid is None:
            continue
        role = p.get("player_role") or {}
        records.append(
            {
                "id": str(pid),
                "firstName": p.get("first_name") or None,
                "lastName": p.get("last_name") or None,
                "nickname": p.get("short_name") or None,
                "dob": p.get("birthday") or None,
                "position": role.get("name") or None,
                "positionGroupType": role.get("position_group") or None,
            }
        )
    return records
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest src/tests/test_skillcorner_bundle_format.py -v`
Expected: PASS (all reader tests).

- [ ] **Step 5: Commit**

```bash
git add src/formats/skillcorner_bundle.py src/tests/test_skillcorner_bundle_format.py
git commit -m "feat(formats): derive owner-tier player records from meta.players"
```

---

## Task 4: Synthetic meta fixture + file-based parse test

**Files:**
- Create: `src/tests/fixtures/skillcorner_bundle_meta_synthetic.json`
- Modify: `src/tests/test_skillcorner_bundle_format.py`

- [ ] **Step 1: Write the failing test**

Append to `src/tests/test_skillcorner_bundle_format.py`:

```python
from formats.skillcorner_bundle import load_meta

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestLoadMetaFixture:
    def test_load_and_parse_synthetic_fixture(self) -> None:
        meta = load_meta(FIXTURES_DIR / "skillcorner_bundle_meta_synthetic.json")
        info = match_info(meta)
        assert info.match_id == "9000001"
        assert info.date == "2099-08-15"
        assert info.home == "Synthetic Home FC"
        assert info.away == "Synthetic Away CF"
        players = players_from_meta(meta)
        assert {p["id"] for p in players} == {"7000001", "7000002"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest src/tests/test_skillcorner_bundle_format.py -k LoadMetaFixture -v`
Expected: FAIL with `FileNotFoundError` (fixture missing).

- [ ] **Step 3: Create the fixture (synthetic — no real ids/names)**

Create `src/tests/fixtures/skillcorner_bundle_meta_synthetic.json`:

```json
{
  "id": 9000001,
  "home_team_score": 0,
  "away_team_score": 2,
  "date_time": "2099-08-15T19:30:00Z",
  "home_team": {"id": 1, "name": "Synthetic Home Town FC", "short_name": "Synthetic Home FC"},
  "away_team": {"id": 2, "name": "Synthetic Away City CF", "short_name": "Synthetic Away CF"},
  "pitch_length": 105.0,
  "pitch_width": 68.0,
  "players": [
    {
      "id": 7000001,
      "number": 9,
      "team_id": 1,
      "first_name": "Synthetic",
      "last_name": "Striker",
      "short_name": "S. Striker",
      "birthday": "2000-01-01",
      "gender": "male",
      "player_role": {"id": 1, "position_group": "Forward", "name": "CF", "acronym": "CF"}
    },
    {
      "id": 7000002,
      "number": 1,
      "team_id": 2,
      "first_name": "",
      "last_name": "",
      "short_name": "Keeper",
      "birthday": "1995-05-05",
      "gender": "male",
      "player_role": {"id": 2, "position_group": "Goalkeeper", "name": "GK", "acronym": "GK"}
    }
  ]
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest src/tests/test_skillcorner_bundle_format.py -v`
Expected: PASS (all reader tests incl. fixture).

- [ ] **Step 5: Commit**

```bash
git add src/tests/fixtures/skillcorner_bundle_meta_synthetic.json src/tests/test_skillcorner_bundle_format.py
git commit -m "test(formats): synthetic meta fixture + file-based parse test"
```

---

## Task 5: Adapter — staging + streamed gzip

**Files:**
- Create: `scripts/upload_skillcorner_realmadrid.py`
- Test: `src/tests/test_upload_skillcorner_realmadrid.py`

- [ ] **Step 1: Write the failing test**

Create `src/tests/test_upload_skillcorner_realmadrid.py`:

```python
"""Tests for scripts/upload_skillcorner_realmadrid.py (no real S3, no real network)."""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

import pytest

# Lambda shared dir parity with other upload tests (harmless if unused here).
LAMBDA_SRC = Path(__file__).parent.parent.parent / "terraform" / "modules" / "functions" / "src"
sys.path.insert(0, str(LAMBDA_SRC))


def _make_bundle(root: Path, match_id: str) -> None:
    specs = {
        "tracking": ("tracking", ".json", '{"frames": []}'),
        "events": ("dynamic", ".parquet", "EVENTS-PARQUET-BYTES"),
        "freeze_frames": ("freeze", ".parquet", "FREEZE-PARQUET-BYTES"),
        "metadata": ("meta", ".json", "{}"),
        "physical": ("physical", ".parquet", "PHYSICAL-PARQUET-BYTES"),
    }
    for _role, (subdir, ext, body) in specs.items():
        d = root / subdir
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{match_id}{ext}").write_text(body, encoding="utf-8")


class TestStageMatch:
    def test_stages_five_artifacts_with_role_names_and_gzips_tracking(
        self, tmp_path: Path, load_script
    ) -> None:
        mod = load_script("upload_skillcorner_realmadrid")
        root = tmp_path / "src"
        _make_bundle(root, "1000001")
        staging = tmp_path / "stage"
        staging.mkdir()

        mod.stage_match(root, "1000001", staging)

        names = sorted(p.name for p in staging.iterdir())
        assert names == [
            "events.parquet",
            "freeze_frames.parquet",
            "metadata.json",
            "physical.parquet",
            "tracking.json.gz",
        ]
        # velocities is NOT staged
        assert not (staging / "velocities.parquet").exists()
        # tracking is real gzip and round-trips to the original bytes
        with gzip.open(staging / "tracking.json.gz", "rt", encoding="utf-8") as f:
            assert f.read() == '{"frames": []}'
        # parquet artifacts copied verbatim
        assert (staging / "events.parquet").read_text(encoding="utf-8") == "EVENTS-PARQUET-BYTES"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest src/tests/test_upload_skillcorner_realmadrid.py -v`
Expected: FAIL — `scripts/upload_skillcorner_realmadrid.py` does not exist (load_script raises).

- [ ] **Step 3: Write minimal implementation**

Create `scripts/upload_skillcorner_realmadrid.py`:

```python
"""Upload restricted SkillCorner Real Madrid data to the mock provider API (OWNER tier).

Owner-tier (visibility=private) ingest of restricted SkillCorner tracking data
(Soccermatics-course distribution). NOT redistributable — served only to the owner
bearer token. See
docs/superpowers/specs/2026-06-29-skillcorner-restricted-realmadrid-owner-tier-design.md.

Source root is read from $SKILLCORNER_RESTRICTED_DIR (an operator-local path that is
never committed). Reuses the existing `skillcorner` provider at the private tier.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Make src/ importable when run directly from a checkout.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

import boto3  # noqa: E402

from formats.skillcorner_bundle import (  # noqa: E402
    ARTIFACT_SPECS,
    discover_matches,
    load_meta,
    match_info,
    missing_artifacts,
    players_from_meta,
)
from mock_api.upload import upload_game  # noqa: E402
from mock_api.upload_players import upload_players  # noqa: E402

PROVIDER = "skillcorner"
SOURCE_NAME = "SkillCorner"
SOURCE_LICENCE = "Restricted; redistribution not permitted"


def _gzip_file(src: Path, dest: Path) -> None:
    """Stream-gzip src -> dest in 1 MiB chunks (never loads the body into memory)."""
    with src.open("rb") as f_in, gzip.open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out, length=1 << 20)


def stage_match(root: Path, match_id: str, staging: Path) -> None:
    """Copy/gzip the 5 ingested artifacts for one match into `staging` under role-aligned names."""
    for _role, subdir, ext, staged in ARTIFACT_SPECS:
        src = root / subdir / f"{match_id}{ext}"
        dest = staging / staged
        if staged.endswith(".gz"):
            _gzip_file(src, dest)
        else:
            shutil.copyfile(src, dest)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest src/tests/test_upload_skillcorner_realmadrid.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/upload_skillcorner_realmadrid.py src/tests/test_upload_skillcorner_realmadrid.py
git commit -m "feat(scripts): SkillCorner RM adapter — staging + streamed gzip"
```

---

## Task 6: Adapter — player derivation, dedup, and collision skip

**Files:**
- Modify: `scripts/upload_skillcorner_realmadrid.py`
- Test: `src/tests/test_upload_skillcorner_realmadrid.py`

- [ ] **Step 1: Write the failing test**

Append to `src/tests/test_upload_skillcorner_realmadrid.py`:

```python
class TestDerivePlayers:
    def test_dedup_across_matches_and_skip_public_ids(self, load_script) -> None:
        mod = load_script("upload_skillcorner_realmadrid")
        meta_a = {
            "players": [
                {"id": 1, "short_name": "One", "first_name": "P", "last_name": "One"},
                {"id": 2, "short_name": "Two", "first_name": "P", "last_name": "Two"},
            ]
        }
        meta_b = {
            "players": [
                {"id": 2, "short_name": "Two", "first_name": "P", "last_name": "Two"},  # dup across matches
                {"id": 3, "short_name": "Three", "first_name": "P", "last_name": "Three"},
            ]
        }
        kept, skipped = mod.derive_players([meta_a, meta_b], skip_ids={"3"})

        assert [r["id"] for r in kept] == ["1", "2"]  # deduped, sorted, id 3 skipped
        assert skipped == ["3"]

    def test_no_skip_ids_keeps_all(self, load_script) -> None:
        mod = load_script("upload_skillcorner_realmadrid")
        meta = {"players": [{"id": 5, "short_name": "Five"}]}
        kept, skipped = mod.derive_players([meta], skip_ids=set())
        assert [r["id"] for r in kept] == ["5"]
        assert skipped == []


class TestPublicPlayerIds:
    def test_reads_public_index_ids(self, load_script) -> None:
        import json
        from unittest.mock import MagicMock

        mod = load_script("upload_skillcorner_realmadrid")
        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        body = {"players": [{"id": "1"}, {"id": "2"}]}
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(body).encode())}

        ids = mod.public_player_ids(s3, "bucket")
        assert ids == {"1", "2"}
        s3.get_object.assert_called_once_with(Bucket="bucket", Key="skillcorner/players.json")

    def test_missing_public_index_returns_empty(self, load_script) -> None:
        from unittest.mock import MagicMock

        mod = load_script("upload_skillcorner_realmadrid")
        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        s3.get_object.side_effect = s3.exceptions.NoSuchKey()
        assert mod.public_player_ids(s3, "bucket") == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest src/tests/test_upload_skillcorner_realmadrid.py -k "DerivePlayers or PublicPlayerIds" -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'derive_players'`.

- [ ] **Step 3: Write minimal implementation**

Append to `scripts/upload_skillcorner_realmadrid.py`:

```python
def public_player_ids(s3, bucket: str) -> set[str]:
    """Return the player ids already present in the PUBLIC skillcorner players.json.

    These are the ids that would make `upload_players` raise (cross-tier guard:
    when uploading private, the "other tier" is the public index). Private-tier
    ids are NOT collected — re-uploading them updates in place, which is fine.
    """
    key = f"{PROVIDER}/players.json"
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except s3.exceptions.NoSuchKey:
        return set()
    data = json.loads(obj["Body"].read().decode("utf-8"))
    return {p.get("id") for p in data.get("players", []) if p.get("id") is not None}


def derive_players(metas: list[dict], skip_ids: set[str]) -> tuple[list[dict], list[str]]:
    """Derive deduped owner-tier player records from many meta dicts.

    Returns (kept, skipped_ids). Ids in `skip_ids` (already public) are dropped and
    reported rather than passed to upload_players, which would abort on the first
    cross-tier collision. The colliding player is already public; only the tracking
    is restricted, and the restricted matches still reference them by id.
    """
    by_id: dict[str, dict] = {}
    for meta in metas:
        for record in players_from_meta(meta):
            by_id[record["id"]] = record  # dedup by id; entries for the same id are identical
    kept = sorted((r for r in by_id.values() if r["id"] not in skip_ids), key=lambda r: r["id"])
    skipped = sorted(pid for pid in by_id if pid in skip_ids)
    return kept, skipped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest src/tests/test_upload_skillcorner_realmadrid.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/upload_skillcorner_realmadrid.py src/tests/test_upload_skillcorner_realmadrid.py
git commit -m "feat(scripts): player derivation with cross-tier collision skip"
```

---

## Task 7: Adapter — `upload_all` orchestration + `main`

**Files:**
- Modify: `scripts/upload_skillcorner_realmadrid.py`
- Test: `src/tests/test_upload_skillcorner_realmadrid.py`

- [ ] **Step 1: Write the failing test**

Append to `src/tests/test_upload_skillcorner_realmadrid.py`:

```python
class TestUploadAll:
    def test_orchestrates_uploads_with_expected_kwargs(self, tmp_path: Path, load_script, monkeypatch) -> None:
        import json
        from unittest.mock import MagicMock

        mod = load_script("upload_skillcorner_realmadrid")

        # One complete match + one incomplete (missing physical) that must be skipped.
        root = tmp_path
        _make_bundle(root, "1000001")
        _make_bundle(root, "1000002")
        (root / "physical" / "1000002.parquet").unlink()  # make 1000002 incomplete
        # Give 1000001 a real parseable meta with one player.
        (root / "meta" / "1000001.json").write_text(
            json.dumps(
                {
                    "id": 1000001,
                    "date_time": "2023-08-12T19:30:00Z",
                    "home_team": {"short_name": "Home FC"},
                    "away_team": {"short_name": "Away CF"},
                    "players": [{"id": 4242, "short_name": "Star", "first_name": "A", "last_name": "B",
                                 "player_role": {"name": "CF", "position_group": "Forward"}}],
                }
            ),
            encoding="utf-8",
        )

        # Stub S3 (only used for public_player_ids) + capture the two upload calls.
        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        s3.get_object.side_effect = s3.exceptions.NoSuchKey()  # no existing public players
        monkeypatch.setattr(mod.boto3, "client", lambda _name: s3)

        game_calls: list[dict] = []
        players_calls: list[dict] = []
        monkeypatch.setattr(mod, "upload_game", lambda **kw: game_calls.append(kw) or ["tracking"])
        monkeypatch.setattr(mod, "upload_players", lambda **kw: players_calls.append(kw) or 1)

        uploaded, n_players, n_skipped = mod.upload_all(root, "test-bucket")

        # Only the complete match uploaded.
        assert uploaded == 1
        assert len(game_calls) == 1
        g = game_calls[0]
        assert g["provider"] == "skillcorner"
        assert g["game_id"] == "1000001"
        assert g["visibility"] == "private"
        assert g["provenance"] == "original"
        assert g["date"] == "2023-08-12"
        assert g["home"] == "Home FC" and g["away"] == "Away CF"
        assert g["source_name"] == "SkillCorner"
        assert g["source_licence"] == "Restricted; redistribution not permitted"

        # Players derived from the complete match and uploaded private from a JSON file.
        assert n_players == 1 and n_skipped == 0
        assert len(players_calls) == 1
        p = players_calls[0]
        assert p["provider"] == "skillcorner" and p["visibility"] == "private"
        assert Path(p["input_file"]).suffix == ".json"

    def test_limit_caps_matches(self, tmp_path: Path, load_script, monkeypatch) -> None:
        from unittest.mock import MagicMock

        mod = load_script("upload_skillcorner_realmadrid")
        for mid in ("1000001", "1000002", "1000003"):
            _make_bundle(tmp_path, mid)

        s3 = MagicMock()
        s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        s3.get_object.side_effect = s3.exceptions.NoSuchKey()
        monkeypatch.setattr(mod.boto3, "client", lambda _name: s3)
        monkeypatch.setattr(mod, "upload_game", lambda **kw: ["tracking"])
        monkeypatch.setattr(mod, "upload_players", lambda **kw: 0)

        uploaded, _n_players, _n_skipped = mod.upload_all(tmp_path, "test-bucket", limit=2)
        assert uploaded == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest src/tests/test_upload_skillcorner_realmadrid.py -k UploadAll -v`
Expected: FAIL with `AttributeError: ... has no attribute 'upload_all'`.

- [ ] **Step 3: Write minimal implementation**

Append to `scripts/upload_skillcorner_realmadrid.py`:

```python
def upload_all(root: Path, bucket: str, limit: int | None = None) -> tuple[int, int, int]:
    """Stage + upload all complete matches, then derive + upload the owner-tier players.

    Returns (matches_uploaded, players_uploaded, players_skipped).
    """
    match_ids = discover_matches(root)
    if limit:
        match_ids = match_ids[:limit]
    print(f"Found {len(match_ids)} match(es) under {root}")

    s3 = boto3.client("s3")
    metas: list[dict] = []
    uploaded = 0

    for match_id in match_ids:
        missing = missing_artifacts(root, match_id)
        if missing:
            print(f"WARN: match {match_id} missing {missing} — skipping")
            continue
        meta = load_meta(root / "meta" / f"{match_id}.json")
        try:
            info = match_info(meta)
        except ValueError as e:
            print(f"WARN: match {match_id} meta unparseable ({e}) — skipping")
            continue
        metas.append(meta)
        with tempfile.TemporaryDirectory(prefix=f"sc-rm-{match_id}-") as tmp:
            staging = Path(tmp)
            stage_match(root, match_id, staging)
            upload_game(
                game_dir=staging,
                provider=PROVIDER,
                game_id=info.match_id,
                bucket=bucket,
                visibility="private",
                provenance="original",
                date=info.date,
                home=info.home,
                away=info.away,
                source_name=SOURCE_NAME,
                source_licence=SOURCE_LICENCE,
            )
        uploaded += 1

    # Owner-tier player catalogue (skip ids already public — §6.1).
    skip_ids = public_player_ids(s3, bucket)
    players, skipped = derive_players(metas, skip_ids)
    if skipped:
        print(f"NOTE: {len(skipped)} player id(s) already in the public tier — skipped: {skipped}")
    if players:
        with tempfile.TemporaryDirectory(prefix="sc-rm-players-") as tmp:
            players_file = Path(tmp) / "players.json"
            players_file.write_text(json.dumps({"players": players}, indent=2), encoding="utf-8")
            upload_players(
                input_file=players_file,
                provider=PROVIDER,
                bucket=bucket,
                visibility="private",
                source_name=SOURCE_NAME,
                source_licence=SOURCE_LICENCE,
            )

    return uploaded, len(players), len(skipped)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload restricted SkillCorner Real Madrid data to the mock provider API (owner tier)"
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("PINING_BUCKET"),
        help="S3 bucket name (default: $PINING_BUCKET env var)",
    )
    parser.add_argument(
        "--source-dir",
        default=os.environ.get("SKILLCORNER_RESTRICTED_DIR"),
        help="Bundle root containing meta/ tracking/ dynamic/ freeze/ physical/ "
        "(default: $SKILLCORNER_RESTRICTED_DIR env var)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Upload only the first N matches (smoke test)")
    args = parser.parse_args()

    if not args.bucket:
        parser.error("--bucket is required (or set PINING_BUCKET)")
    if not args.source_dir:
        parser.error("--source-dir is required (or set SKILLCORNER_RESTRICTED_DIR)")

    root = Path(args.source_dir)
    if not (root / "meta").is_dir():
        parser.error(f"no meta/ directory under {root} — is this a SkillCorner bundle root?")

    print(f"Uploading restricted SkillCorner RM data to s3://{args.bucket}/{PROVIDER}/ (OWNER tier)")
    uploaded, n_players, n_skipped = upload_all(root, args.bucket, limit=args.limit)
    print(f"Done — {uploaded} match(es), {n_players} player(s) uploaded, {n_skipped} player(s) skipped.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest src/tests/test_upload_skillcorner_realmadrid.py -v`
Expected: PASS (all adapter tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/upload_skillcorner_realmadrid.py src/tests/test_upload_skillcorner_realmadrid.py
git commit -m "feat(scripts): upload_all orchestration + CLI for SkillCorner RM owner tier"
```

---

## Task 8: Post-load verifier (owner + public token)

**Files:**
- Create: `scripts/verify_skillcorner_realmadrid_load.py`

No unit test (it hits the live API). It is exercised manually in Task 11.

- [ ] **Step 1: Write the verifier**

Create `scripts/verify_skillcorner_realmadrid_load.py`:

```python
"""Post-load verification for the restricted SkillCorner Real Madrid owner-tier dataset.

Asserts (sampling ids from the live OWNER response — no licensed ids hardcoded):
  - owner /skillcorner/matches contains private (restricted) entries
  - those private ids are ABSENT from the public /skillcorner/matches list
  - owner can fetch a restricted match's artifacts (tracking via a Range GET to
    avoid downloading the full body); public gets 404 on the same restricted id
  - owner /skillcorner/players is non-empty (derived catalogue present)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

# tracking is the large artifact (validate via Range GET, no full download).
LARGE_ARTIFACTS = {"tracking"}


def parse_content_range_total(header: str) -> int:
    m = re.search(r"/(\d+)\s*$", header or "")
    return int(m.group(1)) if m else -1


def _get_json(api: str, path: str, token: str) -> dict:
    req = urllib.request.Request(f"{api}{path}", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


class _NoFollow(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def _status_or_presigned(api: str, path: str, token: str) -> tuple[int, str | None]:
    """Return (status, location). For 302 returns the presigned URL; otherwise location is None."""
    req = urllib.request.Request(f"{api}{path}", headers={"Authorization": f"Bearer {token}"})
    opener = urllib.request.build_opener(_NoFollow)
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.status, None
    except urllib.error.HTTPError as e:
        if e.code == 302:
            return 302, e.headers.get("Location")
        return e.code, None


def _artifact_ok_owner(location: str, large: bool) -> tuple[int, int]:
    if large:
        s3_req = urllib.request.Request(location, headers={"Range": "bytes=0-0"})
        with urllib.request.urlopen(s3_req, timeout=60) as resp:
            return resp.status, parse_content_range_total(resp.headers.get("Content-Range", ""))
    with urllib.request.urlopen(urllib.request.Request(location), timeout=60) as resp:
        return resp.status, len(resp.read())


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the restricted SkillCorner RM owner-tier load")
    parser.add_argument("--api", required=True, help="API base URL (no trailing slash)")
    parser.add_argument("--owner-token", required=True)
    parser.add_argument("--public-token", required=True)
    args = parser.parse_args()

    failures: list[str] = []

    owner_matches = _get_json(args.api, "/skillcorner/matches", args.owner_token).get("matches", [])
    public_matches = _get_json(args.api, "/skillcorner/matches", args.public_token).get("matches", [])
    public_ids = {m["id"] for m in public_matches}

    restricted = [m for m in owner_matches if m.get("visibility") == "private"]
    if not restricted:
        failures.append("owner /skillcorner/matches: no private (restricted) entries found")
    else:
        print(f"OK: owner sees {len(restricted)} restricted match(es)")

    leaked = [m["id"] for m in restricted if m["id"] in public_ids]
    if leaked:
        failures.append(f"restricted ids visible to public token: {leaked[:5]}")
    elif restricted:
        print("OK: restricted ids absent from public match list")

    if restricted:
        sample = restricted[0]
        mid = sample["id"]
        for artifact in sample.get("artifacts", {}):
            # Owner must get the artifact (200/206); public must get 404.
            o_status, location = _status_or_presigned(args.api, f"/skillcorner/matches/{mid}/{artifact}", args.owner_token)
            if o_status == 302 and location:
                a_status, total = _artifact_ok_owner(location, artifact in LARGE_ARTIFACTS)
                if a_status in (200, 206) and total > 0:
                    print(f"OK: owner {artifact} -> {a_status}, {total}B")
                else:
                    failures.append(f"owner {mid}/{artifact}: status={a_status}, total={total}")
            else:
                failures.append(f"owner {mid}/{artifact}: expected 302, got {o_status}")

            p_status, _ = _status_or_presigned(args.api, f"/skillcorner/matches/{mid}/{artifact}", args.public_token)
            if p_status == 404:
                print(f"OK: public {artifact} -> 404 (no existence leak)")
            else:
                failures.append(f"public {mid}/{artifact}: expected 404, got {p_status}")

    owner_players = _get_json(args.api, "/skillcorner/players", args.owner_token).get("players", [])
    if owner_players:
        print(f"OK: owner /skillcorner/players = {len(owner_players)}")
    else:
        failures.append("owner /skillcorner/players: empty (derived catalogue missing)")

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

- [ ] **Step 2: Smoke-check it parses and shows help**

Run: `uv run python scripts/verify_skillcorner_realmadrid_load.py --help`
Expected: argparse help text (no import errors).

- [ ] **Step 3: Commit**

```bash
git add scripts/verify_skillcorner_realmadrid_load.py
git commit -m "feat(scripts): owner+public post-load verifier for SkillCorner RM"
```

---

## Task 9: ruff per-file-ignores for the new scripts

**Files:**
- Modify: `pyproject.toml`

The verifier uses `urllib.request` to a configurable HTTPS endpoint (ruff S310 false-positive), matching `verify_idsse_load.py`. The adapter has no network/crypto calls and needs no ignore.

- [ ] **Step 1: Add the ignore**

In `pyproject.toml`, under `[tool.ruff.lint.per-file-ignores]`, after the existing `"scripts/verify_idsse_load.py" = ["S310"]` line, add:

```toml
# Verifier: urllib.request to a configurable HTTPS API endpoint is the entire point (S310 false-positive).
"scripts/verify_skillcorner_realmadrid_load.py" = ["S310"]
```

- [ ] **Step 2: Verify ruff is clean on the new files**

Run: `uv run ruff check scripts/upload_skillcorner_realmadrid.py scripts/verify_skillcorner_realmadrid_load.py src/formats/skillcorner_bundle.py`
Expected: `All checks passed!`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "build: ruff per-file-ignore for SkillCorner RM verifier"
```

---

## Task 10: ADR 0009 + docs

**Files:**
- Create: `docs/decisions/0009-restricted-tier-under-existing-public-provider.md`
- Modify: `docs/decisions/README.md`
- Modify: `README.md`

- [ ] **Step 1: Write ADR 0009**

Create `docs/decisions/0009-restricted-tier-under-existing-public-provider.md`:

```markdown
# 0009 — Restricted Data Under an Existing Public Provider

## Status

Accepted

## Date

2026-06-29

## Context

We obtained a restricted SkillCorner tracking dataset (~99 Real Madrid matches,
2023/24 season; Soccermatics-course distribution) that we may use but may not
redistribute. SkillCorner already exists as a **public** provider serving
redistributed open data (A-League). The new data is from the same data provider
but cannot be public.

The system was designed for one provider with two visibility tiers (public +
`_private/` prefix, owner-token gating) — see ADR 0002 / 0005. The question was
whether to reuse the `skillcorner` slug at the private tier or mint a new slug.

## Decision

The restricted data is ingested under the **existing `skillcorner` provider** at
`visibility="private"`, `provenance="original"`. No new provider slug. Owner-token
consumers see public + restricted merged under `/skillcorner/...`; public-token
consumers see only the open data (existing handler gating, uniform 404 on private).

Supporting choices:

1. **Artifact keys are role-aligned** (ADR 0008): `tracking`, `events`,
   `freeze_frames`, `metadata`, `physical`. `freeze_frames` and `physical` are
   **added to the shared role vocabulary** (not skillcorner-local) so future
   providers reuse them.
2. **`velocities` is excluded** — it is derived from `tracking` by a preprocessing
   script and is fully reproducible.
3. **The index is built from each match's `meta/*.json`, not `matches.parquet`** —
   the bundle's `matches.parquet` lists a different season's fixtures whose ids do
   not match the per-match artifact filenames. `meta` is self-contained and
   authoritative (it also sources the owner-tier player catalogue).
4. **Cross-tier player-id collisions are handled by skip-and-report** in the
   adapter: SkillCorner ids are global and shared with the public tier, so a
   participant may already be public. The adapter drops such ids (the player is
   already public; only the tracking is restricted) and uploads the remainder;
   `upload_players`' raise-on-collision remains an untouched backstop.

## Consequences

**Positive:** No new provider, no Lambda/Terraform change; consumers keep one
SkillCorner contract; uses the tier machinery as designed.

**Negative / accepted:** Two artifact-key conventions and three wire conventions
now coexist within `skillcorner` — legacy public id-prefixed keys with bz2/JSONL
vs. role-aligned private keys with gzip JSON. Consumers need a per-tier/per-match
artifact map. We do **not** retro-rename the public keys (Hyrum's Law — existing
consumers depend on them).

**Reversal cost:** Moderate. Re-tiering or re-slugging means re-uploading under a
new prefix and rebuilding indexes; the no-tier-mixing guard blocks accidental
in-place flips.

## Alternatives Considered

- **New owner-only slug (e.g. `skillcorner-restricted`).** Rejected: fragments one
  data provider into two providers and duplicates attribution; the tier dimension
  already expresses "restricted" without a new noun.
- **De-identify and publish.** Rejected: licence forbids redistribution; owner-tier
  gating is the correct control (consistent with Gradient Sports). The
  de-identification engine stays reserved for a future redistribution case.

## See Also

- Spec: `docs/superpowers/specs/2026-06-29-skillcorner-restricted-realmadrid-owner-tier-design.md`
- ADR 0002 (private-prefix tier separation), ADR 0005 (single-bucket multi-tier),
  ADR 0008 (role-aligned artifact-key vocabulary)
- Implementation: `src/formats/skillcorner_bundle.py`,
  `scripts/upload_skillcorner_realmadrid.py`,
  `scripts/verify_skillcorner_realmadrid_load.py`
```

- [ ] **Step 2: Add the index row**

In `docs/decisions/README.md`, add this row to the table immediately after the `0008` row:

```markdown
| [0009](0009-restricted-tier-under-existing-public-provider.md) | Restricted Data Under an Existing Public Provider | Accepted | SkillCorner RM owner-tier ingest (spec §13) |
```

- [ ] **Step 3: Update README provider notes**

In `README.md`, find the section that lists/describes providers (search for `idsse` or "provider"). Add a sentence noting the owner tier — example wording to place alongside the SkillCorner provider description:

```markdown
> The `skillcorner` provider also carries **owner-tier** (restricted) matches that
> are served only to the owner bearer token; public consumers see only the
> redistributed open data. See ADR 0009.
```

(If the README has a providers table, add an "owner tier: restricted SkillCorner matches" note to the SkillCorner row instead — match the existing format. Do not name licensed match ids or player names.)

- [ ] **Step 4: Commit**

```bash
git add docs/decisions/0009-restricted-tier-under-existing-public-provider.md docs/decisions/README.md README.md
git commit -m "docs: ADR 0009 + provider notes for SkillCorner owner tier"
```

---

## Task 11: Quality gate, final-review, and dry run

**Files:** none (verification only)

- [ ] **Step 1: Full lint + type + test gate (Shift Left)**

Run each and confirm green:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright src/formats/skillcorner_bundle.py scripts/upload_skillcorner_realmadrid.py scripts/verify_skillcorner_realmadrid_load.py
uv run pytest src/tests/test_skillcorner_bundle_format.py src/tests/test_upload_skillcorner_realmadrid.py -v
```

Expected: ruff "All checks passed!"; ruff format clean; pyright 0 errors; pytest all PASS.

- [ ] **Step 2: Full suite (no regressions)**

Run: `uv run pytest`
Expected: the entire suite passes (existing tests + the new ones).

- [ ] **Step 3: Run the `final-review` skill**

Invoke the `final-review` skill (mandated by CLAUDE.md before the final commit). Resolve any documentation-drift / stale-reference findings it surfaces.

- [ ] **Step 4: Dry-run the adapter against one real match (operator-local)**

This step needs the real bundle and AWS creds; run it as the maintainer (e.g. via `! ...`). It is NOT part of CI.

```bash
export SKILLCORNER_RESTRICTED_DIR=/path/to/bundle/root   # the extracted "Skillcorner data" tree
export PINING_BUCKET=karstenskyt-pining-for-the-data
uv run python scripts/upload_skillcorner_realmadrid.py --limit 1
```

Expected: one match staged + uploaded private; a small player catalogue uploaded private; any already-public ids reported as skipped.

- [ ] **Step 5: Verify the load**

```bash
uv run python scripts/verify_skillcorner_realmadrid_load.py \
  --api https://<api-host>/v1 \
  --owner-token "$OWNER_TOKEN" --public-token "$PUBLIC_TOKEN"
```

Expected: "All post-conditions pass." (owner sees the restricted match + artifacts + players; public gets 404; no id leak).

- [ ] **Step 6: Full ingest (after the dry run looks right)**

```bash
uv run python scripts/upload_skillcorner_realmadrid.py   # all matches
```

Then re-run Step 5's verifier.

---

## Self-Review (completed by plan author)

**Spec coverage:**
- §3.1 artifact selection (5 ingested, velocities/players.parquet/teams.parquet excluded) → Tasks 1–2 (`ARTIFACT_SPECS`), Task 5 (staging excludes velocities, asserted).
- §3.2 role-aligned keys + shared vocab → Task 1 (`ARTIFACT_SPECS` staged names), Task 10 (ADR registers `freeze_frames`/`physical`).
- §3.3 gzip in adapter, streamed → Task 5 (`_gzip_file`, round-trip test).
- §5.2 index from `meta`, not `matches.parquet` → Tasks 1, 7 (never reads `matches.parquet`); ADR Task 10.
- §5.3 game_id = meta id; live id verify via no-tier-mixing backstop → Task 7 (`game_id=info.match_id`).
- §6 / §6.1 players from `meta.players`, dedup, skip-and-report → Tasks 3, 6, 7; tests in 3 & 6.
- §7 Europe/Madrid local date → Task 1 (`local_match_date`, tests incl. day-roll).
- §8 no de-id → nothing to build (assert by absence; provenance="original").
- §9 public-repo hygiene → Task 4 synthetic fixture; Task 7 `$SKILLCORNER_RESTRICTED_DIR`; Task 8 verifier samples ids from live response; no real ids in any committed file.
- §10 error handling (missing dir, missing artifacts, malformed meta, limit) → Task 7 (`main` guards, skip logic, `--limit`).
- §11 tests → Tasks 1–7 unit tests; Task 8 verifier; Task 11 gate.
- §13 ADR 0009 → Task 10.

**Placeholder scan:** none — every step has complete code or an exact command.

**Type consistency:** `ARTIFACT_SPECS` 4-tuple `(role, subdir, ext, staged)` used identically in reader (Tasks 1–2) and adapter (Task 5). `MatchInfo(match_id, date, home, away)` consistent. `upload_all` returns `(int, int, int)` consistent between Task 7 impl and tests. `public_player_ids`/`derive_players`/`stage_match` signatures match their tests. `upload_players(input_file=…)` (a Path) used per the real signature.
