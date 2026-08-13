# StatsBomb Commercial 360 Owner-Tier Provider — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve one commercial StatsBomb 360 match (events + freeze frames + roster + metadata) at the owner tier under a new `statsbomb` provider, in the provider's own published shape.

**Architecture:** A pure reader (`src/formats/statsbomb.py`) turns the delivered archive into canonical dicts — detecting and de-pivoting pandas column-orient dumps, joining the competition row, resolving team ids, and re-nesting to StatsBomb's real match shape. An upload script runs fail-loud pre-flight checks, stages four role-aligned artifacts, and delegates to the existing `upload_game` / `upload_players`. No Terraform or Lambda change — a provider is an S3 prefix.

**Tech Stack:** Python 3.12, pydantic v2 (canonical models), boto3 (S3, via the existing upload modules), pytest, ruff, pyright.

**Spec:** `docs/superpowers/specs/2026-08-12-statsbomb-commercial-360-owner-tier-design.md` (revision 3). Section references below (§) point at it.

## Global Constraints

- Python 3.12+; ruff line-length 120; pyright basic mode.
- **Never invent a value.** Fields absent from the export are emitted as `null` (§4.3, D-4). Structure may be reconstructed; values may not.
- **Inference from inside the delivery only** (D-5). Never look a value up in the public StatsBomb catalogue.
- **Fail loud, never loosen.** Every ambiguity raises. Do not add fuzzy/substring/case-insensitive matching anywhere (§4.2.1, §4.2.2).
- **No `--force` flag** on the upload script (§7.1).
- **No bare `assert` in `scripts/`.** ruff selects `S` (flake8-bandit) globally and `S101` is ignored only for `src/tests/**`. Scripts accumulate a `failures: list[str]` and `sys.exit(main())` — the pattern every existing verify script uses. `python -O` would strip asserts and make a verifier pass having checked nothing.
- Provider slug `statsbomb`; `visibility="private"`; `provenance="original"`; source name `StatsBomb`; source licence `Restricted; redistribution not permitted`.
- **No real club data in the repo.** All test fixtures are synthetic — invented ids and names. No licensed `(id → entity)` tuple may be committed.
- Ids are cast to `str` at the reader boundary — pydantic v2 does not coerce `int` → `str` (§6.2).
- Never run `git commit`. The user commits, once, on their own explicit approval.

## File Structure

| Path | Responsibility |
|---|---|
| `src/formats/statsbomb.py` | **Create.** Pure reader: JSON loading with a non-finite guard, shape detection, de-pivot, competition join, team-id resolution, metadata re-nest, player mapping, coherence assertions. No S3, no network. |
| `scripts/upload_statsbomb_club.py` | **Create.** Pre-flight → stage → `upload_game` + `upload_players`. Owns all I/O. |
| `scripts/verify_statsbomb_load.py` | **Create.** Post-upload verification against the live API, owner **and** public token. |
| `src/tests/conftest.py` | **Modify.** Shared synthetic-bundle fixtures (this repo's established home for shared test machinery — `load_script` already lives there). |
| `src/tests/test_statsbomb_format.py` | **Create.** Reader unit tests (Tasks 1–6). |
| `src/tests/test_upload_statsbomb.py` | **Create.** Staging + pre-flight + upload-wiring tests (Task 7). |
| `docs/decisions/0010-faithful-feed-mimicry.md` | **Create.** ADR for D-2 / D-4 / D-5. |
| `docs/decisions/README.md` | **Modify.** Add the 0010 row to the index table. |
| `pyproject.toml` | **Modify.** Ruff per-file-ignore for the verify script. |
| `CLAUDE.md`, `README.md`, `docs/api-reference.md`, `docs/c4/architecture.dsl` (+ regenerated `.html`) | **Modify.** Provider documentation. |

Synthetic bundles are built in `tmp_path` from **conftest fixtures**, so no test module imports another. No new files in `src/tests/fixtures/`.

Each task's `Expected: PASS (N tests)` line is the **cumulative** count for `src/tests/test_statsbomb_format.py` after that task, reconciled to the shipped suite. The code blocks below are abridged excerpts of the tests actually written, so a block shows fewer cases than the count it produces.

---

### Task 1: Reader scaffold — JSON loading, shape detection, de-pivot

Implements §4.1. The de-pivot is lossless; the discriminator must be a rule, not an accident of one file.

**Files:**
- Create: `src/formats/statsbomb.py`
- Test: `src/tests/test_statsbomb_format.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `SOURCE_FILES: dict[str, str]`, `ARTIFACT_SPECS: tuple[tuple[str, str, str], ...]` (role, source filename, staged filename), `STAGED_METADATA_FILENAME: str`
  - `load_json(path: Path) -> Any` — raises `ValueError` on bare `NaN`/`Infinity`.
  - `is_column_orient(payload: object) -> bool`
  - `depivot(payload: dict, expected_key: str) -> list[dict]`
  - `normalise_records(payload: object, expected_key: str) -> list[dict]` — the durable entry point; accepts feed arrays, single raw objects, and column-orient dumps.

- [ ] **Step 1: Write the failing tests**

Create `src/tests/test_statsbomb_format.py`:

```python
"""Tests for src/formats/statsbomb.py (pure reader — no S3, no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from formats import statsbomb as sb


class TestShapeDetection:
    def test_column_orient_dump_is_detected(self) -> None:
        payload = {"match_id": {"7": 9999999}, "match_date": {"7": "2026-01-01"}}
        assert sb.is_column_orient(payload) is True

    def test_raw_feed_object_is_not_detected(self) -> None:
        # A real match object: values are scalars and mixed-shape dicts.
        payload = {"match_id": 9999999, "competition": {"competition_id": 1}}
        assert sb.is_column_orient(payload) is False

    def test_uniform_subobjects_with_non_integer_keys_are_not_detected(self) -> None:
        # THE false-positive case: every value is a same-keyed dict, but the
        # shared keys are field names, not a DataFrame index.
        payload = {
            "home_team": {"id": 1, "name": "Alpha"},
            "away_team": {"id": 2, "name": "Beta"},
        }
        assert sb.is_column_orient(payload) is False

    def test_single_column_is_not_detected(self) -> None:
        assert sb.is_column_orient({"match_id": {"7": 9999999}}) is False

    def test_list_is_not_detected(self) -> None:
        assert sb.is_column_orient([{"match_id": 9999999}]) is False


class TestDepivot:
    def test_single_row_yields_one_record_value_for_value(self) -> None:
        payload = {
            "match_id": {"7": 9999999},
            "match_date": {"7": "2026-01-01"},
            "home_score": {"7": 2.0},
        }
        assert sb.depivot(payload, "match_id") == [
            {"match_id": 9999999, "match_date": "2026-01-01", "home_score": 2.0}
        ]

    def test_multiple_rows_are_ordered_by_integer_index(self) -> None:
        payload = {"competition_id": {"10": 50, "2": 49}, "season_name": {"10": "2027", "2": "2026"}}
        assert sb.depivot(payload, "competition_id") == [
            {"competition_id": 49, "season_name": "2026"},
            {"competition_id": 50, "season_name": "2027"},
        ]

    def test_missing_expected_key_raises(self) -> None:
        payload = {"foo": {"7": 1}, "bar": {"7": 2}}
        with pytest.raises(ValueError, match="match_id"):
            sb.depivot(payload, "match_id")


class TestNormaliseRecords:
    def test_feed_array_passes_through_unchanged(self) -> None:
        records = [{"match_id": 9999999, "competition": {"competition_id": 1}}]
        assert sb.normalise_records(records, "match_id") == records

    def test_feed_array_missing_expected_key_raises(self) -> None:
        # The RAW path is the one §4.1's durability claim is about, so it must
        # carry the post-condition too — not just the de-pivot branch.
        with pytest.raises(ValueError, match="match_id"):
            sb.normalise_records([{"competition": {"competition_id": 1}}], "match_id")

    def test_single_raw_object_is_wrapped(self) -> None:
        obj = {"match_id": 9999999, "competition": {"competition_id": 1}}
        assert sb.normalise_records(obj, "match_id") == [obj]

    def test_column_orient_is_transposed(self) -> None:
        payload = {"match_id": {"7": 9999999}, "match_date": {"7": "2026-01-01"}}
        assert sb.normalise_records(payload, "match_id") == [
            {"match_id": 9999999, "match_date": "2026-01-01"}
        ]

    def test_unrecognised_object_raises(self) -> None:
        with pytest.raises(ValueError, match="unrecognised payload shape"):
            sb.normalise_records({"competition": {"competition_id": 1}}, "match_id")


class TestLoadJson:
    def test_bare_nan_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text('{"attendance": NaN}', encoding="utf-8")
        with pytest.raises(ValueError, match="non-finite"):
            sb.load_json(path)

    def test_null_is_fine(self, tmp_path: Path) -> None:
        path = tmp_path / "ok.json"
        path.write_text('{"attendance": null}', encoding="utf-8")
        assert sb.load_json(path) == {"attendance": None}


class TestArtifactSpecs:
    def test_source_filenames_are_derived_from_source_files(self) -> None:
        # A rename in SOURCE_FILES must not be able to half-apply.
        for _role, source_name, _staged in sb.ARTIFACT_SPECS:
            assert source_name in sb.SOURCE_FILES.values()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest src/tests/test_statsbomb_format.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'formats.statsbomb'`

- [ ] **Step 3: Write the implementation**

Create `src/formats/statsbomb.py`:

```python
"""StatsBomb commercial 360 bundle reader (club file-drop distribution).

Pure, I/O-light reader for the owner-tier restricted StatsBomb data. Turns the
delivered archive into canonical dicts: detects and de-pivots pandas
column-orient dumps, joins the competition row, resolves team ids from events,
and re-nests the match record into StatsBomb's real feed shape.

Structure may be reconstructed; values are never invented. See
docs/superpowers/specs/2026-08-12-statsbomb-commercial-360-owner-tier-design.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

# Source filenames as delivered.
SOURCE_FILES: dict[str, str] = {
    "events": "events.json",
    "frames": "frames.json",
    "lineups": "lineups.json",
    "matches": "matches.json",
    "competitions": "competitions.json",
}

# (role_key, source_key, staged_filename) for artifacts copied verbatim.
# `metadata` is NOT here — it is synthesised by build_metadata().
# upload_game derives the artifact key from the staged stem (name.split(".", 1)[0]).
_ARTIFACT_ROLES: tuple[tuple[str, str, str], ...] = (
    ("events", "events", "events.json.gz"),
    ("freeze_frames", "frames", "freeze_frames.json.gz"),
    ("roster", "lineups", "roster.json"),
)

# (role_key, source_filename, staged_filename). The source filename is resolved
# through SOURCE_FILES so a rename cannot half-apply.
ARTIFACT_SPECS: tuple[tuple[str, str, str], ...] = tuple(
    (role, SOURCE_FILES[source_key], staged) for role, source_key, staged in _ARTIFACT_ROLES
)

STAGED_METADATA_FILENAME = "metadata.json"

_INT_INDEX_RE = re.compile(r"^-?\d+$")


def _reject_constant(token: str) -> NoReturn:
    """Refuse bare NaN/Infinity: valid to Python's json, rejected by JSON.parse."""
    raise ValueError(
        f"non-finite JSON token {token!r} in payload — a strict JSON parser would reject "
        f"the served artifact. Normalise it to null at the producer."
    )


def load_json(path: Path) -> Any:
    """Load JSON, refusing bare NaN/Infinity tokens (spec §4.1)."""
    with path.open(encoding="utf-8") as f:
        return json.load(f, parse_constant=_reject_constant)


def is_column_orient(payload: object) -> bool:
    """True if `payload` is a pandas column-orient dump (spec §4.1).

    ALL of: an object, >= 2 keys, every value an object, all values sharing one
    identical non-empty key set, and every key of that set an integer-like string
    (the DataFrame index). The integer-index requirement is what makes this a rule
    rather than an accident — without it, an object whose values happen to be
    uniformly-shaped sub-objects would be transposed into garbage.
    """
    if not isinstance(payload, dict) or len(payload) < 2:
        return False
    key_sets: list[frozenset[str]] = []
    for value in payload.values():
        if not isinstance(value, dict):
            return False
        key_sets.append(frozenset(value))
    shared = key_sets[0]
    if not shared or any(ks != shared for ks in key_sets):
        return False
    return all(isinstance(k, str) and _INT_INDEX_RE.match(k) for k in shared)


def _require_key(records: list[dict], expected_key: str) -> list[dict]:
    """Post-condition: every record carries the key its file is keyed on.

    Applied on EVERY branch of normalise_records, not just the de-pivot. §4.1's
    durability claim is about the raw path, so leaving that path unchecked would
    put the post-condition everywhere except where it is most needed.
    """
    for record in records:
        if not isinstance(record, dict) or record.get(expected_key) is None:
            raise ValueError(
                f"record is missing required key {expected_key!r} — "
                f"the payload is not the file this reader expected"
            )
    return records


def depivot(payload: dict, expected_key: str) -> list[dict]:
    """Transpose a column-orient dump back to records. Lossless: only the container changes."""
    if not is_column_orient(payload):
        raise ValueError("payload is not a column-orient dump")
    index = sorted(next(iter(payload.values())).keys(), key=int)
    records = [{col: values.get(i) for col, values in payload.items()} for i in index]
    return _require_key(records, expected_key)


def normalise_records(payload: object, expected_key: str) -> list[dict]:
    """Return records from a feed array, a single raw object, or a column-orient dump.

    The durable entry point: a future fully-raw delivery flows through here with no
    flag and no edit.
    """
    if isinstance(payload, list):
        return _require_key(payload, expected_key)
    if isinstance(payload, dict):
        if is_column_orient(payload):
            return depivot(payload, expected_key)
        if expected_key in payload:
            return _require_key([payload], expected_key)
    raise ValueError(f"unrecognised payload shape for a file keyed on {expected_key!r}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest src/tests/test_statsbomb_format.py -v`
Expected: PASS (18 tests)

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check src/formats/statsbomb.py src/tests/test_statsbomb_format.py && uv run ruff format --check src/formats/statsbomb.py src/tests/test_statsbomb_format.py && uv run pyright src/formats/statsbomb.py`
Expected: no errors.

---

### Task 2: Competition join

Implements §4.2.1. One of the two tests the spec calls load-bearing — the fixture is engineered so a naive row-0 implementation passes structurally and fails on value.

**Files:**
- Modify: `src/formats/statsbomb.py`
- Test: `src/tests/test_statsbomb_format.py`

**Interfaces:**
- Consumes: `normalise_records` (Task 1).
- Produces: `join_competition(competitions: list[dict], match: dict) -> dict`

- [ ] **Step 1: Write the failing tests**

Append to `src/tests/test_statsbomb_format.py`:

```python
def _competitions_multi() -> list[dict]:
    """Three entitlement rows. Row 0 is deliberately the WRONG one.

    Local to this module: these are adversarial variants, not the canonical
    fixture that conftest provides.
    """
    return [
        {"competition_id": 11, "country_name": "Wakanda", "competition_name": "Vibranium League",
         "season_id": 900, "season_name": "2025"},
        {"competition_id": 22, "country_name": "Arendelle", "competition_name": "Ice Cup",
         "season_id": 901, "season_name": "2026"},
        {"competition_id": 33, "country_name": "Wakanda", "competition_name": "Vibranium League",
         "season_id": 902, "season_name": "2026"},
    ]


class TestJoinCompetition:
    def test_joins_on_rendered_string_and_season_not_row_zero(self) -> None:
        match = {"competition": "Wakanda - Vibranium League", "season": "2026"}
        joined = sb.join_competition(_competitions_multi(), match)
        # Row 0 shares the competition string; row 1 shares the season. Only row 2 is right.
        assert joined["competition_id"] == 33
        assert joined["season_id"] == 902

    def test_season_compared_as_string_when_source_is_numeric(self) -> None:
        rows = [{"competition_id": 33, "country_name": "Wakanda", "competition_name": "Vibranium League",
                 "season_id": 902, "season_name": 2026}]
        match = {"competition": "Wakanda - Vibranium League", "season": "2026"}
        assert sb.join_competition(rows, match)["competition_id"] == 33

    def test_no_match_raises(self) -> None:
        match = {"competition": "Atlantis - Trident Cup", "season": "2026"}
        with pytest.raises(ValueError, match="matched 0 row"):
            sb.join_competition(_competitions_multi(), match)

    def test_multiple_matches_raise(self) -> None:
        rows = _competitions_multi() + [
            {"competition_id": 44, "country_name": "Wakanda", "competition_name": "Vibranium League",
             "season_id": 903, "season_name": "2026"},
        ]
        match = {"competition": "Wakanda - Vibranium League", "season": "2026"}
        with pytest.raises(ValueError, match="matched 2 row"):
            sb.join_competition(rows, match)

    def test_missing_join_fields_raise(self) -> None:
        with pytest.raises(ValueError, match="competition"):
            sb.join_competition(_competitions_multi(), {"season": "2026"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest src/tests/test_statsbomb_format.py::TestJoinCompetition -v`
Expected: FAIL with `AttributeError: module 'formats.statsbomb' has no attribute 'join_competition'`

- [ ] **Step 3: Write the implementation**

Append to `src/formats/statsbomb.py`:

```python
def _rendered_competition(row: dict) -> str:
    """statsbombpy renders the competition column as "{country_name} - {competition_name}"."""
    return f"{row.get('country_name')} - {row.get('competition_name')}"


def join_competition(competitions: list[dict], match: dict) -> dict:
    """Return the single competitions row belonging to `match` (spec §4.2.1).

    The match row carries no competition_id/season_id — that is precisely why the
    competitions file is needed — so the join is on statsbombpy's rendered
    "{country} - {competition}" string plus the season name.

    This key is a TOOLING CONVENTION, not a feed contract. If a future delivery
    stops matching, re-derive the key. NEVER loosen the comparison: substring,
    case-insensitive or fuzzy matching converts a fail-loud stop into a silent
    wrong-competition attachment, which is the exact failure this guard prevents.
    """
    want_competition = match.get("competition")
    want_season = match.get("season")
    if not want_competition or want_season is None:
        raise ValueError("match row lacks 'competition' and/or 'season' — cannot join the competitions file")

    hits = [
        row
        for row in competitions
        if _rendered_competition(row) == want_competition and str(row.get("season_name")) == str(want_season)
    ]
    if len(hits) != 1:
        raise ValueError(
            f"competition join matched {len(hits)} rows for "
            f"{want_competition!r} / season {want_season!r}; expected exactly 1. "
            f"Re-derive the join key — do not loosen the comparison."
        )
    return hits[0]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest src/tests/test_statsbomb_format.py -v`
Expected: PASS (23 tests)

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check src/formats/statsbomb.py src/tests/test_statsbomb_format.py && uv run pyright src/formats/statsbomb.py`
Expected: no errors.

---

### Task 3: Home/away resolution and per-team gender

Implements §4.2.2. The second load-bearing test — an inverted fixture is indistinguishable from correct downstream, so the test asserts non-inversion by value.

**Files:**
- Modify: `src/formats/statsbomb.py`
- Test: `src/tests/test_statsbomb_format.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `resolve_team_ids(events: list[dict], match: dict) -> tuple[int, int]` — returns `(home_id, away_id)`.
  - `team_gender(lineups: list[dict], team_id: int) -> str | None`

- [ ] **Step 1: Write the failing tests**

Append to `src/tests/test_statsbomb_format.py`:

```python
def _events_two_teams() -> list[dict]:
    """Away team appears FIRST, so any order-based rule inverts. Local: adversarial variant."""
    return [
        {"id": "e1", "team": {"id": 90007, "name": "Beta United"}},
        {"id": "e2", "team": {"id": 90001, "name": "Alpha City"}},
        {"id": "e3", "team": {"id": 90001, "name": "Alpha City"}},
    ]


class TestResolveTeamIds:
    def test_home_is_home_not_first_seen(self) -> None:
        match = {"home_team": "Alpha City", "away_team": "Beta United"}
        home_id, away_id = sb.resolve_team_ids(_events_two_teams(), match)
        # First team in the event stream is the AWAY side — an order-based
        # implementation would return (90007, 90001) here.
        assert (home_id, away_id) == (90001, 90007)

    def test_unmatched_name_raises(self) -> None:
        match = {"home_team": "Alpha City FC", "away_team": "Beta United"}
        with pytest.raises(ValueError, match="home_team"):
            sb.resolve_team_ids(_events_two_teams(), match)

    def test_same_name_two_ids_raises(self) -> None:
        events = _events_two_teams() + [{"id": "e4", "team": {"id": 999, "name": "Alpha City"}}]
        match = {"home_team": "Alpha City", "away_team": "Beta United"}
        with pytest.raises(ValueError, match="expected exactly 1"):
            sb.resolve_team_ids(events, match)

    def test_both_sides_same_id_raises(self) -> None:
        events = [{"id": "e1", "team": {"id": 90001, "name": "Alpha City"}}]
        match = {"home_team": "Alpha City", "away_team": "Alpha City"}
        with pytest.raises(ValueError, match="same team id"):
            sb.resolve_team_ids(events, match)


class TestTeamGender:
    def test_resolves_per_team_not_delivery_wide(self) -> None:
        # The feed models gender per TEAM, so a delivery-wide value would be wrong
        # for a fixture whose two squads differ.
        lineups = [
            {"team_id": 90001, "lineup": [{"player_gender": "female"}, {"player_gender": "female"}]},
            {"team_id": 90007, "lineup": [{"player_gender": "male"}]},
        ]
        assert sb.team_gender(lineups, 90001) == "female"
        assert sb.team_gender(lineups, 90007) == "male"

    def test_disagreement_within_a_team_yields_none(self) -> None:
        lineups = [{"team_id": 90001, "lineup": [{"player_gender": "female"}, {"player_gender": "male"}]}]
        assert sb.team_gender(lineups, 90001) is None

    def test_absent_or_unknown_team_yields_none(self) -> None:
        assert sb.team_gender([{"team_id": 90001, "lineup": [{}]}], 90001) is None
        assert sb.team_gender([{"team_id": 90001, "lineup": [{"player_gender": "female"}]}], 999) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest src/tests/test_statsbomb_format.py::TestResolveTeamIds -v`
Expected: FAIL with `AttributeError: ... has no attribute 'resolve_team_ids'`

- [ ] **Step 3: Write the implementation**

Append to `src/formats/statsbomb.py`:

```python
def resolve_team_ids(events: list[dict], match: dict) -> tuple[int, int]:
    """Resolve (home_team_id, away_team_id) from events by EXACT name equality (spec §4.2.2).

    An inverted fixture is strictly worse than a null: it is indistinguishable from
    correct downstream and silently corrupts every home/away-dependent computation.
    So: exactly one id per side, and the two sides must differ — anything else raises.

    No fuzzy or normalised matching. A near-match is exactly the case that should stop
    the upload rather than guess. Lineup ordering is NOT a fallback: the delivered
    lineups list the away team first, so ordering carries no home/away signal.
    """
    by_name: dict[str, set[int]] = {}
    for event in events:
        team = event.get("team")
        if isinstance(team, dict) and team.get("name") is not None and team.get("id") is not None:
            by_name.setdefault(team["name"], set()).add(team["id"])

    resolved: list[int] = []
    for side in ("home_team", "away_team"):
        name = match.get(side)
        ids = by_name.get(name, set()) if name is not None else set()
        if len(ids) != 1:
            raise ValueError(
                f"{side} {name!r} resolved to {len(ids)} team id(s) in events; expected exactly 1"
            )
        resolved.append(next(iter(ids)))

    if resolved[0] == resolved[1]:
        raise ValueError(f"home and away resolved to the same team id ({resolved[0]})")
    return resolved[0], resolved[1]


def team_gender(lineups: list[dict], team_id: int) -> str | None:
    """The single gender shared by one team's players, else None.

    StatsBomb records gender per player and the match object carries it PER TEAM,
    so this resolves per team rather than delivery-wide. A team whose players
    disagree — or an unknown team — yields None rather than a guess.
    """
    genders = {
        player.get("player_gender")
        for team in lineups
        if team.get("team_id") == team_id
        for player in team.get("lineup", [])
        if player.get("player_gender")
    }
    return next(iter(genders)) if len(genders) == 1 else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest src/tests/test_statsbomb_format.py -v`
Expected: PASS (30 tests)

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check src/formats/statsbomb.py src/tests/test_statsbomb_format.py && uv run pyright src/formats/statsbomb.py`
Expected: no errors.

---

### Task 4: Shared fixtures, re-nest to feed shape, and the index entry

Implements §4.2.3, §4.4 and §4.5. Nulls are asserted per field, and the complete top-level key set is asserted so a forgotten feed field cannot slip through.

**Files:**
- Modify: `src/tests/conftest.py`, `src/formats/statsbomb.py`
- Test: `src/tests/test_statsbomb_format.py`

**Interfaces:**
- Consumes: `join_competition` (Task 2), `resolve_team_ids` / `team_gender` (Task 3).
- Produces:
  - conftest fixtures `sb_match_row`, `sb_competition_row`, `sb_events`, `sb_lineups`, `sb_bundle_dir`
  - `build_metadata(match, competition, home_id, away_id, home_gender, away_gender) -> dict`
  - `MatchInfo` frozen dataclass: `match_id: str`, `date: str`, `home: str`, `away: str`
  - `match_info(metadata: dict) -> MatchInfo`

- [ ] **Step 1: Add shared synthetic fixtures to conftest.py**

Append to `src/tests/conftest.py`. These are the canonical synthetic bundle pieces, shared by the reader tests and the upload tests — conftest is where this repo already puts shared test machinery, so no test module needs to import another.

First add two imports to the top of the file, alongside the existing ones:

```python
import json
from collections.abc import Callable
```

Then append the fixtures. They carry return annotations to match the existing ones (`def fixtures_dir() -> Path:`):

```python
# --- StatsBomb synthetic bundle (spec 2026-08-12) -------------------------------
# All ids and names are invented. No licensed (id -> entity) tuple is committed.


@pytest.fixture
def sb_match_row() -> dict:
    """A de-pivoted match row as statsbombpy flattens it."""
    return {
        "match_id": 9999999,
        "match_date": "2026-01-01",
        "kick_off": "20:00:00.000",
        "competition": "Wakanda - Vibranium League",
        "season": "2026",
        "home_team": "Alpha City",
        "away_team": "Beta United",
        "home_score": 2.0,
        "away_score": 1.0,
        "attendance": None,
        "behind_closed_doors": False,
        "neutral_ground": False,
        "collection_status": "Complete",
        "play_status": "Normal",
        "match_status": "available",
        "match_status_360": "available",
        "last_updated": "2026-01-02T00:00:00.000000",
        "last_updated_360": "2026-01-02T00:00:00.000000",
        "match_week": 15,
        "competition_stage": "Regular Season",
        "stadium": "Alpha Arena",
        "referee": "Sam Whistle",
        "home_managers": "Dana Coach",
        "away_managers": "Robin Boss",
        "data_version": "1.1.0",
        "shot_fidelity_version": "2",
        "xy_fidelity_version": "2",
    }


@pytest.fixture
def sb_competition_row() -> dict:
    return {
        "competition_id": 33,
        "country_name": "Wakanda",
        "competition_name": "Vibranium League",
        "season_id": 902,
        "season_name": "2026",
    }


@pytest.fixture
def sb_events() -> list[dict]:
    """Coherent event stream: two periods, a Half End, exactly two teams."""
    return [
        {"id": "e1", "period": 1, "type": {"name": "Pass"}, "team": {"id": 90001, "name": "Alpha City"}},
        {"id": "e2", "period": 2, "type": {"name": "Pass"}, "team": {"id": 90007, "name": "Beta United"}},
        {"id": "e3", "period": 2, "type": {"name": "Half End"}, "team": {"id": 90001, "name": "Alpha City"}},
    ]


@pytest.fixture
def sb_lineups() -> list[dict]:
    """A WHOLE delivery: both squads, away block first as the real feed orders them.

    Both team blocks are present deliberately. With only one, `team_gender` resolves
    to None for the missing side and `home_team_gender` is null in every end-to-end
    assertion without anything noticing.

    Every value here is invented — ids, names, dates, body metrics, jersey numbers,
    country and the proprietary rating block. None is derived from any delivery.
    """
    return [
        {
            "team_id": 90007,
            "team_name": "Beta United",
            "lineup": [
                {
                    "player_id": 77001,
                    "player_name": "Rosalind Amara Fenwick",
                    "player_nickname": "Roz Fenwick",
                    "birth_date": "2001-02-03",
                    "player_gender": "female",
                    "player_height": 168.5,
                    "player_weight": 64.125,
                    "jersey_number": 99,
                    "country": {"id": 993, "name": "Wakanda"},
                    "skills": {"HOPS": {"rating": 0.5137, "raw_rating": 123.4567}},
                    "stats": [],
                    "positions": [
                        {"position_id": 91, "position": "Center Forward",
                         "from": "00:45:00.000", "from_period": 2},
                        {"position_id": 92, "position": "Right Wing",
                         "from": "00:00:00.000", "from_period": 1},
                    ],
                },
                {
                    # No nickname — the full name must land in `nickname`, unsplit.
                    "player_id": 77004,
                    "player_name": "Ingrid Beatrix Halvorsen",
                    "player_nickname": "",
                    "birth_date": "1999-11-12",
                    "player_gender": "female",
                    "player_height": 181.0,
                    "player_weight": 71.875,
                    "jersey_number": 90,
                    "country": {"id": 993, "name": "Wakanda"},
                    "positions": [],
                },
            ],
        },
        {
            # The HOME side, listed second — see the docstring.
            "team_id": 90001,
            "team_name": "Alpha City",
            "lineup": [
                {
                    "player_id": 77002,
                    "player_name": "Perrin Solveig Ashgrove",
                    "player_nickname": "Perry Ashgrove",
                    "player_gender": "female",
                    "country": {"id": 993, "name": "Wakanda"},
                    "positions": [],
                },
            ],
        },
    ]


@pytest.fixture
def sb_bundle_dir(
    tmp_path: Path,
    sb_match_row: dict,
    sb_competition_row: dict,
    sb_events: list[dict],
    sb_lineups: list[dict],
) -> Callable[..., Path]:
    """Write a complete synthetic StatsBomb bundle; return a factory.

    Calling the returned factory with no arguments writes the canonical bundle and
    returns its root. Pass `overrides={"frames.json": [...]}` to replace one file —
    used by the pre-flight tests to inject an incoherent delivery.
    """

    def _build(overrides: dict[str, object] | None = None) -> Path:
        root = tmp_path / "sb_bundle"
        root.mkdir(parents=True, exist_ok=True)
        payloads: dict[str, object] = {
            "matches.json": {col: {"7": val} for col, val in sb_match_row.items()},
            "competitions.json": {col: {"14": val} for col, val in sb_competition_row.items()},
            "events.json": sb_events,
            "frames.json": [{"event_uuid": "e1"}],
            "lineups.json": sb_lineups,
        }
        payloads.update(overrides or {})
        for name, payload in payloads.items():
            (root / name).write_text(json.dumps(payload), encoding="utf-8")
        return root

    return _build
```

- [ ] **Step 2: Write the failing tests**

Append to `src/tests/test_statsbomb_format.py`:

```python
@pytest.fixture
def built(sb_match_row, sb_competition_row) -> dict:
    return sb.build_metadata(sb_match_row, sb_competition_row, 90001, 90007, "female", "female")


class TestBuildMetadata:
    def test_envelope_is_an_object_not_an_array(self, built: dict) -> None:
        assert isinstance(built, dict)

    def test_top_level_key_set_matches_the_feed_exactly(self, built: dict) -> None:
        # The test that catches a FORGOTTEN feed field. Per-field assertions
        # cannot substitute for it.
        assert set(built) == {
            "match_id", "match_date", "kick_off", "competition", "season",
            "home_team", "away_team", "home_score", "away_score",
            "match_status", "match_status_360", "last_updated", "last_updated_360",
            "metadata", "match_week", "competition_stage", "stadium", "referee",
            "attendance", "behind_closed_doors", "neutral_ground",
            "collection_status", "play_status",
        }

    def test_nested_substructure_key_sets_match_the_feed(self, built: dict) -> None:
        assert set(built["home_team"]) == {
            "home_team_id", "home_team_name", "home_team_gender",
            "home_team_group", "country", "managers",
        }
        assert set(built["away_team"]) == {
            "away_team_id", "away_team_name", "away_team_gender",
            "away_team_group", "country", "managers",
        }
        assert set(built["competition"]) == {"competition_id", "country_name", "competition_name"}
        assert set(built["season"]) == {"season_id", "season_name"}
        assert set(built["stadium"]) == {"id", "name", "country"}
        assert set(built["referee"]) == {"id", "name", "country"}
        assert set(built["competition_stage"]) == {"id", "name"}
        assert set(built["metadata"]) == {
            "data_version", "shot_fidelity_version", "xy_fidelity_version",
        }

    def test_competition_and_season_are_nested(self, built: dict) -> None:
        assert built["competition"] == {
            "competition_id": 33, "country_name": "Wakanda", "competition_name": "Vibranium League",
        }
        assert built["season"] == {"season_id": 902, "season_name": "2026"}

    def test_team_ids_and_gender_are_recovered(self, built: dict) -> None:
        assert built["home_team"]["home_team_id"] == 90001
        assert built["home_team"]["home_team_name"] == "Alpha City"
        assert built["home_team"]["home_team_gender"] == "female"
        assert built["away_team"]["away_team_id"] == 90007

    def test_gender_is_applied_per_side(self, sb_match_row, sb_competition_row) -> None:
        md = sb.build_metadata(sb_match_row, sb_competition_row, 90001, 90007, "female", "male")
        assert md["home_team"]["home_team_gender"] == "female"
        assert md["away_team"]["away_team_gender"] == "male"

    def test_manager_name_present_and_ids_null(self, built: dict) -> None:
        managers = built["home_team"]["managers"]
        assert len(managers) == 1
        assert managers[0]["name"] == "Dana Coach"
        assert managers[0]["id"] is None
        assert managers[0]["nickname"] is None
        assert managers[0]["dob"] is None
        assert managers[0]["country"] is None

    def test_no_manager_yields_empty_list(self, sb_match_row, sb_competition_row) -> None:
        sb_match_row["home_managers"] = None
        md = sb.build_metadata(sb_match_row, sb_competition_row, 90001, 90007, "female", "female")
        assert md["home_team"]["managers"] == []

    def test_non_string_manager_cell_raises(self, sb_match_row, sb_competition_row) -> None:
        # A multi-manager cell cannot be split without inventing a boundary (D-4),
        # so the shape is asserted rather than silently accepted.
        sb_match_row["home_managers"] = ["Dana Coach", "Robin Boss"]
        with pytest.raises(ValueError, match="single name string"):
            sb.build_metadata(sb_match_row, sb_competition_row, 90001, 90007, "female", "female")

    def test_fidelity_block_is_renested(self, built: dict) -> None:
        assert built["metadata"] == {
            "data_version": "1.1.0", "shot_fidelity_version": "2", "xy_fidelity_version": "2",
        }

    def test_scores_are_ints_not_floats(self, built: dict) -> None:
        assert built["home_score"] == 2 and isinstance(built["home_score"], int)
        assert built["away_score"] == 1 and isinstance(built["away_score"], int)

    def test_commercial_only_fields_are_preserved_top_level(self, built: dict) -> None:
        assert built["collection_status"] == "Complete"
        assert built["play_status"] == "Normal"
        assert built["behind_closed_doors"] is False
        assert built["neutral_ground"] is False
        assert built["attendance"] is None

    @pytest.mark.parametrize(
        ("path", "_label"),
        [
            (("home_team", "home_team_group"), "home group"),
            (("home_team", "country"), "home country"),
            (("away_team", "away_team_group"), "away group"),
            (("away_team", "country"), "away country"),
            (("competition_stage", "id"), "stage id"),
            (("stadium", "id"), "stadium id"),
            (("stadium", "country"), "stadium country"),
            (("referee", "id"), "referee id"),
            (("referee", "country"), "referee country"),
        ],
    )
    def test_absent_values_are_null_per_field(self, built: dict, path: tuple[str, ...], _label: str) -> None:
        node: object = built
        for key in path:
            node = node[key]
        assert node is None

    def test_named_substructures_keep_their_names(self, built: dict) -> None:
        assert built["competition_stage"]["name"] == "Regular Season"
        assert built["stadium"]["name"] == "Alpha Arena"
        assert built["referee"]["name"] == "Sam Whistle"


class TestMatchInfo:
    def test_derives_index_fields(self, built: dict) -> None:
        info = sb.match_info(built)
        assert info.match_id == "9999999"
        assert isinstance(info.match_id, str)
        assert info.date == "2026-01-01"
        assert info.home == "Alpha City"
        assert info.away == "Beta United"

    def test_missing_date_raises(self, built: dict) -> None:
        built["match_date"] = None
        with pytest.raises(ValueError, match="match_date"):
            sb.match_info(built)

    def test_empty_date_raises(self, built: dict) -> None:
        built["match_date"] = ""
        with pytest.raises(ValueError, match="match_date"):
            sb.match_info(built)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest src/tests/test_statsbomb_format.py::TestBuildMetadata -v`
Expected: FAIL with `AttributeError: ... has no attribute 'build_metadata'`

- [ ] **Step 4: Write the implementation**

Append to `src/formats/statsbomb.py`:

```python
@dataclass(frozen=True)
class MatchInfo:
    """Index metadata for one match (spec §4.5)."""

    match_id: str
    date: str
    home: str
    away: str


def _int_or_none(value: object) -> int | None:
    """Cast a numeric to int, preserving None. statsbombpy emits scores as floats."""
    return None if value is None else int(value)


def _managers(name: object) -> list[dict]:
    """The feed's managers array. Only the name survived the export; the rest are null.

    A non-string cell (e.g. a list, if statsbombpy ever renders multiple managers)
    is refused rather than accepted: splitting it would invent a boundary the export
    does not assert (D-4), and accepting it silently would emit a compound name as a
    single manager.
    """
    if name is None or name == "":
        return []
    if not isinstance(name, str):
        raise ValueError(
            f"manager cell is {type(name).__name__}, expected a single name string — "
            f"a multi-manager cell cannot be split without inventing a boundary"
        )
    return [{"id": None, "name": name, "nickname": None, "dob": None, "country": None}]


def build_metadata(
    match: dict,
    competition: dict,
    home_id: int,
    away_id: int,
    home_gender: str | None,
    away_gender: str | None,
) -> dict:
    """Re-nest a flattened match row into StatsBomb's real match shape (spec §4.2.3).

    The real match object natively co-locates competition{} and season{}, which is
    what licenses pulling them from the competitions file — this restores what the
    feed contains, it does not invent a composite.

    Fields the export dropped are emitted as null. Nothing is looked up externally.
    """
    return {
        "match_id": _int_or_none(match.get("match_id")),
        "match_date": match.get("match_date"),
        "kick_off": match.get("kick_off"),
        "competition": {
            "competition_id": competition.get("competition_id"),
            "country_name": competition.get("country_name"),
            "competition_name": competition.get("competition_name"),
        },
        "season": {
            "season_id": competition.get("season_id"),
            "season_name": competition.get("season_name"),
        },
        "home_team": {
            "home_team_id": home_id,
            "home_team_name": match.get("home_team"),
            "home_team_gender": home_gender,
            "home_team_group": None,
            "country": None,
            "managers": _managers(match.get("home_managers")),
        },
        "away_team": {
            "away_team_id": away_id,
            "away_team_name": match.get("away_team"),
            "away_team_gender": away_gender,
            "away_team_group": None,
            "country": None,
            "managers": _managers(match.get("away_managers")),
        },
        "home_score": _int_or_none(match.get("home_score")),
        "away_score": _int_or_none(match.get("away_score")),
        "match_status": match.get("match_status"),
        "match_status_360": match.get("match_status_360"),
        "last_updated": match.get("last_updated"),
        "last_updated_360": match.get("last_updated_360"),
        "metadata": {
            "data_version": match.get("data_version"),
            "shot_fidelity_version": match.get("shot_fidelity_version"),
            "xy_fidelity_version": match.get("xy_fidelity_version"),
        },
        "match_week": _int_or_none(match.get("match_week")),
        "competition_stage": {"id": None, "name": match.get("competition_stage")},
        "stadium": {"id": None, "name": match.get("stadium"), "country": None},
        "referee": {"id": None, "name": match.get("referee"), "country": None},
        # Credentialed-endpoint fields the open catalogue does not carry. Genuine
        # feed fields, not export artifacts — preserved at top level.
        "attendance": match.get("attendance"),
        "behind_closed_doors": match.get("behind_closed_doors"),
        "neutral_ground": match.get("neutral_ground"),
        "collection_status": match.get("collection_status"),
        "play_status": match.get("play_status"),
    }


def match_info(metadata: dict) -> MatchInfo:
    """Derive the provider-index entry fields from a built metadata artifact.

    `date` is REQUIRED. apply_filters excludes an empty date from both range
    filters (shared.py:296,299), so a dateless entry is invisible to every
    dateFrom/dateTo query while still returning 200 on the unfiltered list.
    Refuse rather than upload a match that cannot be found by date.
    """
    match_id = metadata.get("match_id")
    date = metadata.get("match_date")
    home = (metadata.get("home_team") or {}).get("home_team_name")
    away = (metadata.get("away_team") or {}).get("away_team_name")

    if match_id is None:
        raise ValueError("metadata lacks match_id")
    if not date:
        raise ValueError("metadata lacks match_date — refusing to upload a match that cannot be found by date")
    if not home or not away:
        raise ValueError("metadata lacks home and/or away team name")
    return MatchInfo(match_id=str(match_id), date=date, home=home, away=away)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest src/tests/test_statsbomb_format.py -v`
Expected: PASS (58 tests)

- [ ] **Step 6: Lint and type-check**

Run: `uv run ruff check src/formats/statsbomb.py src/tests/test_statsbomb_format.py src/tests/conftest.py && uv run pyright src/formats/statsbomb.py`
Expected: no errors.

---

### Task 5: Player mapping

Implements §5.1 and §5.2. The feed has no first/last split and often no nickname, so the full name routes into `nickname`. **No whitespace splitting** — that would invent a boundary the feed does not assert.

**Files:**
- Modify: `src/formats/statsbomb.py`
- Test: `src/tests/test_statsbomb_format.py`

**Interfaces:**
- Consumes: `sb_lineups` fixture (Task 4).
- Produces: `players_from_lineups(lineups: list[dict]) -> list[dict]` — canonical `PlayerRecord` dicts without `visibility`/`updated_at` (added by `upload_players`).

- [ ] **Step 1: Write the failing tests**

Append to `src/tests/test_statsbomb_format.py`:

```python
class TestPlayersFromLineups:
    def test_nickname_used_when_present(self, sb_lineups) -> None:
        assert sb.players_from_lineups(sb_lineups)[0]["nickname"] == "Roz Fenwick"

    def test_full_name_falls_back_into_nickname_unsplit(self, sb_lineups) -> None:
        assert sb.players_from_lineups(sb_lineups)[1]["nickname"] == "Ingrid Beatrix Halvorsen"

    def test_first_and_last_name_are_never_populated(self, sb_lineups) -> None:
        for rec in sb.players_from_lineups(sb_lineups):
            assert rec.get("firstName") is None
            assert rec.get("lastName") is None

    def test_ids_are_strings(self, sb_lineups) -> None:
        for rec in sb.players_from_lineups(sb_lineups):
            assert isinstance(rec["id"], str)

    def test_canonical_scalars_map_verbatim(self, sb_lineups) -> None:
        rec = sb.players_from_lineups(sb_lineups)[0]
        assert rec["dob"] == "2001-02-03"
        assert rec["height"] == 168.5
        assert rec["nationality"] == "Wakanda"

    def test_earliest_spell_wins(self, sb_lineups) -> None:
        # The period-1 spell is listed SECOND; the earliest must still win.
        assert sb.players_from_lineups(sb_lineups)[0]["position"] == "Right Wing"

    def test_empty_positions_yield_none(self, sb_lineups) -> None:
        assert sb.players_from_lineups(sb_lineups)[1]["position"] is None

    def test_position_group_type_never_populated(self, sb_lineups) -> None:
        for rec in sb.players_from_lineups(sb_lineups):
            assert rec.get("positionGroupType") is None

    def test_proprietary_and_excluded_fields_absent(self, sb_lineups) -> None:
        for rec in sb.players_from_lineups(sb_lineups):
            for excluded in ("skills", "player_weight", "weight", "stats", "jersey_number"):
                assert excluded not in rec

    def test_records_validate_against_the_canonical_model(self, sb_lineups) -> None:
        from canonical.models import PlayerRecord

        for rec in sb.players_from_lineups(sb_lineups):
            PlayerRecord.model_validate({**rec, "visibility": "private", "updated_at": "2026-01-01T00:00:00Z"})

    def test_player_without_id_is_skipped(self) -> None:
        lineups = [{"lineup": [{"player_name": "Ghost"}, {"player_id": 7, "player_name": "Real"}]}]
        assert [r["id"] for r in sb.players_from_lineups(lineups)] == ["7"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest src/tests/test_statsbomb_format.py::TestPlayersFromLineups -v`
Expected: FAIL with `AttributeError: ... has no attribute 'players_from_lineups'`

- [ ] **Step 3: Write the implementation**

Append to `src/formats/statsbomb.py`:

```python
def _starting_position(player: dict) -> str | None:
    """The earliest positional spell by (from_period, from). Spec §5.2.

    positions[] is a per-spell array (up to 4 entries). The catalogue records the
    player's STARTING position; the full history stays in the roster artifact.
    """
    spells = player.get("positions") or []
    if not spells:
        return None
    earliest = min(spells, key=lambda s: (s.get("from_period") or 0, s.get("from") or ""))
    return earliest.get("position") or None


def players_from_lineups(lineups: list[dict]) -> list[dict]:
    """Derive canonical PlayerRecord dicts (without visibility/updated_at). Spec §5.1.

    StatsBomb's lineups feed carries NO first/last split — only `player_name` plus an
    often-empty `player_nickname`. Satisfying PlayerRecord's
    ``nickname OR (firstName AND lastName)`` validator via first/last would require
    splitting the full name on whitespace, which invents a boundary the feed does not
    assert and mangles compound surnames. D-4 forbids it, so the full name routes into
    `nickname` and firstName/lastName are never populated.

    Note: upload_players serialises with model_dump(exclude_none=True), so unset
    fields are ABSENT from the served players.json rather than null. A consumer must
    read absence — not a null value — as "this provider asserts no split".
    """
    records: list[dict] = []
    for team in lineups:
        for player in team.get("lineup", []):
            player_id = player.get("player_id")
            if player_id is None:
                continue
            country = player.get("country") or {}
            records.append(
                {
                    "id": str(player_id),
                    "nickname": player.get("player_nickname") or player.get("player_name") or None,
                    "dob": player.get("birth_date") or None,
                    "height": player.get("player_height"),
                    "nationality": country.get("name") or None,
                    "position": _starting_position(player),
                }
            )
    return records
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest src/tests/test_statsbomb_format.py -v`
Expected: PASS (70 tests)

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check src/formats/statsbomb.py src/tests/test_statsbomb_format.py && uv run pyright src/formats/statsbomb.py`
Expected: no errors.

---

### Task 6: Coherence pre-flight and bundle reading

Implements §7.1. Hand-profiling does not survive "re-run, not rewrite", so these become fail-loud assertions. Pure functions here; the upload script calls them (Task 7).

`lineups` is a parameter of the coherence assertion, not just a field of `Bundle`: an empty lineups array and a lineups file naming teams that events never mention are both delivery-level contradictions, and neither is visible to a check that only sees events and frames. `_require_array` likewise checks *elements*, not just the container — `["a", "b"]` is a list, so a container-only check defers the `AttributeError` instead of preventing it.

**Files:**
- Modify: `src/formats/statsbomb.py`
- Test: `src/tests/test_statsbomb_format.py`

**Interfaces:**
- Consumes: `load_json`, `normalise_records` (Task 1), `join_competition` (Task 2), `sb_bundle_dir` fixture (Task 4).
- Produces:
  - `assert_delivery_coherent(events: list[dict], frames: list[dict], lineups: list[dict]) -> None`
  - `Bundle` frozen dataclass: `root: Path`, `match: dict`, `competition: dict`, `events: list[dict]`, `frames: list[dict]`, `lineups: list[dict]`
  - `read_bundle(root: Path) -> Bundle`

- [ ] **Step 1: Write the failing tests**

Append to `src/tests/test_statsbomb_format.py`:

```python
class TestAssertDeliveryCoherent:
    def test_coherent_delivery_passes(self, sb_events, sb_lineups) -> None:
        sb.assert_delivery_coherent(sb_events, [{"event_uuid": "e1"}, {"event_uuid": "e2"}], sb_lineups)

    def test_orphan_frame_raises(self, sb_events, sb_lineups) -> None:
        with pytest.raises(ValueError, match="unknown event id"):
            sb.assert_delivery_coherent(sb_events, [{"event_uuid": "nope"}], sb_lineups)

    def test_uuidless_frame_is_an_orphan_even_when_an_event_lacks_an_id(self, sb_events, sb_lineups) -> None:
        # `None` must not enter event_ids, or a uuid-less frame matches it and
        # slips through the orphan check.
        events = [*sb_events, {"period": 2, "type": {"name": "Pass"}, "team": {"id": 90001, "name": "Alpha City"}}]
        with pytest.raises(ValueError, match="unknown event id"):
            sb.assert_delivery_coherent(events, [{"visible_area": []}], sb_lineups)

    def test_zero_frames_raises(self, sb_events, sb_lineups) -> None:
        # The orphan check passes vacuously on an empty list, so this needs its
        # own guard: 360 frames are the entire point of this provider.
        with pytest.raises(ValueError, match="zero freeze frames"):
            sb.assert_delivery_coherent(sb_events, [], sb_lineups)

    def test_zero_lineups_raises(self, sb_events) -> None:
        # Same vacuous pass as zero frames, one file over: [] satisfies
        # _require_array, stages an empty roster, and skips the player upload —
        # shipping an indexed match with no squad and no error.
        with pytest.raises(ValueError, match="zero lineup entries"):
            sb.assert_delivery_coherent(sb_events, [{"event_uuid": "e1"}], [])

    def test_lineups_and_events_disagreeing_on_teams_raises(self, sb_events, sb_lineups) -> None:
        # A contradiction INSIDE the delivery (ADR 0010 rule 3): one export of one
        # fixture cannot field one pair of teams in events and another in lineups.
        sb_lineups[1]["team_id"] = 90002
        with pytest.raises(ValueError, match="contradicts itself"):
            sb.assert_delivery_coherent(sb_events, [{"event_uuid": "e1"}], sb_lineups)

    def test_lineups_covering_only_one_side_raises(self, sb_events, sb_lineups) -> None:
        # The half-delivery case: a lineups file for the away side only. Every other
        # check passes and home_team_gender silently resolves to null.
        with pytest.raises(ValueError, match="contradicts itself"):
            sb.assert_delivery_coherent(sb_events, [{"event_uuid": "e1"}], sb_lineups[:1])

    def test_single_period_raises(self, sb_events, sb_lineups) -> None:
        with pytest.raises(ValueError, match="period"):
            sb.assert_delivery_coherent([e for e in sb_events if e["period"] == 1], [{"event_uuid": "e1"}], sb_lineups)

    def test_missing_half_end_raises(self, sb_events, sb_lineups) -> None:
        events = [e for e in sb_events if e["type"]["name"] != "Half End"]
        with pytest.raises(ValueError, match="Half End"):
            sb.assert_delivery_coherent(events, [{"event_uuid": "e1"}], sb_lineups)

    def test_wrong_team_count_raises(self, sb_events, sb_lineups) -> None:
        events = [*sb_events, {"id": "e4", "period": 2, "type": {"name": "Pass"}, "team": {"id": 3, "name": "Gamma"}}]
        with pytest.raises(ValueError, match="distinct team"):
            sb.assert_delivery_coherent(events, [{"event_uuid": "e1"}], sb_lineups)

    def test_single_team_raises(self, sb_events, sb_lineups) -> None:
        events = [e for e in sb_events if e["team"]["id"] == 90001]
        with pytest.raises(ValueError, match="distinct team"):
            sb.assert_delivery_coherent(events, [{"event_uuid": "e1"}], sb_lineups)


class TestReadBundle:
    def test_reads_and_joins(self, sb_bundle_dir) -> None:
        bundle = sb.read_bundle(sb_bundle_dir())
        assert bundle.match["match_id"] == 9999999
        assert bundle.competition["competition_id"] == 33
        assert len(bundle.events) == 3
        assert len(bundle.frames) == 1
        assert len(bundle.lineups) == 2

    def test_missing_file_raises(self, sb_bundle_dir) -> None:
        root = sb_bundle_dir()
        (root / "frames.json").unlink()
        with pytest.raises(FileNotFoundError, match=r"frames\.json"):
            sb.read_bundle(root)

    def test_multi_match_dump_raises(self, sb_bundle_dir, sb_match_row) -> None:
        root = sb_bundle_dir(
            overrides={"matches.json": {col: {"7": val, "8": val} for col, val in sb_match_row.items()}}
        )
        with pytest.raises(ValueError, match="one match per bundle"):
            sb.read_bundle(root)

    def test_passthrough_file_that_is_not_an_array_raises(self, sb_bundle_dir) -> None:
        # Keeps the fail-loud story consistent across all five files.
        root = sb_bundle_dir(overrides={"lineups.json": {"not": "an array"}})
        with pytest.raises(ValueError, match=r"lineups\.json is a dict, expected a JSON array"):
            sb.read_bundle(root)

    def test_passthrough_array_of_non_objects_raises(self, sb_bundle_dir) -> None:
        # The container check alone lets this through — it IS a list — and the
        # AttributeError the guard claims to prevent happens later anyway.
        root = sb_bundle_dir(overrides={"events.json": ["a", "b"]})
        with pytest.raises(ValueError, match=r"events\.json\[0\] is a str, expected a JSON object"):
            sb.read_bundle(root)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest src/tests/test_statsbomb_format.py::TestAssertDeliveryCoherent -v`
Expected: FAIL with `AttributeError: ... has no attribute 'assert_delivery_coherent'`

- [ ] **Step 3: Write the implementation**

Append to `src/formats/statsbomb.py`:

```python
def assert_delivery_coherent(events: list[dict], frames: list[dict], lineups: list[dict]) -> None:
    """Fail loud on an incoherent delivery, BEFORE anything is staged (spec §7.1).

    The synthetic-fixture tests prove these helpers work; this run proves THIS
    delivery is coherent. Both are needed.

    A legitimately abnormal delivery (abandoned match, unusual period structure) is
    handled by amending the SPECIFIC assertion that no longer applies, with the
    reason recorded in the operator's delivery note. There is deliberately no
    --force flag: a blanket override is what gets reached for under time pressure,
    and it would disable every check at once.
    """
    if not frames:
        # Without this the orphan check below passes VACUOUSLY on an empty list,
        # and a 360 provider ships a match with no 360 data.
        raise ValueError("delivery contains zero freeze frames — 360 payload is empty or truncated")

    if not lineups:
        # Same vacuous-pass hazard, one file over: an empty lineups array satisfies
        # _require_array, stages an empty roster artifact, and skips the player
        # upload entirely — leaving an indexed match with no squad and no error.
        raise ValueError("delivery contains zero lineup entries — roster payload is empty or truncated")

    # `None` must stay OUT of this set. An id-less event would otherwise put it in,
    # and a frame with no event_uuid would then match it and slip through as valid.
    event_ids = {event["id"] for event in events if event.get("id") is not None}
    orphans = [f.get("event_uuid") for f in frames if f.get("event_uuid") not in event_ids]
    if orphans:
        raise ValueError(
            f"{len(orphans)} freeze frame(s) reference unknown event ids, e.g. {orphans[:3]} — "
            f"events and frames are not from the same export"
        )

    periods = {event.get("period") for event in events if event.get("period") is not None}
    if len(periods) < 2:
        raise ValueError(f"events cover {len(periods)} period(s); expected at least 2")

    if not any((event.get("type") or {}).get("name") == "Half End" for event in events):
        raise ValueError("no 'Half End' event — the delivery looks truncated")

    team_ids = {
        event["team"]["id"]
        for event in events
        if isinstance(event.get("team"), dict) and event["team"].get("id") is not None
    }
    if len(team_ids) != 2:
        raise ValueError(f"events contain {len(team_ids)} distinct team(s); expected exactly 2")

    # A contradiction INSIDE the delivery, which ADR 0010 rule 3 says is exactly the
    # kind that can be made to raise: one export of one fixture cannot legitimately
    # field one pair of teams in its events and a different pair in its lineups.
    lineup_team_ids = {team.get("team_id") for team in lineups if team.get("team_id") is not None}
    if lineup_team_ids != team_ids:
        raise ValueError(
            f"lineups cover team(s) {sorted(lineup_team_ids, key=str)} but events cover "
            f"{sorted(team_ids, key=str)} — the delivery contradicts itself"
        )


@dataclass(frozen=True)
class Bundle:
    """One delivered match: parsed, de-pivoted and joined. No artifact bodies are transformed."""

    root: Path
    match: dict
    competition: dict
    events: list[dict]
    frames: list[dict]
    lineups: list[dict]


def _require_array(root: Path, source_key: str) -> list[dict]:
    """Load a passthrough file that must already be a feed array OF OBJECTS.

    events/frames/lineups are staged verbatim, so they get no normalise_records
    treatment — but they still need a stated failure. Without this, a malformed
    file surfaces downstream as ``AttributeError: 'str' object has no attribute
    'get'`` instead of naming the file and the expectation.

    The element check is what actually closes that gap. A container check alone
    waves ``["a", "b"]`` through — it IS a list — and the AttributeError still
    happens, just later, in assert_delivery_coherent. So the offending index is
    named here, where the file name is still in hand.
    """
    name = SOURCE_FILES[source_key]
    payload = load_json(root / name)
    if not isinstance(payload, list):
        raise ValueError(f"{name} is a {type(payload).__name__}, expected a JSON array")
    for index, record in enumerate(payload):
        if not isinstance(record, dict):
            raise ValueError(f"{name}[{index}] is a {type(record).__name__}, expected a JSON object")
    return payload


def read_bundle(root: Path) -> Bundle:
    """Load and normalise one delivered bundle directory.

    The match id is discovered from matches.json rather than passed in, so a second
    delivery is a re-run rather than a rewrite.
    """
    missing = [name for name in SOURCE_FILES.values() if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError(f"bundle at {root} is missing {missing}")

    matches = normalise_records(load_json(root / SOURCE_FILES["matches"]), "match_id")
    if len(matches) != 1:
        raise ValueError(
            f"matches.json holds {len(matches)} rows; this reader ingests one match per bundle "
            f"(events/frames cover a single fixture, so a multi-row dump is ambiguous)"
        )
    competitions = normalise_records(load_json(root / SOURCE_FILES["competitions"]), "competition_id")

    return Bundle(
        root=root,
        match=matches[0],
        competition=join_competition(competitions, matches[0]),
        events=_require_array(root, "events"),
        frames=_require_array(root, "frames"),
        lineups=_require_array(root, "lineups"),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest src/tests/test_statsbomb_format.py -v`
Expected: PASS (86 tests — the shipped total for this file; Task 6 is the last task that appends to it)

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check src/formats/statsbomb.py src/tests/test_statsbomb_format.py && uv run pyright src/formats/statsbomb.py`
Expected: no errors.

---

### Task 7: Upload script

Implements §3 (D-3 staging + compression rule), §4.5 and §5.3.

**Everything that can fail is hoisted before the first byte is staged** — team resolution, metadata build, index-field derivation and player validation. `upload_players` validates too, but it runs *after* `upload_game` has already written artifacts and both indexes, so a validation failure there would leave an indexed match with no player catalogue.

**Files:**
- Create: `scripts/upload_statsbomb_club.py`
- Test: `src/tests/test_upload_statsbomb.py`

**Interfaces:**
- Consumes: `read_bundle`, `assert_delivery_coherent`, `build_metadata`, `match_info`, `players_from_lineups`, `resolve_team_ids`, `team_gender`, `ARTIFACT_SPECS`, `STAGED_METADATA_FILENAME`, `Bundle` (Tasks 1–6); `upload_game`, `upload_players`, `PlayerRecord`.
- Produces:
  - `PROVIDER`, `SOURCE_NAME`, `SOURCE_LICENCE` module constants.
  - `stage_bundle(bundle: Bundle, staging: Path, metadata: dict) -> None`
  - `upload_bundle(root: Path, bucket: str) -> tuple[str, int]` — returns `(match_id, players_uploaded)`.

- [ ] **Step 1: Write the failing tests**

Create `src/tests/test_upload_statsbomb.py`:

```python
"""Tests for scripts/upload_statsbomb_club.py (no real S3, no real network)."""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from formats import statsbomb as sb

_PATH_PARAM_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


@pytest.fixture
def staged(tmp_path, sb_bundle_dir, load_script):
    """Stage the canonical synthetic bundle; return (module, staging_dir)."""
    mod = load_script("upload_statsbomb_club")
    bundle = sb.read_bundle(sb_bundle_dir())
    staging = tmp_path / "stage"
    staging.mkdir()
    home_id, away_id = sb.resolve_team_ids(bundle.events, bundle.match)
    metadata = sb.build_metadata(
        bundle.match,
        bundle.competition,
        home_id,
        away_id,
        sb.team_gender(bundle.lineups, home_id),
        sb.team_gender(bundle.lineups, away_id),
    )
    mod.stage_bundle(bundle, staging, metadata)
    return mod, staging


class TestStageBundle:
    def test_stages_four_role_aligned_artifacts(self, staged) -> None:
        _mod, staging = staged
        assert sorted(p.name for p in staging.iterdir()) == [
            "events.json.gz",
            "freeze_frames.json.gz",
            "metadata.json",
            "roster.json",
        ]

    def test_artifact_keys_derived_from_stems_match_the_vocabulary(self, staged) -> None:
        _mod, staging = staged
        # upload_game derives the artifact name as name.split(".", 1)[0].
        keys = sorted(p.name.split(".", 1)[0] for p in staging.iterdir())
        assert keys == ["events", "freeze_frames", "metadata", "roster"]

    def test_artifact_keys_satisfy_the_path_param_regex(self, staged) -> None:
        # MatchEntry._validate_artifact_keys enforces this (models.py:68-78); a key
        # that fails it would be accepted at staging and rejected at upload.
        _mod, staging = staged
        for path in staging.iterdir():
            key = path.name.split(".", 1)[0]
            assert _PATH_PARAM_RE.match(key) and len(key) <= 128

    def test_large_bodies_are_gzipped_and_round_trip(self, staged, sb_events) -> None:
        _mod, staging = staged
        with gzip.open(staging / "events.json.gz", "rt", encoding="utf-8") as f:
            assert json.load(f) == sb_events

    def test_roster_is_staged_verbatim(self, staged) -> None:
        # The proprietary rating block survives in the roster artifact.
        _mod, staging = staged
        roster = json.loads((staging / "roster.json").read_text(encoding="utf-8"))
        assert roster[0]["lineup"][0]["skills"]["HOPS"]["rating"] == 0.5137

    def test_metadata_artifact_is_an_object_in_feed_shape(self, staged) -> None:
        _mod, staging = staged
        md = json.loads((staging / "metadata.json").read_text(encoding="utf-8"))
        assert isinstance(md, dict)
        assert md["competition"]["competition_id"] == 33
        assert md["home_team"]["home_team_id"] == 90001
        assert md["stadium"]["id"] is None

    def test_gender_is_resolved_for_both_sides_end_to_end(self, staged) -> None:
        # The whole-delivery check. With a lineups fixture covering only one team,
        # home_team_gender is null here and nothing else in the suite notices.
        _mod, staging = staged
        md = json.loads((staging / "metadata.json").read_text(encoding="utf-8"))
        assert md["home_team"]["home_team_gender"] == "female"
        assert md["away_team"]["away_team_gender"] == "female"


class TestPreflight:
    def test_orphan_frame_blocks_upload(self, sb_bundle_dir, load_script) -> None:
        mod = load_script("upload_statsbomb_club")
        root = sb_bundle_dir(overrides={"frames.json": [{"event_uuid": "orphan"}]})
        with (
            patch.object(mod, "upload_game") as game,
            patch.object(mod, "upload_players") as players,
        ):
            with pytest.raises(ValueError, match="unknown event id"):
                mod.upload_bundle(root, "test-bucket")
        game.assert_not_called()
        players.assert_not_called()

    def test_invalid_player_blocks_upload_before_any_index_write(self, sb_bundle_dir, sb_lineups, load_script) -> None:
        # A player with no usable name fails PlayerRecord. It must stop the run
        # BEFORE upload_game writes artifacts and indexes.
        mod = load_script("upload_statsbomb_club")
        sb_lineups[0]["lineup"].append({"player_id": 7, "player_name": "", "player_nickname": ""})
        root = sb_bundle_dir(overrides={"lineups.json": sb_lineups})
        with (
            patch.object(mod, "upload_game") as game,
            patch.object(mod, "upload_players") as players,
        ):
            with pytest.raises(ValidationError, match="nickname"):
                mod.upload_bundle(root, "test-bucket")
        game.assert_not_called()
        players.assert_not_called()

    def test_empty_lineups_blocks_upload(self, sb_bundle_dir, load_script) -> None:
        # Regression: `[]` used to sail through — _require_array accepted it,
        # assert_delivery_coherent never saw it, an empty roster was staged, the
        # match was indexed, and `if players:` skipped the catalogue upload.
        mod = load_script("upload_statsbomb_club")
        root = sb_bundle_dir(overrides={"lineups.json": []})
        with (
            patch.object(mod, "upload_game") as game,
            patch.object(mod, "upload_players") as players,
        ):
            with pytest.raises(ValueError, match="zero lineup entries"):
                mod.upload_bundle(root, "test-bucket")
        game.assert_not_called()
        players.assert_not_called()

    def test_lineups_for_the_wrong_teams_block_upload(self, sb_bundle_dir, sb_lineups, load_script) -> None:
        mod = load_script("upload_statsbomb_club")
        sb_lineups[1]["team_id"] = 90002
        root = sb_bundle_dir(overrides={"lineups.json": sb_lineups})
        with (
            patch.object(mod, "upload_game") as game,
            patch.object(mod, "upload_players") as players,
        ):
            with pytest.raises(ValueError, match="contradicts itself"):
                mod.upload_bundle(root, "test-bucket")
        game.assert_not_called()
        players.assert_not_called()

    def test_no_force_flag_exists(self, load_script) -> None:
        mod = load_script("upload_statsbomb_club")
        assert "--force" not in Path(mod.__file__).read_text(encoding="utf-8")


class TestUploadBundle:
    def test_uploads_private_original_with_index_fields(self, sb_bundle_dir, load_script) -> None:
        mod = load_script("upload_statsbomb_club")
        with (
            patch.object(mod, "upload_game") as game,
            patch.object(mod, "upload_players") as players,
        ):
            match_id, n_players = mod.upload_bundle(sb_bundle_dir(), "test-bucket")

        assert match_id == "9999999"
        assert n_players == 3

        kwargs = game.call_args.kwargs
        assert kwargs["provider"] == "statsbomb"
        assert kwargs["game_id"] == "9999999"
        assert kwargs["visibility"] == "private"
        assert kwargs["provenance"] == "original"
        assert kwargs["date"] == "2026-01-01"
        assert kwargs["home"] == "Alpha City"
        assert kwargs["away"] == "Beta United"
        assert kwargs["source_name"] == "StatsBomb"
        assert kwargs["source_licence"] == "Restricted; redistribution not permitted"

        assert players.call_args.kwargs["visibility"] == "private"
        assert players.call_args.kwargs["provider"] == "statsbomb"

    def test_production_path_stages_the_four_artifacts(self, sb_bundle_dir, load_script) -> None:
        # TestStageBundle composes the resolve -> build -> stage sequence itself, so
        # it validates the TEST's composition. This asserts what upload_bundle
        # actually puts in the tempdir, snapshotted before it is torn down.
        mod = load_script("upload_statsbomb_club")
        seen: list[str] = []

        def _snapshot(**kwargs) -> list[str]:
            seen.extend(sorted(p.name for p in kwargs["game_dir"].iterdir()))
            return seen

        with (
            patch.object(mod, "upload_game", side_effect=_snapshot),
            patch.object(mod, "upload_players"),
        ):
            mod.upload_bundle(sb_bundle_dir(), "test-bucket")

        assert seen == ["events.json.gz", "freeze_frames.json.gz", "metadata.json", "roster.json"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest src/tests/test_upload_statsbomb.py -v`
Expected: FAIL — `scripts/upload_statsbomb_club.py` does not exist.

- [ ] **Step 3: Write the implementation**

Create `scripts/upload_statsbomb_club.py`:

```python
"""Upload restricted commercial StatsBomb 360 data to the mock provider API (OWNER tier).

Owner-tier (visibility=private) ingest of a club file-drop delivery. NOT
redistributable — served only to the owner bearer token. See
docs/superpowers/specs/2026-08-12-statsbomb-commercial-360-owner-tier-design.md.

Source root is read from $STATSBOMB_RESTRICTED_DIR (an operator-local path that is
never committed).
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

from canonical.models import PlayerRecord  # noqa: E402
from formats.statsbomb import (  # noqa: E402
    ARTIFACT_SPECS,
    STAGED_METADATA_FILENAME,
    Bundle,
    assert_delivery_coherent,
    build_metadata,
    match_info,
    players_from_lineups,
    read_bundle,
    resolve_team_ids,
    team_gender,
)
from mock_api.upload import upload_game  # noqa: E402
from mock_api.upload_players import upload_players  # noqa: E402

PROVIDER = "statsbomb"
SOURCE_NAME = "StatsBomb"
SOURCE_LICENCE = "Restricted; redistribution not permitted"

# Probe value only — upload_players stamps the real timestamp on write.
_VALIDATION_PROBE_TIMESTAMP = "1970-01-01T00:00:00Z"


def _gzip_file(src: Path, dest: Path) -> None:
    """Stream-gzip src -> dest in 1 MiB chunks (never loads the body into memory)."""
    with src.open("rb") as f_in, gzip.open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out, length=1 << 20)


def _validate_players(players: list[dict]) -> None:
    """Validate every record BEFORE upload_game writes anything.

    upload_players validates too, but it runs AFTER upload_game has written the
    artifacts, matches.json and providers.json — a failure there would leave an
    indexed match with no player catalogue.
    """
    for record in players:
        PlayerRecord.model_validate({**record, "visibility": "private", "updated_at": _VALIDATION_PROBE_TIMESTAMP})


def stage_bundle(bundle: Bundle, staging: Path, metadata: dict) -> None:
    """Stage the four role-aligned artifacts.

    Compression rule (spec §3): gzip the multi-megabyte bodies (events, freeze
    frames), stage the kilobyte ones plain. `metadata` is passed in already built —
    every fallible step runs before staging opens.
    """
    for _role, source_name, staged_name in ARTIFACT_SPECS:
        src = bundle.root / source_name
        dest = staging / staged_name
        if staged_name.endswith(".gz"):
            _gzip_file(src, dest)
        else:
            shutil.copyfile(src, dest)

    (staging / STAGED_METADATA_FILENAME).write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def upload_bundle(root: Path, bucket: str) -> tuple[str, int]:
    """Pre-flight, stage and upload one delivery. Returns (match_id, players_uploaded).

    Ordering is load-bearing: every check that can fail runs before the first byte
    is staged and before upload_game writes any index (spec §7.1).
    """
    bundle = read_bundle(root)
    assert_delivery_coherent(bundle.events, bundle.frames, bundle.lineups)

    home_id, away_id = resolve_team_ids(bundle.events, bundle.match)
    metadata = build_metadata(
        bundle.match,
        bundle.competition,
        home_id,
        away_id,
        team_gender(bundle.lineups, home_id),
        team_gender(bundle.lineups, away_id),
    )
    info = match_info(metadata)
    players = players_from_lineups(bundle.lineups)
    _validate_players(players)

    with tempfile.TemporaryDirectory(prefix="sb-club-") as tmp:
        staging = Path(tmp)
        stage_bundle(bundle, staging, metadata)
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

    if players:
        with tempfile.TemporaryDirectory(prefix="sb-club-players-") as tmp:
            players_file = Path(tmp) / "players.json"
            players_file.write_text(json.dumps({"players": players}, indent=2, ensure_ascii=False), encoding="utf-8")
            upload_players(
                input_file=players_file,
                provider=PROVIDER,
                bucket=bucket,
                visibility="private",
                source_name=SOURCE_NAME,
                source_licence=SOURCE_LICENCE,
            )

    return info.match_id, len(players)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload restricted commercial StatsBomb 360 data to the mock provider API (owner tier)"
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("PINING_BUCKET"),
        help="S3 bucket name (default: $PINING_BUCKET env var)",
    )
    parser.add_argument(
        "--source-dir",
        default=os.environ.get("STATSBOMB_RESTRICTED_DIR"),
        help="Bundle root containing events.json, frames.json, lineups.json, matches.json, "
        "competitions.json (default: $STATSBOMB_RESTRICTED_DIR env var)",
    )
    args = parser.parse_args()

    if not args.bucket:
        parser.error("--bucket is required (or set PINING_BUCKET)")
    if not args.source_dir:
        parser.error("--source-dir is required (or set STATSBOMB_RESTRICTED_DIR)")

    root = Path(args.source_dir)
    if not root.is_dir():
        parser.error(f"not a directory: {root}")

    print(f"Uploading restricted StatsBomb data to s3://{args.bucket}/{PROVIDER}/ (OWNER tier)")
    match_id, n_players = upload_bundle(root, args.bucket)
    print(f"Done — match {match_id}, {n_players} player(s) uploaded.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest src/tests/test_upload_statsbomb.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Run the whole suite for regressions**

Run: `uv run pytest src/tests/ -q`
Expected: all tests pass; no existing test breaks.

- [ ] **Step 6: Lint and type-check**

Run: `uv run ruff check scripts/upload_statsbomb_club.py src/tests/test_upload_statsbomb.py && uv run ruff format --check scripts/upload_statsbomb_club.py && uv run pyright scripts/upload_statsbomb_club.py`
Expected: no errors.

---

### Task 8: Verification script

**Written against `scripts/verify_skillcorner_realmadrid_load.py`, not from scratch.** That script already solves three problems this one would otherwise hit:

1. **Redirect handling.** `urlopen` follows the API's 302 to a presigned S3 URL and CPython's `HTTPRedirectHandler` copies the `Authorization` header onto the redirected request. S3 rejects a request carrying both a bearer header and query-string auth. `NoFollow` (now shared, in `scripts/_verify_http.py`) stops the redirect; the presigned URL is then fetched with a **clean**, header-free request.
2. **No bare asserts.** `S101` is only ignored under `src/tests/**`, and `python -O` strips asserts entirely. The precedent accumulates `failures: list[str]` and returns an exit code.
3. **Range GET** (`bytes=0-0` + `Content-Range` parsing) so a multi-megabyte artifact is validated without downloading it.

It also takes **both** tokens. For a provider that is owner-only from birth, "the public tier cannot see this" is the licence post-condition — the one check that must not be missing.

**Files:**
- Create: `scripts/verify_statsbomb_load.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: nothing from earlier tasks (the verifier is standalone by design).
- Produces: `main() -> int` — `0` on success, `1` with a printed failure list.

- [ ] **Step 1: Add the ruff per-file-ignore**

The spec's §6 note is that no `[project.scripts]` entry is added — but ruff config *does* change: `urllib.request` against a configurable HTTPS endpoint trips `S310`, exactly as the existing verify scripts do.

In `pyproject.toml`, under `[tool.ruff.lint.per-file-ignores]`, immediately after the existing `verify_skillcorner_realmadrid_load.py` entry, add:

```toml
# Verifier: urllib.request to a configurable HTTPS API endpoint is the entire point (S310 false-positive).
"scripts/verify_statsbomb_load.py" = ["S310"]
```

- [ ] **Step 2: Write the script**

Create `scripts/verify_statsbomb_load.py`:

> **As-planned sketch — superseded on one axis.** The block below defines
> `parse_content_range_total`, `_get_json` and the redirect handler **inline**, which
> is how this task was planned and how it originally shipped. A later, post-plan
> review extracted those three into `scripts/_verify_http.py` (and renamed
> `_NoFollow` → `NoFollow`) across all four verify scripts; the shipped script now
> imports them. That extraction was never a task in this plan, so the block is left
> as written rather than back-dated — read `scripts/_verify_http.py` and
> `scripts/verify_statsbomb_load.py` for the current shape, and the `[0.4.0]`
> CHANGELOG entry for why the presigned-fetch functions stayed per-script.

```python
"""Post-load verification for the restricted StatsBomb commercial 360 owner-tier dataset.

Asserts (sampling ids from the live OWNER response — no licensed ids hardcoded):
  - owner /statsbomb/matches contains private (restricted) entries
  - those private ids are ABSENT from the public /statsbomb/matches list
  - every entry carries a date (a dateless entry is invisible to dateFrom/dateTo)
  - the artifact key set is exactly the role vocabulary for this provider
  - owner can fetch each artifact (large ones via a Range GET); public gets 404
  - the metadata artifact is a single object in feed shape
  - owner /statsbomb/players is non-empty and every record is private

Mirrors scripts/verify_skillcorner_realmadrid_load.py — including NoFollow, which
exists because urllib would otherwise forward the bearer token to presigned S3.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

PROVIDER = "statsbomb"
EXPECTED_ARTIFACTS = {"events", "freeze_frames", "roster", "metadata"}
# Multi-megabyte bodies — validate via Range GET, no full download.
LARGE_ARTIFACTS = {"events", "freeze_frames"}


def parse_content_range_total(header: str) -> int:
    m = re.search(r"/(\d+)\s*$", header or "")
    return int(m.group(1)) if m else -1


def _get_json(api: str, path: str, token: str) -> dict:
    req = urllib.request.Request(f"{api}{path}", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


class NoFollow(urllib.request.HTTPRedirectHandler):
    """Do not follow the API's 302.

    urllib copies every header except Content-Length/Content-Type onto the
    redirected request, so Authorization would reach the presigned S3 URL — which
    already carries query-string auth. S3 rejects requests with two auth mechanisms.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _status_or_presigned(api: str, path: str, token: str) -> tuple[int, str | None]:
    """Return (status, location). For 302 returns the presigned URL; otherwise None."""
    req = urllib.request.Request(f"{api}{path}", headers={"Authorization": f"Bearer {token}"})
    opener = urllib.request.build_opener(NoFollow)
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.status, None
    except urllib.error.HTTPError as e:
        if e.code == 302:
            return 302, e.headers.get("Location")
        return e.code, None


def _fetch_presigned(location: str, large: bool) -> tuple[int, int, bytes | None]:
    """Fetch the presigned URL with a CLEAN request — no Authorization header.

    Returns (status, total_bytes, body). `body` is None for a Range GET, where only
    the size is known; small artifacts return their body so a caller that needs to
    inspect the content does not have to fetch it twice.
    """
    if large:
        s3_req = urllib.request.Request(location, headers={"Range": "bytes=0-0"})
        with urllib.request.urlopen(s3_req, timeout=60) as resp:
            return resp.status, parse_content_range_total(resp.headers.get("Content-Range", "")), None
    with urllib.request.urlopen(urllib.request.Request(location), timeout=60) as resp:
        body = resp.read()
        return resp.status, len(body), body


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the restricted StatsBomb owner-tier load")
    parser.add_argument("--api", required=True, help="API base URL (no trailing slash)")
    parser.add_argument("--owner-token", required=True)
    parser.add_argument("--public-token", required=True)
    args = parser.parse_args()

    failures: list[str] = []

    owner_matches = _get_json(args.api, f"/{PROVIDER}/matches", args.owner_token).get("matches", [])
    public_matches = _get_json(args.api, f"/{PROVIDER}/matches", args.public_token).get("matches", [])
    public_ids = {m["id"] for m in public_matches}

    restricted = [m for m in owner_matches if m.get("visibility") == "private"]
    if not restricted:
        failures.append(f"owner /{PROVIDER}/matches: no private (restricted) entries found")
    else:
        print(f"OK: owner sees {len(restricted)} restricted match(es)")

    leaked = [m["id"] for m in restricted if m["id"] in public_ids]
    if leaked:
        failures.append(f"restricted ids visible to public token: {leaked[:5]}")
    elif restricted:
        print("OK: restricted ids absent from public match list")

    undated = [m["id"] for m in restricted if not m.get("date")]
    if undated:
        failures.append(f"entries with no date (invisible to dateFrom/dateTo): {undated[:5]}")
    elif restricted:
        print("OK: every restricted entry carries a date")

    if restricted:
        sample = restricted[0]
        mid = sample["id"]

        artifacts = set(sample.get("artifacts", {}))
        if artifacts != EXPECTED_ARTIFACTS:
            failures.append(f"artifact keys {sorted(artifacts)} != {sorted(EXPECTED_ARTIFACTS)}")
        else:
            print(f"OK: artifact keys = {sorted(artifacts)}")

        for artifact in sorted(artifacts):
            o_status, location = _status_or_presigned(
                args.api, f"/{PROVIDER}/matches/{mid}/{artifact}", args.owner_token
            )
            if o_status == 302 and location:
                a_status, total, body = _fetch_presigned(location, artifact in LARGE_ARTIFACTS)
                if a_status in (200, 206) and total > 0:
                    print(f"OK: owner {artifact} -> {a_status}, {total}B")
                else:
                    failures.append(f"owner {mid}/{artifact}: status={a_status}, total={total}")

                if artifact == "metadata" and body is not None:
                    md = json.loads(body.decode("utf-8"))
                    if not isinstance(md, dict):
                        failures.append("metadata artifact is not a JSON object")
                    elif "competition_id" not in md.get("competition", {}):
                        failures.append("metadata.competition is not nested in feed shape")
                    elif "home_team_id" not in md.get("home_team", {}):
                        failures.append("metadata.home_team is not nested in feed shape")
                    else:
                        print("OK: metadata is a single object in feed shape")
            else:
                failures.append(f"owner {mid}/{artifact}: expected 302, got {o_status}")

            p_status, _ = _status_or_presigned(
                args.api, f"/{PROVIDER}/matches/{mid}/{artifact}", args.public_token
            )
            if p_status == 404:
                print(f"OK: public {artifact} -> 404 (no existence leak)")
            else:
                failures.append(f"public {mid}/{artifact}: expected 404, got {p_status}")

    owner_players = _get_json(args.api, f"/{PROVIDER}/players", args.owner_token).get("players", [])
    if not owner_players:
        failures.append(f"owner /{PROVIDER}/players: empty (derived catalogue missing)")
    else:
        print(f"OK: owner /{PROVIDER}/players = {len(owner_players)}")
        non_private = [p["id"] for p in owner_players if p.get("visibility") != "private"]
        if non_private:
            failures.append(f"player(s) not private: {non_private[:5]}")
        else:
            print("OK: every player record is private")

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

- [ ] **Step 3: Lint and type-check**

Run: `uv run ruff check scripts/verify_statsbomb_load.py && uv run ruff format --check scripts/verify_statsbomb_load.py && uv run pyright scripts/verify_statsbomb_load.py`
Expected: no errors. In particular, no `S101` violations — the script contains no bare `assert`.

- [ ] **Step 4: Confirm the CLI wiring works without network**

Run: `uv run python scripts/verify_statsbomb_load.py --help`
Expected: argparse help text listing `--api`, `--owner-token`, `--public-token`; exit 0.

---

### Task 9: ADR 0010 and documentation

Implements §6 (Changed) and §9. The ADR is the durable artifact — it binds future providers.

**Files:**
- Create: `docs/decisions/0010-faithful-feed-mimicry.md`
- Modify: `docs/decisions/README.md`, `CLAUDE.md`, `README.md`, `docs/api-reference.md`, `docs/c4/architecture.dsl`, `docs/c4/architecture.html`

- [ ] **Step 1: Write ADR 0010**

Create `docs/decisions/0010-faithful-feed-mimicry.md`:

```markdown
# 0010 — Faithful-Feed Mimicry

## Status

Accepted

## Date

2026-08-12

## Context

A commercial StatsBomb 360 delivery arrived as a club file drop. Three of its five
files were raw feed arrays; two were pandas column-orient dumps produced by
`statsbombpy`'s default `fmt="dataframe"` mode, which flattens the feed's nested
objects.

That forced a question with no precedent here: when the delivered copy and the
provider's own published format disagree, which one does this API serve?

It matters because this repo is a **mock provider API**. The downstream consumer
builds its parser against what we serve, and had not yet seen the real format. A
flattened artifact would have taught it a contract that breaks the day a
full-fidelity copy arrives — a Hyrum's Law trap of our own making.

## Decision

**A provider-native substructure served by this API represents the provider's
format, not the format of the pipeline that happened to deliver our copy.**

Three rules bound it.

1. **Scope.** This governs the *content* of provider-native structures. It does
   **not** govern the artifact envelope — how many entities an artifact holds and
   how it is scoped. That is ADR 0008's role vocabulary, which is match-scoped by
   construction. Without this boundary a future provider could cite whichever half
   suited it.

2. **Honesty.** Structure may be **reconstructed**; values may never be
   **invented**. A field the delivery dropped is emitted as `null`. A
   plausible-looking synthesised id is indistinguishable from a real one
   downstream, which makes it worse than an absence.

3. **The inference boundary.** Inference **from inside the delivery** is permitted;
   inference **from outside it** is not. The delivery is a single coherent export
   of one fixture, so a disagreement inside it is a contradiction and can be made
   to raise. An outside source carries no such guarantee — it may simply describe
   something else, and agreeing or disagreeing with it proves nothing either way.

This is not a licence to compose. Merging two delivered files is legitimate only
when the feed itself co-locates their contents — as StatsBomb's match object
natively contains `competition{}` and `season{}`. Merging files the feed keeps
apart would be synthesis, and is forbidden.

## Consequences

**Positive:** A consumer's parser is correct on day one and stays correct when a
fuller delivery arrives. Reconstruction rules are explicit and testable, and the
honesty rule makes gaps visible rather than plausible.

**Negative / accepted:** Onboarding a delivery costs a reconstruction step and a
set of fail-loud join/resolution rules rather than a byte-for-byte passthrough.
Reconstruction depends on the delivering tool's conventions (for StatsBomb, a
`statsbombpy` rendering), so a tooling change can break a join — mitigated by
failing loud rather than guessing, and by recording the tool version in the
operator's delivery note.

**Reversal cost:** Low for a future provider (convention, applied per provider at
ingest). Moderate for one already published — changing a served shape is a consumer
break.

## Alternatives Considered

- **Byte-for-byte passthrough of whatever arrives.** Rejected: publishes an
  artifact of the export tooling, and the resulting shape becomes a contract we
  would have to break later.
- **Serve the delivered shape and let each consumer normalise.** Rejected: pushes
  the same reconstruction onto every consumer, and each would do it differently.
- **Reconstruct fully, inferring missing ids from the provider's public catalogue.**
  Rejected: the public catalogue describes different seasons, so an inferred id
  could be wrong in a way nothing in the delivery can detect. This is the case
  rule 3 exists to forbid.

## See Also

- Spec: `docs/superpowers/specs/2026-08-12-statsbomb-commercial-360-owner-tier-design.md`
- ADR 0008 (role-aligned artifact keys — governs the envelope this ADR excludes)
- ADR 0009 (restricted tier under an existing public provider)
- Implementation: `src/formats/statsbomb.py`, `scripts/upload_statsbomb_club.py`
```

- [ ] **Step 2: Add the ADR index row**

In `docs/decisions/README.md`, add a row to the index table immediately after the `0009` row:

```markdown
| [0010](0010-faithful-feed-mimicry.md) | Faithful-Feed Mimicry | Accepted | StatsBomb commercial 360 owner-tier ingest (spec §3) |
```

- [ ] **Step 3: Update CLAUDE.md**

In `## Architecture`, extend the `src/formats/` bullet's parenthetical to include `StatsBomb commercial 360 club bundle for restricted owner-tier data`.

In the `scripts/` bullet, add `upload_statsbomb_club.py, verify_statsbomb_load.py`.

In `## Mock Provider API: two-tier auth`, after the owner-tier paragraph, add:

```markdown
Owner-tier providers: `gradientsports` (Gradient Sports), `skillcorner` private tier
(restricted Real Madrid), `statsbomb` (commercial 360 club delivery — ADR 0010).
```

- [ ] **Step 4: Update README.md and docs/api-reference.md**

Read the existing `gradientsports` entries in each file first and match their form exactly.

In both, add `statsbomb` to the provider enumeration, noting it is owner-tier only and serves `events`, `freeze_frames`, `roster`, `metadata`. In `api-reference.md` add that `statsbomb` has **no `tracking` artifact** — StatsBomb 360 supplies event-moment freeze frames, not continuous tracking — and that its `metadata` artifact is a single JSON object in StatsBomb feed shape.

- [ ] **Step 5: Update the C4 model**

Read the existing `gradientsports` element and its relationships in `docs/c4/architecture.dsl`, then add a `statsbomb` element mirroring that structure with the description "Commercial StatsBomb 360 (owner tier)".

- [ ] **Step 6: Regenerate the C4 HTML**

Use the `mad-scientist-skills:c4` skill to regenerate `docs/c4/architecture.html` from the updated `.dsl`. The HTML is generated — never hand-edit it.

- [ ] **Step 7: Full verification sweep**

Run: `uv run pytest src/tests/ -q && uv run ruff check . && uv run ruff format --check . && uv run pyright src/ scripts/upload_statsbomb_club.py scripts/verify_statsbomb_load.py`

Expected: all tests pass, no lint errors, no type errors.

**Why pyright is scoped to the two new scripts rather than all of `scripts/`.** CI runs `uv run pyright src/` (`python-ci.yml:43`) and pre-commit runs no pyright at all, so `scripts/` has **never** been type-checked. A bare `pyright scripts/` would pull nine pre-existing scripts into pyright for the first time at the last step of this plan, and a worker hitting errors in `upload_gradient_wc2022.py` or `verify_idsse_load.py` would either fix unrelated code or stall. Those are out of scope here. Widening pyright's coverage to all of `scripts/` is worth doing — as its own change, with its own review.

`ruff check .` is unfiltered on purpose: pre-commit already runs ruff and ruff-format across the whole repo, so it is known clean.

- [ ] **Step 8: Run the final-review skill**

CLAUDE.md makes this non-negotiable before the final commit of any multi-file change. Invoke `mad-scientist-skills:final-review` and address anything it surfaces — documentation drift, stale references, missing test updates.

---

## Operator runbook (not an implementation task)

After the plan is implemented and the user has approved a commit, the actual data load is:

```bash
export PINING_BUCKET=<bucket>
export STATSBOMB_RESTRICTED_DIR=<operator-local bundle root>
uv run python scripts/upload_statsbomb_club.py

uv run python scripts/verify_statsbomb_load.py \
  --api <api base>/v1 \
  --owner-token <owner token> \
  --public-token <public token>
```

Record in the operator-local delivery note (never in this repo): the match /
competition / season ids, the club names, the `statsbombpy` version that produced
the delivery, and the receipt date.
