# StatsBomb Open-Data Tournaments 360 — Owner-Tier Second Source Family — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve the six complete-tournament open-data 360 competitions (292 matches) at the owner tier under the existing `statsbomb` slug, in the same faithful StatsBomb feed shape the commercial family already uses.

**Architecture:** A second StatsBomb *source-family* adapter. A new pure reader (`formats/statsbomb_open.py`) reshapes StatsBomb's real open-data feed into the canonical metadata schema and derives players; a new ops script (`scripts/upload_statsbomb_open.py`) fetches the raw JSON from GitHub (cached), validates, and uploads at `visibility="private"`, `provenance="redistributed"`. The commercial family's coherence/roster/upload machinery is reused; `stage_bundle` is extracted to a shared module so both families stage through one path.

**Tech Stack:** Python 3.12+, hatch, ruff (line-length 120), pyright (basic), pytest, boto3, urllib (no `statsbombpy`, no pandas, no new runtime dependency).

**Spec:** `docs/superpowers/specs/2026-09-06-statsbomb-open-tournaments-360-owner-tier-design.md` (revision 2). The plan argues from the spec; executors read both.

**Revision 2 (this plan):** addresses independent plan-review findings — SBO-PLAN-01 (licensed-cache git hygiene: cache defaults outside the tree, `.gitignore` entry in Task 7, explicit `git add` path list in Task 8), SBO-PLAN-02 (dry-run counts are run-relative — noted in Task 8 Step 3), SBO-PLAN-03 (removed the out-of-context commit-message note), SBO-PLAN-04 (§7.2/§7.3 added to the coverage map).

## Global Constraints

- **Python 3.12+**, ruff line-length **120**, pyright **basic**. Run `ruff check`, `ruff format --check`, `pyright`, and `pytest` before declaring any task done (Shift-Left).
- **No new runtime dependency.** Fetch via `urllib.request`; parse with stdlib `json`.
- **Wholly-synthetic fixtures.** Every id/name/date/metric in a test fixture is invented outright — never a renamed real entity. No licensed `(id → entity)` tuple, no real person's attributes, in the repo.
- **Faithful-feed gzip JSON.** Artifacts are `events.json.gz` / `freeze_frames.json.gz` / `roster.json` / `metadata.json`. No Parquet.
- **Owner tier only.** Every upload is `visibility="private"`, `provenance="redistributed"`, `source.name="StatsBomb"`, `source.licence="StatsBomb Public Data User Agreement (open data); redistribution to third parties not permitted"`. Never `public`.
- **British spelling** `licence` is canonical.
- **Commit discipline (OVERRIDES the writing-plans default):** NO per-task commits. Each task ends at "full suite green." A **single** fully-tested commit at the very end (code + tests + docs + ADR + spec + plan), made **only after Karsten's explicit approval of the shown diff**. Do the work on a **feature branch** (never a worktree).
- **Selection ids are public and committed** (the six `(competition_id, season_id)` pairs). Test fixtures still use invented ids.

---

### Task 0: Feature branch

**Files:** none (git setup).

- [ ] **Step 1: Create the feature branch off the default branch**

```bash
git checkout main
git pull --ff-only
git checkout -b feat/statsbomb-open-tournaments-360
```

- [ ] **Step 2: Confirm a clean base**

Run: `git status`
Expected: on `feat/statsbomb-open-tournaments-360`, working tree clean apart from the already-present untracked spec/plan under `docs/superpowers/`.

---

### Task 1: Extract shared staging module (behaviour-preserving refactor)

Resolves spec SBO-SPEC-02. `stage_bundle` currently lives in `scripts/upload_statsbomb_club.py:67` and is bound to the commercial `ARTIFACT_SPECS`. Extract the staging concern into `src/mock_api/staging.py`, parameterized by the artifact specs, so the open family reuses it with its own specs. The commercial script keeps a thin `stage_bundle` wrapper so its existing tests are untouched.

**Files:**
- Create: `src/mock_api/staging.py`
- Modify: `scripts/upload_statsbomb_club.py:50-84` (delete the local gzip+stage bodies; delegate to the shared module)
- Test: `src/tests/test_staging.py`

**Interfaces:**
- Produces: `stage_artifacts(source_root: Path, staging_dir: Path, artifact_specs: tuple[tuple[str, str, str], ...], metadata: dict, metadata_filename: str = "metadata.json") -> None`. Each spec is `(role, source_filename, staged_filename)`; a `staged_filename` ending in `.gz` is stream-gzipped, otherwise copied. `metadata` is written to `staging_dir / metadata_filename` as indented UTF-8 JSON.

- [ ] **Step 1: Write the failing test**

```python
# src/tests/test_staging.py
"""Tests for the shared staging helper (src/mock_api/staging.py)."""
from __future__ import annotations

import gzip
import json

from mock_api.staging import stage_artifacts

SPECS = (
    ("events", "events.json", "events.json.gz"),
    ("roster", "lineups.json", "roster.json"),
)


def test_gzips_gz_specs_copies_plain_and_writes_metadata(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "events.json").write_text(json.dumps([{"id": "e1"}]), encoding="utf-8")
    (src / "lineups.json").write_text(json.dumps([{"team_id": 1}]), encoding="utf-8")
    staging = tmp_path / "stage"
    staging.mkdir()

    stage_artifacts(src, staging, SPECS, {"match_id": 42})

    assert sorted(p.name for p in staging.iterdir()) == ["events.json.gz", "metadata.json", "roster.json"]
    with gzip.open(staging / "events.json.gz", "rt", encoding="utf-8") as f:
        assert json.load(f) == [{"id": "e1"}]
    assert json.loads((staging / "roster.json").read_text(encoding="utf-8")) == [{"team_id": 1}]
    assert json.loads((staging / "metadata.json").read_text(encoding="utf-8")) == {"match_id": 42}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest src/tests/test_staging.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mock_api.staging'`.

- [ ] **Step 3: Create the shared module**

```python
# src/mock_api/staging.py
"""Stage role-aligned artifacts into a directory for upload.

Extracted from scripts/upload_statsbomb_club.py so both StatsBomb source families
(commercial club-drop and open-data tournaments) stage through one path. Parameterized
by artifact specs so each family supplies its own source filenames.
"""
from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path


def _gzip_file(src: Path, dest: Path) -> None:
    """Stream-gzip src -> dest in 1 MiB chunks (never loads the body into memory)."""
    with src.open("rb") as f_in, gzip.open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out, length=1 << 20)


def stage_artifacts(
    source_root: Path,
    staging_dir: Path,
    artifact_specs: tuple[tuple[str, str, str], ...],
    metadata: dict,
    metadata_filename: str = "metadata.json",
) -> None:
    """Stage each artifact; gzip the .gz specs, copy the rest; write metadata as JSON.

    Compression rule: staged names ending in .gz are stream-gzipped (the multi-megabyte
    events/freeze-frame bodies), the rest copied plain (kilobyte roster/metadata).
    """
    for _role, source_name, staged_name in artifact_specs:
        src = source_root / source_name
        dest = staging_dir / staged_name
        if staged_name.endswith(".gz"):
            _gzip_file(src, dest)
        else:
            shutil.copyfile(src, dest)
    (staging_dir / metadata_filename).write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
```

- [ ] **Step 4: Delegate the commercial script's `stage_bundle` to the shared module**

In `scripts/upload_statsbomb_club.py`: remove the local `_gzip_file` and the body of `stage_bundle`, and replace `stage_bundle` with a thin wrapper (keeps the existing `mod.stage_bundle(bundle, staging, metadata)` test interface intact):

```python
from mock_api.staging import stage_artifacts  # noqa: E402  (with the other mock_api imports)

def stage_bundle(bundle: Bundle, staging: Path, metadata: dict) -> None:
    """Stage the four role-aligned artifacts (delegates to the shared stager)."""
    stage_artifacts(bundle.root, staging, ARTIFACT_SPECS, metadata, STAGED_METADATA_FILENAME)
```

Delete the now-unused `import gzip` / `import shutil` from the script if nothing else uses them (leave `import shutil` only if still referenced).

- [ ] **Step 5: Run the new test and the full commercial upload suite**

Run: `pytest src/tests/test_staging.py src/tests/test_upload_statsbomb.py -v`
Expected: PASS — including the untouched `TestStageBundle` (proves the refactor is behaviour-preserving).

- [ ] **Step 6: Lint & type-check**

Run: `ruff check src/mock_api/staging.py scripts/upload_statsbomb_club.py && pyright src/mock_api/staging.py`
Expected: clean.

---

### Task 2: `build_metadata_open`, selection constant, artifact specs, and open fixtures

Creates the pure open-family reader core and the synthetic nested-feed fixtures. Implements spec §4.1 (the full field-source map, SBO-SPEC-01) and §6.4's selection constant.

**Files:**
- Create: `src/formats/statsbomb_open.py`
- Modify: `src/tests/conftest.py` (add `sbo_*` fixtures)
- Test: `src/tests/test_statsbomb_open_format.py`

**Interfaces:**
- Produces:
  - `TOURNAMENTS: tuple[tuple[int, int], ...]` — the six `(competition_id, season_id)` pairs.
  - `OPEN_ARTIFACT_SPECS: tuple[tuple[str, str, str], ...]` — `(role, source_filename, staged_filename)`, 360 source name is `three-sixty.json`.
  - `build_metadata_open(match: dict) -> dict` — canonical metadata dict, same key set as `formats.statsbomb.build_metadata`.

- [ ] **Step 1: Add the synthetic open-family fixtures to conftest**

Append to `src/tests/conftest.py` (every value invented — no real entity renamed):

```python
# --- StatsBomb OPEN-DATA synthetic fixtures (spec 2026-09-06) --------------------
# Real-feed shape (nested match object, raw arrays). Every id/name/date invented.

@pytest.fixture
def sbo_match() -> dict:
    """A synthetic open-data match object in real feed shape."""
    return {
        "match_id": 8888888,
        "match_date": "2026-07-15",
        "kick_off": "18:00:00.000",
        "competition": {"competition_id": 555555, "country_name": "Europa", "competition_name": "Continental Cup"},
        "season": {"season_id": 777, "season_name": "2026"},
        "home_team": {
            "home_team_id": 60001, "home_team_name": "Northmoor United", "home_team_gender": "male",
            "home_team_group": "A", "country": {"id": 900, "name": "Northmoor"},
            "managers": [{"id": 5001, "name": "Alex Gaffer", "nickname": None, "dob": "1970-01-01",
                          "country": {"id": 900, "name": "Northmoor"}}],
        },
        "away_team": {
            "away_team_id": 60002, "away_team_name": "Southford City", "away_team_gender": "male",
            "away_team_group": None, "country": {"id": 901, "name": "Southford"},
            "managers": [{"id": 5002, "name": "Sam Boss", "nickname": None, "dob": "1972-02-02",
                          "country": {"id": 901, "name": "Southford"}}],
        },
        "home_score": 2, "away_score": 1,
        "match_status": "available", "match_status_360": "available",
        "last_updated": "2026-07-16T00:00:00.000000", "last_updated_360": "2026-07-16T00:10:00.000000",
        "metadata": {"data_version": "1.1.0", "shot_fidelity_version": "2", "xy_fidelity_version": "2"},
        "match_week": 3,
        "competition_stage": {"id": 10, "name": "Group Stage"},
        "stadium": {"id": 4001, "name": "Northmoor Arena", "country": {"id": 900, "name": "Northmoor"}},
        "referee": {"id": 3001, "name": "Ref Whistle", "country": {"id": 902, "name": "Elsewhere"}},
    }


@pytest.fixture
def sbo_events() -> list[dict]:
    """Coherent open event stream: two periods, a Half End, exactly two teams."""
    return [
        {"id": "e1", "period": 1, "type": {"name": "Pass"},
         "team": {"id": 60001, "name": "Northmoor United"}, "player": {"id": 70001, "name": "Player One"}},
        {"id": "e2", "period": 2, "type": {"name": "Pass"},
         "team": {"id": 60002, "name": "Southford City"}, "player": {"id": 70002, "name": "Player Two"}},
        {"id": "e3", "period": 2, "type": {"name": "Half End"},
         "team": {"id": 60001, "name": "Northmoor United"}},
    ]


@pytest.fixture
def sbo_frames() -> list[dict]:
    """three-sixty payload: one frame joined to event e1."""
    return [{
        "event_uuid": "e1",
        "visible_area": [0.0, 0.0, 120.0, 0.0, 120.0, 80.0, 0.0, 80.0, 0.0, 0.0],
        "freeze_frame": [
            {"teammate": True, "actor": True, "keeper": False, "location": [60.0, 40.0]},
            {"teammate": False, "actor": False, "keeper": True, "location": [2.0, 40.0]},
        ],
    }]


@pytest.fixture
def sbo_lineups() -> list[dict]:
    """Open lineups: NO birth_date / player_height / player_gender (open feed lacks them)."""
    return [
        {"team_id": 60001, "team_name": "Northmoor United", "lineup": [
            {"player_id": 70001, "player_name": "Player One", "player_nickname": "P1",
             "jersey_number": 10, "country": {"id": 900, "name": "Northmoor"},
             "positions": [{"position_id": 21, "position": "Left Wing", "from": "00:00",
                            "from_period": 1}]},
        ]},
        {"team_id": 60002, "team_name": "Southford City", "lineup": [
            {"player_id": 70002, "player_name": "Player Two", "player_nickname": "",
             "jersey_number": 7, "country": {"id": 901, "name": "Southford"}, "positions": []},
        ]},
    ]


@pytest.fixture
def sbo_season_matches(sbo_match) -> list[dict]:
    """A season file: one 360-available match (competition 55) + one unscheduled."""
    other = dict(sbo_match)
    other["match_id"] = 8888889
    other["match_status_360"] = "unscheduled"
    return [sbo_match, other]
```

- [ ] **Step 2: Write the failing reader tests**

```python
# src/tests/test_statsbomb_open_format.py
"""Tests for formats/statsbomb_open.py (pure reader)."""
from __future__ import annotations

from formats import statsbomb as sb
from formats import statsbomb_open as sbo


def _key_shape(d: dict) -> dict:
    """Recursive top-level+one-level key structure, for schema comparison."""
    return {k: (sorted(v.keys()) if isinstance(v, dict) else None) for k, v in d.items()}


def test_metadata_key_set_matches_commercial(sbo_match, sb_match_row, sb_competition_row) -> None:
    commercial = sb.build_metadata(sb_match_row, sb_competition_row, 90001, 90007, "female", "female")
    open_md = sbo.build_metadata_open(sbo_match)
    assert _key_shape(open_md) == _key_shape(commercial)


def test_richer_fields_all_preserved(sbo_match) -> None:
    md = sbo.build_metadata_open(sbo_match)
    # every slot build_metadata hard-nulls, populated from the open feed (SBO-SPEC-01)
    assert md["competition_stage"]["id"] == 10
    assert md["stadium"]["id"] == 4001
    assert md["stadium"]["country"] == {"id": 900, "name": "Northmoor"}
    assert md["referee"]["id"] == 3001
    assert md["referee"]["country"] == {"id": 902, "name": "Elsewhere"}
    assert md["home_team"]["home_team_group"] == "A"
    assert md["home_team"]["country"] == {"id": 900, "name": "Northmoor"}
    assert md["home_team"]["managers"][0]["dob"] == "1970-01-01"
    assert md["home_team"]["managers"][0]["id"] == 5001
    # away path is a structurally identical passthrough — assert it too
    assert md["away_team"]["country"] == {"id": 901, "name": "Southford"}
    assert md["away_team"]["managers"][0]["dob"] == "1972-02-02"


def test_metadata_triplet_read_from_nested_object(sbo_match) -> None:
    md = sbo.build_metadata_open(sbo_match)
    assert md["metadata"] == {"data_version": "1.1.0", "shot_fidelity_version": "2", "xy_fidelity_version": "2"}


def test_credentialed_fields_are_null(sbo_match) -> None:
    md = sbo.build_metadata_open(sbo_match)
    for field in ("attendance", "behind_closed_doors", "neutral_ground", "collection_status", "play_status"):
        assert md[field] is None


def test_team_ids_from_match_not_events(sbo_match) -> None:
    md = sbo.build_metadata_open(sbo_match)
    assert md["home_team"]["home_team_id"] == 60001
    assert md["away_team"]["away_team_id"] == 60002


def test_gender_from_match_object(sbo_match) -> None:
    md = sbo.build_metadata_open(sbo_match)
    assert md["home_team"]["home_team_gender"] == "male"
    assert md["away_team"]["away_team_gender"] == "male"


def test_tournaments_constant_is_the_six_documented_pairs() -> None:
    assert sbo.TOURNAMENTS == ((43, 106), (72, 107), (55, 43), (55, 282), (53, 106), (53, 315))
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest src/tests/test_statsbomb_open_format.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'formats.statsbomb_open'`.

- [ ] **Step 4: Implement the reader core**

```python
# src/formats/statsbomb_open.py
"""StatsBomb open-data tournament reader (second source family).

Pure transforms over StatsBomb's real open-data feed: reshape the already-nested match
object into the canonical metadata schema, select 360-available matches, and partition
players against a known-id set. No I/O — the upload script fetches and stages.

The open feed is the real published format, so unlike formats/statsbomb.py there is no
de-pivot, no competition join, and no team-id resolution. See
docs/superpowers/specs/2026-09-06-statsbomb-open-tournaments-360-owner-tier-design.md.
"""
from __future__ import annotations

from formats.statsbomb import _int_or_none

# Six complete-tournament open-data competitions (public identifiers — spec D-6).
TOURNAMENTS: tuple[tuple[int, int], ...] = (
    (43, 106),   # FIFA World Cup 2022 (M)
    (72, 107),   # Women's World Cup 2023 (F)
    (55, 43),    # UEFA Euro 2020 (M)
    (55, 282),   # UEFA Euro 2024 (M)
    (53, 106),   # UEFA Women's Euro 2022 (F)
    (53, 315),   # UEFA Women's Euro 2025 (F)
)

# (role, source_filename, staged_filename). Source names are what the open assembly
# writes into the bundle dir; the 360 source is `three-sixty.json`, not `frames.json`.
OPEN_ARTIFACT_SPECS: tuple[tuple[str, str, str], ...] = (
    ("events", "events.json", "events.json.gz"),
    ("freeze_frames", "three-sixty.json", "freeze_frames.json.gz"),
    ("roster", "lineups.json", "roster.json"),
)


def build_metadata_open(match: dict) -> dict:
    """Reshape an open-data match object into the canonical metadata schema (spec §4.1).

    The open feed is already nested and richer than the commercial reconstruction; this
    whitelists it into the SAME key set build_metadata() emits, preserving the richer
    values and nulling the credentialed-only fields the open catalogue does not carry.
    Values are never invented (ADR 0010 D-4).
    """
    home = match.get("home_team") or {}
    away = match.get("away_team") or {}
    competition = match.get("competition") or {}
    season = match.get("season") or {}
    stage = match.get("competition_stage") or {}
    stadium = match.get("stadium") or {}
    referee = match.get("referee") or {}
    meta = match.get("metadata") or {}
    return {
        "match_id": _int_or_none(match.get("match_id")),
        "match_date": match.get("match_date"),
        "kick_off": match.get("kick_off"),
        "competition": {
            "competition_id": competition.get("competition_id"),
            "country_name": competition.get("country_name"),
            "competition_name": competition.get("competition_name"),
        },
        "season": {"season_id": season.get("season_id"), "season_name": season.get("season_name")},
        "home_team": {
            "home_team_id": home.get("home_team_id"),
            "home_team_name": home.get("home_team_name"),
            "home_team_gender": home.get("home_team_gender"),
            "home_team_group": home.get("home_team_group"),
            "country": home.get("country"),
            "managers": home.get("managers") or [],
        },
        "away_team": {
            "away_team_id": away.get("away_team_id"),
            "away_team_name": away.get("away_team_name"),
            "away_team_gender": away.get("away_team_gender"),
            "away_team_group": away.get("away_team_group"),
            "country": away.get("country"),
            "managers": away.get("managers") or [],
        },
        "home_score": _int_or_none(match.get("home_score")),
        "away_score": _int_or_none(match.get("away_score")),
        "match_status": match.get("match_status"),
        "match_status_360": match.get("match_status_360"),
        "last_updated": match.get("last_updated"),
        "last_updated_360": match.get("last_updated_360"),
        "metadata": {
            "data_version": meta.get("data_version"),
            "shot_fidelity_version": meta.get("shot_fidelity_version"),
            "xy_fidelity_version": meta.get("xy_fidelity_version"),
        },
        "match_week": _int_or_none(match.get("match_week")),
        "competition_stage": {"id": stage.get("id"), "name": stage.get("name")},
        "stadium": {"id": stadium.get("id"), "name": stadium.get("name"), "country": stadium.get("country")},
        "referee": {"id": referee.get("id"), "name": referee.get("name"), "country": referee.get("country")},
        "attendance": match.get("attendance"),
        "behind_closed_doors": match.get("behind_closed_doors"),
        "neutral_ground": match.get("neutral_ground"),
        "collection_status": match.get("collection_status"),
        "play_status": match.get("play_status"),
    }
```

- [ ] **Step 5: Run to verify pass**

Run: `pytest src/tests/test_statsbomb_open_format.py -v`
Expected: PASS (all seven tests).

- [ ] **Step 6: Lint & type-check**

Run: `ruff check src/formats/statsbomb_open.py && pyright src/formats/statsbomb_open.py`
Expected: clean. (`_int_or_none` is imported from `formats.statsbomb`; if ruff flags the private import, keep it — it is an intentional intra-package reuse, matching how the commercial family exposes its helpers.)

---

### Task 3: Selection filter + transposed-pair guard + player skip-and-report

Implements spec §6.4 (transposed-pair guard, SBO-SPEC-03) and §5.2 (skip-and-report, SBO-SPEC-04) as pure helpers.

**Files:**
- Modify: `src/formats/statsbomb_open.py` (add two functions)
- Test: `src/tests/test_statsbomb_open_format.py` (add cases)

**Interfaces:**
- Produces:
  - `select_ingestible_matches(season_matches: list[dict], competition_id: int) -> list[dict]` — returns the `match_status_360 == "available"` matches; raises `ValueError` if any match's `competition.competition_id` ≠ `competition_id`.
  - `partition_new_players(players: list[dict], known_ids: set[str]) -> tuple[list[dict], list[str]]` — returns `(new_players, skipped_ids)`; does not mutate `known_ids`.

- [ ] **Step 1: Write the failing tests**

```python
# add to src/tests/test_statsbomb_open_format.py
import pytest


def test_select_keeps_only_360_available(sbo_season_matches) -> None:
    got = sbo.select_ingestible_matches(sbo_season_matches, 555555)
    assert [m["match_id"] for m in got] == [8888888]


def test_select_raises_on_transposed_pair(sbo_season_matches) -> None:
    # request competition 111111 but the fetched matches carry 555555 (both invented)
    with pytest.raises(ValueError, match="competition_id"):
        sbo.select_ingestible_matches(sbo_season_matches, 111111)


def test_partition_splits_new_from_known() -> None:
    players = [{"id": "70001"}, {"id": "70002"}, {"id": "70003"}]
    new, skipped = sbo.partition_new_players(players, {"70002"})
    assert [p["id"] for p in new] == ["70001", "70003"]
    assert skipped == ["70002"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest src/tests/test_statsbomb_open_format.py -k "select or partition" -v`
Expected: FAIL — `AttributeError: module 'formats.statsbomb_open' has no attribute 'select_ingestible_matches'`.

- [ ] **Step 3: Implement the helpers**

```python
# append to src/formats/statsbomb_open.py

def select_ingestible_matches(season_matches: list[dict], competition_id: int) -> list[dict]:
    """Return the 360-available matches, asserting they belong to `competition_id`.

    Transposed-pair guard (spec §6.4): season_id is per-competition, so a swapped
    (competition_id, season_id) pair fetches a real-but-wrong tournament. The open match
    object carries competition.competition_id inline, so a mismatch fails loud here
    rather than silently ingesting the wrong competition.
    """
    ingestible: list[dict] = []
    for match in season_matches:
        got = (match.get("competition") or {}).get("competition_id")
        if got != competition_id:
            raise ValueError(
                f"match {match.get('match_id')!r} carries competition_id {got!r}, "
                f"requested {competition_id!r} — transposed (competition_id, season_id) pair"
            )
        if match.get("match_status_360") == "available":
            ingestible.append(match)
    return ingestible


def partition_new_players(players: list[dict], known_ids: set[str]) -> tuple[list[dict], list[str]]:
    """Split players into (new, skipped-ids) against known_ids (spec §5.2).

    The open (sparser) source yields to any id already present so it never clobbers a
    richer commercial record. Does not mutate known_ids — the caller updates it so the
    skip sees intra-run adds.
    """
    new: list[dict] = []
    skipped: list[str] = []
    for player in players:
        pid = player["id"]
        if pid in known_ids:
            skipped.append(pid)
        else:
            new.append(player)
    return new, skipped
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest src/tests/test_statsbomb_open_format.py -v`
Expected: PASS (all cases).

- [ ] **Step 5: Lint & type-check**

Run: `ruff check src/formats/statsbomb_open.py && pyright src/formats/statsbomb_open.py`
Expected: clean.

---

### Task 4: Fetch + cache layer

Implements spec §6.3. Network-fetch with a local cache and an optional local-clone bypass, so dry-runs and re-runs reuse bytes and tests never hit the network.

**Files:**
- Create: `scripts/upload_statsbomb_open.py` (skeleton + `fetch_json`)
- Test: `src/tests/test_upload_statsbomb_open.py`

**Interfaces:**
- Produces: `fetch_json(rel_path: str, cache_dir: Path, source_dir: Path | None) -> object`. Resolution order: `source_dir/rel_path` if it exists → `cache_dir/rel_path` if it exists → HTTP GET `BASE_URL + rel_path`, written to `cache_dir/rel_path`, then parsed. Raises `RuntimeError` naming the URL on fetch failure.
- Consumes: nothing from earlier tasks yet (the orchestration in Task 5 imports Task 1–3 outputs).

- [ ] **Step 1: Write the failing tests**

```python
# src/tests/test_upload_statsbomb_open.py
"""Tests for scripts/upload_statsbomb_open.py (no real S3, no real network)."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest


def test_fetch_reads_source_dir_without_network(tmp_path, load_script) -> None:
    mod = load_script("upload_statsbomb_open")
    src = tmp_path / "clone"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "x.json").write_text(json.dumps({"ok": 1}), encoding="utf-8")
    with patch.object(mod.urllib.request, "urlopen") as urlopen:
        got = mod.fetch_json("sub/x.json", cache_dir=tmp_path / "cache", source_dir=src)
    assert got == {"ok": 1}
    urlopen.assert_not_called()


def test_fetch_uses_cache_without_refetch(tmp_path, load_script) -> None:
    mod = load_script("upload_statsbomb_open")
    cache = tmp_path / "cache"
    (cache / "sub").mkdir(parents=True)
    (cache / "sub" / "x.json").write_text(json.dumps({"cached": 1}), encoding="utf-8")
    with patch.object(mod.urllib.request, "urlopen") as urlopen:
        got = mod.fetch_json("sub/x.json", cache_dir=cache, source_dir=None)
    assert got == {"cached": 1}
    urlopen.assert_not_called()


def test_fetch_failure_raises_with_url(tmp_path, load_script) -> None:
    mod = load_script("upload_statsbomb_open")
    with patch.object(mod.urllib.request, "urlopen", side_effect=OSError("boom")):
        with pytest.raises(RuntimeError, match="events/1.json"):
            mod.fetch_json("events/1.json", cache_dir=tmp_path / "cache", source_dir=None)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest src/tests/test_upload_statsbomb_open.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Create the script skeleton with `fetch_json`**

```python
# scripts/upload_statsbomb_open.py
"""Upload StatsBomb OPEN-DATA tournament 360 to the mock provider API (OWNER tier).

Owner-tier (visibility=private, provenance=redistributed) ingest of StatsBomb's public
open-data corpus for the six complete-tournament 360 competitions. The open-data licence
forbids third-party redistribution; owner-tier single-user hosting is the compliant use.
See docs/superpowers/specs/2026-09-06-statsbomb-open-tournaments-360-owner-tier-design.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import boto3
from pydantic import ValidationError

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from canonical.models import PlayerRecord  # noqa: E402
from formats.statsbomb import (  # noqa: E402
    MatchInfo,
    assert_delivery_coherent,
    match_info,
    players_from_lineups,
)
from formats.statsbomb_open import (  # noqa: E402
    OPEN_ARTIFACT_SPECS,
    TOURNAMENTS,
    build_metadata_open,
    partition_new_players,
    select_ingestible_matches,
)
from mock_api.staging import stage_artifacts  # noqa: E402
from mock_api.upload import upload_game  # noqa: E402
from mock_api.upload_players import upload_players  # noqa: E402

PROVIDER = "statsbomb"
SOURCE_NAME = "StatsBomb"
SOURCE_LICENCE = "StatsBomb Public Data User Agreement (open data); redistribution to third parties not permitted"
BASE_URL = "https://raw.githubusercontent.com/statsbomb/open-data/master/data/"
_VALIDATION_PROBE_TIMESTAMP = "1970-01-01T00:00:00Z"


def fetch_json(rel_path: str, cache_dir: Path, source_dir: Path | None) -> Any:
    """Resolve one open-data file: local clone -> cache -> HTTP GET (cached on the way)."""
    if source_dir is not None:
        local = source_dir / rel_path
        if local.is_file():
            return json.loads(local.read_text(encoding="utf-8"))
    cached = cache_dir / rel_path
    if cached.is_file():
        return json.loads(cached.read_text(encoding="utf-8"))
    url = BASE_URL + rel_path
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 (pinned HTTPS host)
            raw = resp.read()
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"failed to fetch {url}: {e}") from e
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(raw)
    return json.loads(raw.decode("utf-8"))
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest src/tests/test_upload_statsbomb_open.py -v`
Expected: PASS (three fetch tests).

- [ ] **Step 5: Lint (ruff ignore for S310 comes in Task 7; use the inline `# noqa: S310`)**

Run: `ruff check scripts/upload_statsbomb_open.py`
Expected at THIS intermediate stage: the urllib `S310` is covered by the inline noqa, but the module also reports `F401` unused-import for the symbols the skeleton front-loads that only Task 5's orchestration consumes (`argparse`, `os`, `tempfile`, `PlayerRecord`, the four `formats` symbols, the five `statsbomb_open` symbols, `stage_artifacts`, `upload_game`, `upload_players`). Those `F401`s are **expected until Task 5** and clear once `run`/`main`/`upload_open_match` use them. The test file is clean now. Do NOT add blanket `# noqa: F401` to paper over them — Task 5 resolves them properly. (The file-level `S310` ignore is added in Task 7 for consistency with the other scripts.)

---

### Task 5: Orchestration + CLI

Ties Tasks 1–4 together: per match, assemble a bundle dir, pre-flight, reshape, derive+skip players, stage, upload. Implements spec §4.2, §5.2, §6.1, §7.1.

**Files:**
- Modify: `scripts/upload_statsbomb_open.py` (add `assemble_bundle`, `upload_open_match`, `load_known_player_ids`, `run`, `main`)
- Test: `src/tests/test_upload_statsbomb_open.py` (add cases)

**Interfaces:**
- Consumes: `fetch_json` (Task 4); `select_ingestible_matches`, `build_metadata_open`, `partition_new_players`, `OPEN_ARTIFACT_SPECS`, `TOURNAMENTS` (Tasks 2–3); `assert_delivery_coherent`, `players_from_lineups`, `match_info` (commercial reader); `stage_artifacts` (Task 1); `upload_game`, `upload_players` (shared).
- Produces:
  - `upload_open_match(match, events, frames, lineups, *, bucket, known_ids: set[str], dry_run: bool) -> tuple[str, int, int]` → `(match_id, n_new_players, n_skipped)`. Mutates `known_ids` with the new ids on a real (non-dry) run.
  - `load_known_player_ids(bucket: str) -> set[str]` — ids in `statsbomb/_private/players.json` (empty if absent).

- [ ] **Step 1: Write the failing orchestration tests**

```python
# add to src/tests/test_upload_statsbomb_open.py
import gzip
from pathlib import Path


def test_uploads_private_redistributed_with_index_fields(
    tmp_path, load_script, sbo_match, sbo_events, sbo_frames, sbo_lineups
) -> None:
    mod = load_script("upload_statsbomb_open")
    with (
        patch.object(mod, "upload_game") as game,
        patch.object(mod, "upload_players") as players,
    ):
        mid, n_new, n_skip = mod.upload_open_match(
            sbo_match, sbo_events, sbo_frames, sbo_lineups,
            bucket="test-bucket", known_ids=set(), dry_run=False,
        )
    assert mid == "8888888"
    assert (n_new, n_skip) == (2, 0)
    kwargs = game.call_args.kwargs
    assert kwargs["provider"] == "statsbomb"
    assert kwargs["visibility"] == "private"
    assert kwargs["provenance"] == "redistributed"
    assert kwargs["game_id"] == "8888888"
    assert kwargs["date"] == "2026-07-15"
    assert kwargs["source_name"] == "StatsBomb"
    assert kwargs["source_licence"].startswith("StatsBomb Public Data User Agreement")
    assert players.call_args.kwargs["visibility"] == "private"


def test_stages_four_role_aligned_artifacts(
    tmp_path, load_script, sbo_match, sbo_events, sbo_frames, sbo_lineups
) -> None:
    mod = load_script("upload_statsbomb_open")
    seen: list[str] = []
    with (
        patch.object(mod, "upload_game", side_effect=lambda **k: seen.extend(sorted(p.name for p in k["game_dir"].iterdir()))),
        patch.object(mod, "upload_players"),
    ):
        mod.upload_open_match(sbo_match, sbo_events, sbo_frames, sbo_lineups,
                              bucket="b", known_ids=set(), dry_run=False)
    assert seen == ["events.json.gz", "freeze_frames.json.gz", "metadata.json", "roster.json"]


def test_orphan_frame_blocks_upload(load_script, sbo_match, sbo_events, sbo_lineups) -> None:
    mod = load_script("upload_statsbomb_open")
    bad_frames = [{"event_uuid": "orphan", "freeze_frame": [{"teammate": True}]}]
    with (
        patch.object(mod, "upload_game") as game,
        patch.object(mod, "upload_players") as players,
    ):
        with pytest.raises(ValueError, match="unknown event id"):
            mod.upload_open_match(sbo_match, sbo_events, bad_frames, sbo_lineups,
                                  bucket="b", known_ids=set(), dry_run=False)
    game.assert_not_called()
    players.assert_not_called()


def test_skips_known_player_uploads_only_new(load_script, sbo_match, sbo_events, sbo_frames, sbo_lineups) -> None:
    mod = load_script("upload_statsbomb_open")
    known = {"70001"}  # pretend 70001 already came from the commercial load
    captured: list[dict] = []
    # NOTE: upload_open_match tears down its temp dir before returning, so the players
    # input_file must be read DURING the mocked upload_players call, not after.
    with (
        patch.object(mod, "upload_game"),
        patch.object(mod, "upload_players",
                     side_effect=lambda **k: captured.extend(
                         json.loads(k["input_file"].read_text(encoding="utf-8"))["players"])),
    ):
        _mid, n_new, n_skip = mod.upload_open_match(
            sbo_match, sbo_events, sbo_frames, sbo_lineups,
            bucket="b", known_ids=known, dry_run=False,
        )
    assert (n_new, n_skip) == (1, 1)
    assert [p["id"] for p in captured] == ["70002"]  # only the new id reaches upload_players
    assert known == {"70001", "70002"}  # known_ids grew (intra-run dedup)


def test_intra_run_dedup_second_match_skips_shared_id(
    load_script, sbo_match, sbo_events, sbo_frames, sbo_lineups
) -> None:
    mod = load_script("upload_statsbomb_open")
    known: set[str] = set()
    with patch.object(mod, "upload_game"), patch.object(mod, "upload_players"):
        mod.upload_open_match(sbo_match, sbo_events, sbo_frames, sbo_lineups,
                              bucket="b", known_ids=known, dry_run=False)
        second = dict(sbo_match); second["match_id"] = 8888890
        _mid, n_new, n_skip = mod.upload_open_match(
            second, sbo_events, sbo_frames, sbo_lineups,
            bucket="b", known_ids=known, dry_run=False,
        )
    assert (n_new, n_skip) == (0, 2)  # both players already added by the first match


def test_dry_run_makes_no_upload_calls(load_script, sbo_match, sbo_events, sbo_frames, sbo_lineups) -> None:
    mod = load_script("upload_statsbomb_open")
    with (
        patch.object(mod, "upload_game") as game,
        patch.object(mod, "upload_players") as players,
    ):
        mod.upload_open_match(sbo_match, sbo_events, sbo_frames, sbo_lineups,
                              bucket="b", known_ids=set(), dry_run=True)
    game.assert_not_called()
    players.assert_not_called()


def test_staged_gz_artifacts_roundtrip(load_script, sbo_match, sbo_events, sbo_frames, sbo_lineups) -> None:
    # complements test_stages_...: the gzipped bodies decompress back to the source feed
    mod = load_script("upload_statsbomb_open")
    bodies: dict[str, object] = {}

    def _capture(**k):
        d = k["game_dir"]
        for name, key in (("events.json.gz", "events"), ("freeze_frames.json.gz", "frames")):
            with gzip.open(d / name, "rt", encoding="utf-8") as f:
                bodies[key] = json.load(f)

    with patch.object(mod, "upload_game", side_effect=_capture), patch.object(mod, "upload_players"):
        mod.upload_open_match(sbo_match, sbo_events, sbo_frames, sbo_lineups,
                              bucket="b", known_ids=set(), dry_run=False)
    assert bodies["events"] == sbo_events
    assert bodies["frames"] == sbo_frames


def test_run_skips_defective_match_and_continues(monkeypatch, load_script, sbo_match, sbo_events, sbo_frames, sbo_lineups) -> None:
    # a match whose upstream artifact is unparseable is skipped-and-reported, run continues (§6.4)
    mod = load_script("upload_statsbomb_open")
    good = dict(sbo_match); good["match_id"] = 8888888
    bad = dict(sbo_match); bad["match_id"] = 8888889
    season = [good, bad]  # both competition 555555, both match_status_360 "available"

    def fake_fetch(rel, cache_dir, source_dir):
        if rel.startswith("matches/"):
            return season
        if rel == "three-sixty/8888889.json":
            raise json.JSONDecodeError("Expecting ',' delimiter", "doc", 100)
        if rel.startswith("events/"):
            return sbo_events
        if rel.startswith("three-sixty/"):
            return sbo_frames
        if rel.startswith("lineups/"):
            return sbo_lineups
        raise AssertionError(f"unexpected fetch: {rel}")

    monkeypatch.setattr(mod, "fetch_json", fake_fetch)
    with (
        patch.object(mod, "load_known_player_ids", return_value=set()),
        patch.object(mod, "upload_game") as game,
        patch.object(mod, "upload_players"),
    ):
        defective = mod.run([(555555, 777)], bucket="b", cache_dir=Path("x"), source_dir=None, dry_run=False)

    assert game.call_count == 1  # only the good match uploaded; the defective one was skipped
    assert [mid for mid, _ in defective] == ["8888889"]
    assert "JSONDecodeError" in defective[0][1]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest src/tests/test_upload_statsbomb_open.py -k "upload_open_match or stages or orphan or skips or intra or dry" -v`
Expected: FAIL — `upload_open_match` undefined.

- [ ] **Step 3: Implement the orchestration**

```python
# append to scripts/upload_statsbomb_open.py


def assemble_bundle(events: list, frames: list, lineups: list, dest: Path) -> None:
    """Write the three passthrough source files under the OPEN_ARTIFACT_SPECS names."""
    (dest / "events.json").write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    (dest / "three-sixty.json").write_text(json.dumps(frames, ensure_ascii=False), encoding="utf-8")
    (dest / "lineups.json").write_text(json.dumps(lineups, ensure_ascii=False), encoding="utf-8")


def validate_open_match(match: dict, events: list, frames: list, lineups: list) -> tuple[dict, MatchInfo, list[dict]]:
    """All fallible, S3-free per-match validation. Raises on a defective match; returns
    (metadata, match_info, player records). No mutation, no I/O."""
    assert_delivery_coherent(events, frames, lineups)
    metadata = build_metadata_open(match)
    info = match_info(metadata)
    players = players_from_lineups(lineups)
    for record in players:
        PlayerRecord.model_validate({**record, "visibility": "private", "updated_at": _VALIDATION_PROBE_TIMESTAMP})
    return metadata, info, players


def _stage_and_upload_open_match(
    metadata: dict, info: MatchInfo, players: list[dict], events: list, frames: list, lineups: list,
    *, bucket: str, known_ids: set[str], dry_run: bool,
) -> tuple[str, int, int]:
    """Partition players, then (unless dry_run) stage + upload. Assumes VALIDATED inputs — the
    only thing that raises here is the S3 layer, so run() calls this OUTSIDE its defect-catch and
    an upload failure propagates rather than being mis-reported as a defective match."""
    new_players, skipped = partition_new_players(players, known_ids)
    known_ids.update(p["id"] for p in new_players)  # before the dry-run return so a dry-run previews intra-run dedup
    if dry_run:
        return info.match_id, len(new_players), len(skipped)
    with tempfile.TemporaryDirectory(prefix="sb-open-") as tmp:
        # Two dirs: assembled source bundle vs staging output, so game_dir holds ONLY the 4 staged artifacts.
        bundle_dir = Path(tmp) / "bundle"
        staging = Path(tmp) / "stage"
        bundle_dir.mkdir()
        staging.mkdir()
        assemble_bundle(events, frames, lineups, bundle_dir)
        stage_artifacts(bundle_dir, staging, OPEN_ARTIFACT_SPECS, metadata)
        upload_game(
            game_dir=staging, provider=PROVIDER, game_id=info.match_id, bucket=bucket,
            visibility="private", provenance="redistributed", date=info.date,
            home=info.home, away=info.away, source_name=SOURCE_NAME, source_licence=SOURCE_LICENCE,
        )
    if new_players:
        with tempfile.TemporaryDirectory(prefix="sb-open-players-") as tmp:
            players_file = Path(tmp) / "players.json"
            players_file.write_text(json.dumps({"players": new_players}, ensure_ascii=False), encoding="utf-8")
            upload_players(
                input_file=players_file, provider=PROVIDER, bucket=bucket, visibility="private",
                source_name=SOURCE_NAME, source_licence=SOURCE_LICENCE,
            )
    return info.match_id, len(new_players), len(skipped)


def upload_open_match(
    match: dict, events: list, frames: list, lineups: list, *,
    bucket: str, known_ids: set[str], dry_run: bool,
) -> tuple[str, int, int]:
    """Validate then stage+upload one open match. run() calls the two phases separately (so an
    upload failure is never mis-classified as a defect); this composed form is kept for tests."""
    metadata, info, players = validate_open_match(match, events, frames, lineups)
    return _stage_and_upload_open_match(
        metadata, info, players, events, frames, lineups, bucket=bucket, known_ids=known_ids, dry_run=dry_run,
    )


def load_known_player_ids(bucket: str) -> set[str]:
    """Ids already in statsbomb/_private/players.json (empty if the index is absent)."""
    s3 = boto3.client("s3")
    try:
        obj = s3.get_object(Bucket=bucket, Key=f"{PROVIDER}/_private/players.json")
    except s3.exceptions.NoSuchKey:
        return set()
    return {p["id"] for p in json.loads(obj["Body"].read().decode("utf-8")).get("players", [])}


def run(tournaments, *, bucket: str, cache_dir: Path, source_dir: Path | None, dry_run: bool) -> list[tuple[str, str]]:
    """Ingest each tournament's 360-available matches, SKIPPING any defective match (an
    unparseable / missing / incoherent upstream artifact) with a reported reason rather
    than aborting the run. Every per-match check runs before that match's first upload, so
    a skip never leaves a partial load. Returns the (match_id, reason) skipped list. §6.4.
    """
    known_ids: set[str] = set() if dry_run else load_known_player_ids(bucket)
    total = new_total = skip_total = 0
    defective: list[tuple[str, str]] = []
    for cid, sid in tournaments:
        season = fetch_json(f"matches/{cid}/{sid}.json", cache_dir, source_dir)
        matches = select_ingestible_matches(season, cid)
        print(f"competition {cid} season {sid}: {len(matches)} match(es) with 360")
        for match in matches:
            mid = str(match["match_id"])
            try:
                events = fetch_json(f"events/{mid}.json", cache_dir, source_dir)
                frames = fetch_json(f"three-sixty/{mid}.json", cache_dir, source_dir)
                lineups = fetch_json(f"lineups/{mid}.json", cache_dir, source_dir)
                metadata, info, players = validate_open_match(match, events, frames, lineups)
            except (json.JSONDecodeError, RuntimeError, ValueError, ValidationError) as e:
                # Defective upstream artifact — reported, never silently dropped. Only the S3-free
                # validation is inside this catch, so an upload failure (below) can never be
                # mis-reported as a defect, and a skip never leaves a partial load.
                defective.append((mid, f"{type(e).__name__}: {str(e)[:140]}"))
                continue
            _mid, n_new, n_skip = _stage_and_upload_open_match(
                metadata, info, players, events, frames, lineups,
                bucket=bucket, known_ids=known_ids, dry_run=dry_run,
            )
            total += 1
            new_total += n_new
            skip_total += n_skip
    verb = "validated" if dry_run else "uploaded"
    est = " (estimate — run-relative)" if dry_run else ""
    print(f"\n{verb} {total} match(es); {new_total} new player(s){est}, {skip_total} skipped (already known).")
    if defective:
        print(f"\nSKIPPED {len(defective)} defective match(es) (unparseable/missing/incoherent upstream artifact):")
        for mid, reason in defective:
            print(f"  match {mid}: {reason}")
    return defective


def _parse_pairs(values: list[str] | None):
    if not values:
        return TOURNAMENTS
    pairs = []
    for v in values:
        cid, sid = v.split(":")
        pairs.append((int(cid), int(sid)))
    return tuple(pairs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload StatsBomb open-data tournament 360 (owner tier)")
    parser.add_argument("--bucket", default=os.environ.get("PINING_BUCKET"))
    # Default cache lives OUTSIDE the repo tree — the fetched raw JSON is licensed
    # StatsBomb data that must never be committed (spec §1.1). $SB_OPEN_CACHE overrides.
    default_cache = Path(os.environ["SB_OPEN_CACHE"]) if os.environ.get("SB_OPEN_CACHE") else Path.home() / ".cache" / "pining-sb-open"
    parser.add_argument("--cache-dir", type=Path, default=default_cache)
    parser.add_argument("--source-dir", type=Path, default=None, help="Local statsbomb/open-data clone (skips network)")
    parser.add_argument("--competition-season", action="append", metavar="CID:SID",
                        help="Override the built-in six tournaments (repeatable)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Fetch + validate, no S3 writes")
    group.add_argument("--execute", action="store_true", help="Upload to S3")
    args = parser.parse_args()
    if not args.bucket and args.execute:
        parser.error("--bucket is required with --execute (or set PINING_BUCKET)")
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    run(_parse_pairs(args.competition_season), bucket=args.bucket, cache_dir=args.cache_dir,
        source_dir=args.source_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the full open-family suite**

Run: `pytest src/tests/test_upload_statsbomb_open.py -v`
Expected: PASS (all fetch + orchestration tests).

- [ ] **Step 5: Lint & type-check**

Run: `ruff check scripts/upload_statsbomb_open.py && pyright scripts/upload_statsbomb_open.py`
Expected: clean.

---

### Task 6: Verify-script extension (sample a `redistributed` match)

Implements spec §8/OQ-B: `verify_statsbomb_load.py` also samples one `provenance="redistributed"` match and runs the artifact + public-404 checks on it, keeping it a smoke test (no hard failure when none exist, so commercial-only runs still pass).

**Files:**
- Modify: `scripts/verify_statsbomb_load.py` (add a redistributed sample block after the existing restricted checks)
- Test: `src/tests/test_verify_statsbomb_load.py` (add a unit test for the new selection helper)

**Interfaces:**
- Produces: `select_redistributed(matches: list[dict]) -> list[dict]` — entries with `provenance == "redistributed"`.

- [ ] **Step 1: Write the failing test**

```python
# add to src/tests/test_verify_statsbomb_load.py
def test_select_redistributed_filters_by_provenance(load_script) -> None:
    mod = load_script("verify_statsbomb_load")
    matches = [
        {"id": "1", "provenance": "original"},
        {"id": "2", "provenance": "redistributed"},
        {"id": "3"},
    ]
    assert [m["id"] for m in mod.select_redistributed(matches)] == ["2"]
```

(If `test_verify_statsbomb_load.py` already imports the module a particular way, match that; otherwise use the `load_script` fixture as shown.)

- [ ] **Step 2: Run to verify failure**

Run: `pytest src/tests/test_verify_statsbomb_load.py -k redistributed -v`
Expected: FAIL — `select_redistributed` undefined.

- [ ] **Step 3: Implement the helper and wire it into `main`**

```python
# scripts/verify_statsbomb_load.py — add near the top-level helpers
def select_redistributed(matches: list[dict]) -> list[dict]:
    """Owner-visible matches whose provenance marks them open-data (spec §8)."""
    return [m for m in matches if m.get("provenance") == "redistributed"]
```

In `main`, after the existing restricted-artifact loop, add a smoke check:

```python
    redistributed = select_redistributed(owner_matches)
    if redistributed:
        print(f"OK: owner sees {len(redistributed)} redistributed (open-data) match(es)")
        sample = redistributed[0]
        mid = sample["id"]
        if set(sample.get("artifacts", {})) != EXPECTED_ARTIFACTS:
            failures.append(f"redistributed {mid} artifact keys {sorted(sample.get('artifacts', {}))} != {sorted(EXPECTED_ARTIFACTS)}")
        # reuse the same owner-302 / public-404 assertions as the restricted sample
        for artifact in sorted(sample.get("artifacts", {})):
            p_status, _ = _status_or_presigned(args.api, f"/{PROVIDER}/matches/{mid}/{artifact}", args.public_token)
            if p_status != 404:
                failures.append(f"public {mid}/{artifact}: expected 404, got {p_status}")
        if mid in public_ids:
            failures.append(f"redistributed id {mid} visible to public token")
    else:
        print("note: no redistributed matches present (open-data load not yet run)")
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest src/tests/test_verify_statsbomb_load.py -v`
Expected: PASS.

- [ ] **Step 5: Lint & type-check**

Run: `ruff check scripts/verify_statsbomb_load.py && pyright scripts/verify_statsbomb_load.py`
Expected: clean.

---

### Task 7: ADR 0012, documentation, and pyproject

Implements spec §6 (Changed), §9 (ADR impact). No code — docs + config, so its "test" is the full suite plus the docs-consistency checks CI already runs.

**Files:**
- Create: `docs/decisions/0012-statsbomb-open-data-second-source-family.md`
- Modify: `docs/decisions/README.md` (index table), `CLAUDE.md`, `README.md`, `ARCHITECTURE.md`, `docs/api-reference.md`, `CHANGELOG.md`, `pyproject.toml`

- [ ] **Step 1: Write ADR 0012**

Create `docs/decisions/0012-statsbomb-open-data-second-source-family.md` recording D-1 (same slug, `provenance="redistributed"`, new `redistributed`+`private` combination), D-2 (faithful gzip JSON; Parquet considered and rejected with the measured ~80 MB figure), D-5 (same-tier skip-and-report), D-7 (single-user owner-tier licence basis). Status **Accepted**, Date 2026-09-06. `See Also`: ADR 0009, ADR 0010, ADR 0011, and the spec. Follow the section structure of `docs/decisions/0010-faithful-feed-mimicry.md` (Status / Date / Context / Decision / Consequences / Alternatives Considered / See Also).

- [ ] **Step 2: Update the ADR index**

Add the ADR 0012 row to the table in `docs/decisions/README.md`.

- [ ] **Step 3: Update project docs**

- `CLAUDE.md`: in `src/formats/` description add the open-data second family; in `scripts/` list add `upload_statsbomb_open.py` (open-data adapter) alongside the club loader; note `statsbomb` now serves commercial (original) + open (redistributed) at the owner tier.
- `README.md` / `ARCHITECTURE.md` / `docs/api-reference.md`: reflect the new source family and the `redistributed` provenance under `statsbomb`.
- `CHANGELOG.md`: under `## [Unreleased]`, add an `### Added` entry describing the open-data tournament ingest (six tournaments, 292 matches, owner tier, `provenance="redistributed"`, shared-staging extraction, ADR 0012) and the test-count delta.

- [ ] **Step 4: Guard the licensed cache, bump version, add ruff ignore**

- `.gitignore`: add `.sb-open-cache/` under the existing `# Tracking data (never commit raw provider CSVs)` section. The fetched raw StatsBomb JSON is licensed and must never be staged, even if `--cache-dir` is pointed inside the tree (the default cache is `~/.cache/pining-sb-open`, already outside the tree — this is defense-in-depth):
  ```gitignore
  # StatsBomb open-data fetch cache (licensed raw JSON — never commit)
  .sb-open-cache/
  ```
- `pyproject.toml`: `version = "0.6.0"` → `"0.7.0"`; add a per-file ruff ignore for `scripts/upload_statsbomb_open.py` (`S310`) matching the existing verify-script entries. Update `uv.lock` if the version is pinned there (`uv lock` / the project's lock-refresh step).

- [ ] **Step 5: Run the full suite + docs checks**

Run: `pytest -q && ruff check . && ruff format --check . && pyright`
Expected: PASS/clean. (If a CI test asserts the ADR index or CHANGELOG shape, it now sees 0012 and the Unreleased entry.)

---

### Task 8: Final review, real-data dry-run, approval gate, single commit

Pre-commit quality gate (CLAUDE.md project rule) + the commit-discipline gate. This is the ONLY commit.

**Files:** `docs/c4/architecture.dsl` + regenerated `docs/c4/architecture.html` (via the final-review skill / the pinned Graphviz `dot` pipeline), plus staging the whole change.

- [ ] **Step 1: Run the `final-review` skill**

Invoke the `final-review` skill (project rule: non-negotiable before the final commit). It reviews code + docs for drift/consistency and **regenerates the C4 architecture diagram** — which MUST render with Graphviz `dot` (never Smetana), per the C4 instructions in CLAUDE.md. Confirm the assemble step did not hit the 0-entity placeholder guard (proof `dot` ran).

- [ ] **Step 2: Shift-Left full gate**

Run: `ruff check . && ruff format --check . && pyright && pytest -q`
Expected: all clean/green.

- [ ] **Step 3: Real-data dry-run (no S3 writes)**

Run (background, ~2 GB of JSON; the cache defaults **outside** the repo tree at `~/.cache/pining-sb-open`):
```bash
python scripts/upload_statsbomb_open.py --dry-run
```
Expected: `validated 292 match(es)` across the six tournaments and zero errors. This exercises the fetch/select/guard/coherence/reshape/player paths against the real feed with no S3 calls. **Read the counts carefully:** the printed "new player(s)" number is **run-relative** — dry-run seeds `known_ids` empty (§7.3 mandates zero S3 calls), so it counts only intra-run duplicates and **overstates** the true new-player delta versus `--execute` (which seeds `known_ids` from the live commercial catalogue). It is not the catalogue delta.

- [ ] **Step 4: Present the diff and STOP for explicit approval**

Show Karsten the complete file list and `git diff` (code + tests + docs + ADR + spec + plan + C4). Do **not** commit. Wait for an explicit "commit" for this specific change. (Commit-discipline rule: none of "tests are green" / "the plan says commit" counts as approval.)

- [ ] **Step 5: Single commit — only after explicit approval**

On approval, one coherent commit of the fully-tested state. Stage an **explicit path list** — never `git add -A` — so no fetched cache or operator-local file can be committed even if a gitignore entry is missed:
```bash
git add \
  src/formats/statsbomb_open.py src/mock_api/staging.py \
  scripts/upload_statsbomb_open.py scripts/upload_statsbomb_club.py scripts/verify_statsbomb_load.py \
  src/tests/test_statsbomb_open_format.py src/tests/test_upload_statsbomb_open.py \
  src/tests/test_staging.py src/tests/test_verify_statsbomb_load.py src/tests/conftest.py \
  docs/decisions/0012-statsbomb-open-data-second-source-family.md docs/decisions/README.md \
  docs/superpowers/specs/2026-09-06-statsbomb-open-tournaments-360-owner-tier-design.md \
  docs/superpowers/plans/2026-09-06-statsbomb-open-tournaments-360-owner-tier.md \
  docs/c4/architecture.dsl docs/c4/architecture.html docs/api-reference.md \
  CLAUDE.md README.md ARCHITECTURE.md CHANGELOG.md pyproject.toml uv.lock .gitignore
git status   # confirm NOTHING under a cache dir or operator-local path is staged
git commit   # message summarising the open-data second source family
```
Then (also gated) push the branch and open the PR per the normal flow. The live S3 `--execute` upload to dev + `verify_statsbomb_load.py` run are operational steps done around shipping (owner token + `AWS_PROFILE=devops-agent`, bucket `karstenskyt-pining-for-the-data`), not part of this commit.

---

## Self-Review

**Spec coverage:**
- §1.1 licence basis → Task 7 ADR 0012 (D-7), constants in Task 4 (`SOURCE_LICENCE`).
- §3 D-1 (same slug, redistributed) → Task 5 `upload_game(provenance="redistributed")` + test; ADR Task 7.
- §3 D-2 (gzip JSON, no Parquet) → Task 1/5 staging; ADR Task 7.
- §3 D-3 (second family, shared path) → Tasks 1–5.
- §4.1 full field-source map (SBO-SPEC-01) → Task 2 `build_metadata_open` + the `test_metadata_key_set_matches_commercial` / `test_richer_fields_all_preserved` / `test_metadata_triplet_read_from_nested_object` tests.
- §5.1 players reused → Task 5 (`players_from_lineups`).
- §5.2 skip-and-report + intra-run (SBO-SPEC-04) → Task 3 `partition_new_players` + Task 5 `known_ids` mutation + `test_skips_known_player_uploads_only_new` / `test_intra_run_dedup_second_match_skips_shared_id`.
- §6.3 fetch/cache → Task 4.
- §6.4 selection + transposed-pair guard (SBO-SPEC-03) → Task 3 `select_ingestible_matches` + `test_select_raises_on_transposed_pair`.
- §6.5 shared staging (SBO-SPEC-02) → Task 1.
- §7.1 pre-flight → Task 5 (`assert_delivery_coherent` reused) + `test_orphan_frame_blocks_upload`.
- §7 reused-helper rows (sparse `dob`/`height`→null + no-name-split, missing-`match_date` raises, str id types, order-independence skip) → field-level **open-path** assertions in `test_statsbomb_open_format.py` (added per impl-review SBO-IMPL-01, closing the §7 table on the open path).
- §7.2 freeze-frame privacy → documentation-only claim (spec §7.2 / ADR 0012); no code.
- §7.3 full-corpus dry-run (zero S3) → Task 8 Step 3.
- §8 verify + cross-repo → Task 6; luxury-lakehouse note is documentation (ADR/spec), no code.
- §9 ADR → Task 7.

**Placeholder scan:** every code/test step carries real code. The verify test import line is flagged as "match the existing pattern" — that is a real, bounded instruction, not a TODO.

**Type consistency:** `stage_artifacts` signature identical in Task 1 (definition) and Task 5 (call). `partition_new_players` returns `(list, list[str])` in Task 3 and is consumed that way in Task 5. `upload_open_match` returns `(str, int, int)` in its interface and every test asserts that shape. `build_metadata_open` / `select_ingestible_matches` / `TOURNAMENTS` / `OPEN_ARTIFACT_SPECS` names match between `formats/statsbomb_open.py` and their importers.

---

## Execution Handoff

Two execution options:

1. **Subagent-Driven (recommended)** — a fresh subagent per task, two-stage review between tasks, fast iteration. (Per the project routing rule, implementation subagents run on `opus`.)
2. **Inline Execution** — execute tasks in this session with checkpoints.

Per your standing rules this plan already differs from the skill default in two ways: **no per-task commits** (single approved commit in Task 8) and **feature branch, not worktree**. Which execution approach do you want — and note this plan will first go to an independent review, per your instruction.
