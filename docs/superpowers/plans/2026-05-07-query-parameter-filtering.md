# Query Parameter Filtering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `updatedSince`, `dateFrom`, and `dateTo` query-parameter filtering to `list_matches` and `list_players` Lambda handlers so downstream consumers can poll for new/changed data.

**Architecture:** A single shared helper `parse_query_filters` in `shared.py` validates and parses query parameters from the API Gateway event. Each handler calls it, gets back either a parsed filter dict or a 400 error response, then applies filters to the list before returning. No new Lambdas, no Terraform changes, no model changes.

**Tech Stack:** Python 3.12, boto3 (Lambda built-in), pytest + unittest.mock

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `terraform/modules/functions/src/shared.py` | Modify (add ~40 lines) | `parse_query_filters` + `apply_filters` helpers |
| `terraform/modules/functions/src/list_matches.py` | Modify (add ~8 lines) | Parse filters, apply to matches list |
| `terraform/modules/functions/src/list_players.py` | Modify (add ~8 lines) | Parse filters (updatedSince only), reject dateFrom/dateTo |
| `src/tests/test_lambda_handlers.py` | Modify (add test classes) | All filter tests |

---

### Task 1: Add `parse_query_filters` and `apply_filters` to `shared.py`

**Files:**
- Modify: `terraform/modules/functions/src/shared.py` (append after line 148)
- Test: `src/tests/test_lambda_handlers.py`

- [ ] **Step 1: Write failing tests for `parse_query_filters`**

Add to `src/tests/test_lambda_handlers.py` after the `TestSafeParam` class (after line 151):

```python
# ----- Query filter parsing -----


class TestParseQueryFilters:
    def test_no_params_returns_empty_filters(self) -> None:
        from shared import parse_query_filters

        result = parse_query_filters({}, allowed={"updatedSince", "dateFrom", "dateTo"})
        assert result == {}

    def test_null_query_string_returns_empty_filters(self) -> None:
        from shared import parse_query_filters

        event = {"queryStringParameters": None}
        result = parse_query_filters(event, allowed={"updatedSince", "dateFrom", "dateTo"})
        assert result == {}

    def test_valid_updated_since(self) -> None:
        from shared import parse_query_filters

        event = {"queryStringParameters": {"updatedSince": "2026-05-01T00:00:00Z"}}
        result = parse_query_filters(event, allowed={"updatedSince"})
        assert result == {"updatedSince": "2026-05-01T00:00:00Z"}

    def test_valid_date_from(self) -> None:
        from shared import parse_query_filters

        event = {"queryStringParameters": {"dateFrom": "2022-11-20"}}
        result = parse_query_filters(event, allowed={"dateFrom", "dateTo"})
        assert result == {"dateFrom": "2022-11-20"}

    def test_valid_date_to(self) -> None:
        from shared import parse_query_filters

        event = {"queryStringParameters": {"dateTo": "2022-12-19"}}
        result = parse_query_filters(event, allowed={"dateFrom", "dateTo"})
        assert result == {"dateTo": "2022-12-19"}

    def test_all_three_combined(self) -> None:
        from shared import parse_query_filters

        event = {"queryStringParameters": {
            "updatedSince": "2026-05-01T00:00:00Z",
            "dateFrom": "2022-11-20",
            "dateTo": "2022-12-19",
        }}
        result = parse_query_filters(event, allowed={"updatedSince", "dateFrom", "dateTo"})
        assert result == {
            "updatedSince": "2026-05-01T00:00:00Z",
            "dateFrom": "2022-11-20",
            "dateTo": "2022-12-19",
        }

    def test_invalid_updated_since_returns_400(self) -> None:
        from shared import parse_query_filters

        event = {"queryStringParameters": {"updatedSince": "not-a-date"}}
        result = parse_query_filters(event, allowed={"updatedSince"})
        assert isinstance(result, dict)
        assert result["statusCode"] == 400
        assert "updatedSince" in json.loads(result["body"])["error"]

    def test_invalid_date_from_returns_400(self) -> None:
        from shared import parse_query_filters

        event = {"queryStringParameters": {"dateFrom": "2022-13-45"}}
        result = parse_query_filters(event, allowed={"dateFrom", "dateTo"})
        assert isinstance(result, dict)
        assert result["statusCode"] == 400
        assert "dateFrom" in json.loads(result["body"])["error"]

    def test_invalid_date_to_returns_400(self) -> None:
        from shared import parse_query_filters

        event = {"queryStringParameters": {"dateTo": "yesterday"}}
        result = parse_query_filters(event, allowed={"dateFrom", "dateTo"})
        assert isinstance(result, dict)
        assert result["statusCode"] == 400
        assert "dateTo" in json.loads(result["body"])["error"]

    def test_disallowed_param_returns_400(self) -> None:
        from shared import parse_query_filters

        event = {"queryStringParameters": {"dateFrom": "2022-11-20"}}
        result = parse_query_filters(event, allowed={"updatedSince"})
        assert isinstance(result, dict)
        assert result["statusCode"] == 400
        assert "dateFrom" in json.loads(result["body"])["error"]

    def test_unrecognised_params_silently_ignored(self) -> None:
        from shared import parse_query_filters

        event = {"queryStringParameters": {"randomJunk": "hello", "updatedSince": "2026-05-01T00:00:00Z"}}
        result = parse_query_filters(event, allowed={"updatedSince"})
        assert result == {"updatedSince": "2026-05-01T00:00:00Z"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src && uv run pytest tests/test_lambda_handlers.py::TestParseQueryFilters -v`
Expected: FAIL with `ImportError: cannot import name 'parse_query_filters' from 'shared'`

- [ ] **Step 3: Write failing tests for `apply_filters`**

Add to `src/tests/test_lambda_handlers.py` after `TestParseQueryFilters`:

```python
class TestApplyFilters:
    def test_updated_since_exclusive(self) -> None:
        from shared import apply_filters

        records = [
            {"id": "a", "updated_at": "2026-05-01T00:00:00Z"},
            {"id": "b", "updated_at": "2026-05-02T00:00:00Z"},
            {"id": "c", "updated_at": "2026-05-03T00:00:00Z"},
        ]
        result = apply_filters(records, {"updatedSince": "2026-05-01T00:00:00Z"})
        assert [r["id"] for r in result] == ["b", "c"]

    def test_date_from_inclusive(self) -> None:
        from shared import apply_filters

        records = [
            {"id": "a", "date": "2022-11-20"},
            {"id": "b", "date": "2022-11-21"},
            {"id": "c", "date": "2022-11-19"},
        ]
        result = apply_filters(records, {"dateFrom": "2022-11-20"})
        assert [r["id"] for r in result] == ["a", "b"]

    def test_date_to_exclusive(self) -> None:
        from shared import apply_filters

        records = [
            {"id": "a", "date": "2022-11-20"},
            {"id": "b", "date": "2022-11-21"},
            {"id": "c", "date": "2022-11-22"},
        ]
        result = apply_filters(records, {"dateTo": "2022-11-21"})
        assert [r["id"] for r in result] == ["a"]

    def test_date_from_and_date_to_combined(self) -> None:
        from shared import apply_filters

        records = [
            {"id": "a", "date": "2022-11-19"},
            {"id": "b", "date": "2022-11-20"},
            {"id": "c", "date": "2022-11-21"},
            {"id": "d", "date": "2022-11-22"},
        ]
        result = apply_filters(records, {"dateFrom": "2022-11-20", "dateTo": "2022-11-22"})
        assert [r["id"] for r in result] == ["b", "c"]

    def test_all_three_filters_combined(self) -> None:
        from shared import apply_filters

        records = [
            {"id": "a", "date": "2022-11-20", "updated_at": "2026-04-30T00:00:00Z"},
            {"id": "b", "date": "2022-11-20", "updated_at": "2026-05-02T00:00:00Z"},
            {"id": "c", "date": "2022-11-25", "updated_at": "2026-05-02T00:00:00Z"},
            {"id": "d", "date": "2022-12-01", "updated_at": "2026-05-02T00:00:00Z"},
        ]
        result = apply_filters(records, {
            "updatedSince": "2026-05-01T00:00:00Z",
            "dateFrom": "2022-11-20",
            "dateTo": "2022-11-30",
        })
        # a: updated_at too old; b: passes all; c: passes all; d: date too late
        assert [r["id"] for r in result] == ["b", "c"]

    def test_null_date_excluded_when_date_from_active(self) -> None:
        from shared import apply_filters

        records = [
            {"id": "a", "date": "2022-11-20"},
            {"id": "b"},  # no date field at all
            {"id": "c", "date": None},
        ]
        result = apply_filters(records, {"dateFrom": "2022-11-01"})
        assert [r["id"] for r in result] == ["a"]

    def test_null_updated_at_excluded_when_updated_since_active(self) -> None:
        from shared import apply_filters

        records = [
            {"id": "a", "updated_at": "2026-05-02T00:00:00Z"},
            {"id": "b"},  # no updated_at
        ]
        result = apply_filters(records, {"updatedSince": "2026-05-01T00:00:00Z"})
        assert [r["id"] for r in result] == ["a"]

    def test_empty_filters_returns_all(self) -> None:
        from shared import apply_filters

        records = [{"id": "a"}, {"id": "b"}]
        result = apply_filters(records, {})
        assert [r["id"] for r in result] == ["a", "b"]
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd src && uv run pytest tests/test_lambda_handlers.py::TestApplyFilters -v`
Expected: FAIL with `ImportError: cannot import name 'apply_filters' from 'shared'`

- [ ] **Step 5: Implement `parse_query_filters` and `apply_filters` in `shared.py`**

Append after line 148 of `terraform/modules/functions/src/shared.py`:

```python
# ----- Query parameter filtering (spec: 2026-05-07-query-parameter-filtering §3) -----

_KNOWN_FILTERS = {"updatedSince", "dateFrom", "dateTo"}

_ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_iso8601(value: str) -> bool:
    """Check that value looks like YYYY-MM-DDTHH:MM:SSZ and represents a real date."""
    if not _ISO8601_RE.match(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        return True
    except ValueError:
        return False


def _validate_date(value: str) -> bool:
    """Check that value looks like YYYY-MM-DD and represents a real date."""
    if not _DATE_RE.match(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def parse_query_filters(event: dict, *, allowed: set[str]) -> dict:
    """Extract and validate query-string filter parameters.

    Returns a dict of ``{param_name: validated_value}`` on success, or an
    API Gateway error response dict (400) on validation failure.  Unknown
    query params are silently ignored; *known* params not in ``allowed``
    return 400 (e.g. ``dateFrom`` on ``list_players``).
    """
    qs = event.get("queryStringParameters") or {}
    filters: dict[str, str] = {}

    for key, value in qs.items():
        if key not in _KNOWN_FILTERS:
            continue  # silently ignore unrecognised params
        if key not in allowed:
            return _error_response(400, f"Filter '{key}' is not supported on this endpoint")
        if key == "updatedSince":
            if not _validate_iso8601(value):
                return _error_response(
                    400, f"Invalid {key}: expected ISO 8601 UTC timestamp (e.g., 2026-05-01T00:00:00Z)"
                )
        elif key in ("dateFrom", "dateTo"):
            if not _validate_date(value):
                return _error_response(400, f"Invalid {key}: expected YYYY-MM-DD")
        filters[key] = value

    return filters


def apply_filters(records: list[dict], filters: dict[str, str]) -> list[dict]:
    """Apply parsed query filters to a list of records. Spec §3.3–3.4."""
    if not filters:
        return records

    result = records
    if "updatedSince" in filters:
        threshold = filters["updatedSince"]
        result = [r for r in result if (r.get("updated_at") or "") > threshold]
    if "dateFrom" in filters:
        threshold = filters["dateFrom"]
        result = [r for r in result if (r.get("date") or "") >= threshold]
    if "dateTo" in filters:
        threshold = filters["dateTo"]
        result = [r for r in result if "" < (r.get("date") or "") < threshold]
    return result
```

Also add the `datetime` import — change line 1–18 of `shared.py`: add `from datetime import datetime` after `import re` (line 17).

- [ ] **Step 6: Run all filter tests to verify they pass**

Run: `cd src && uv run pytest tests/test_lambda_handlers.py::TestParseQueryFilters tests/test_lambda_handlers.py::TestApplyFilters -v`
Expected: All 19 tests PASS.

- [ ] **Step 7: Run existing tests to verify no regressions**

Run: `cd src && uv run pytest tests/test_lambda_handlers.py -v`
Expected: All existing tests PASS (no regressions from the new imports/code).

---

### Task 2: Wire filters into `list_matches` handler

**Files:**
- Modify: `terraform/modules/functions/src/list_matches.py`
- Test: `src/tests/test_lambda_handlers.py`

- [ ] **Step 1: Write failing tests for filtered `list_matches`**

Add to `src/tests/test_lambda_handlers.py` after the existing `TestListMatches` class:

```python
class TestListMatchesFiltering:
    def _payload(self):
        return {
            "provider": "sc",
            "matches": [
                {"id": "m1", "date": "2024-11-30", "updated_at": "2026-05-01T00:00:00Z",
                 "artifacts": {"match": "match.json"}, "visibility": "public"},
                {"id": "m2", "date": "2024-12-07", "updated_at": "2026-05-02T00:00:00Z",
                 "artifacts": {"match": "match.json"}, "visibility": "public"},
                {"id": "m3", "date": "2024-12-21", "updated_at": "2026-05-03T00:00:00Z",
                 "artifacts": {"match": "match.json"}, "visibility": "public"},
                {"id": "m4", "date": "2024-12-21", "updated_at": "2026-05-03T00:00:00Z",
                 "artifacts": {"match": "match.json"}, "visibility": "private"},
            ],
        }

    def test_updated_since_filters_matches(self) -> None:
        from list_matches import handler

        mock_s3 = _mock_s3()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(
            return_value=json.dumps(self._payload()).encode()))}

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "sc"},
            "queryStringParameters": {"updatedSince": "2026-05-01T00:00:00Z"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        ids = [m["id"] for m in json.loads(result["body"])["matches"]]
        assert ids == ["m2", "m3", "m4"]

    def test_date_from_filters_matches(self) -> None:
        from list_matches import handler

        mock_s3 = _mock_s3()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(
            return_value=json.dumps(self._payload()).encode()))}

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "sc"},
            "queryStringParameters": {"dateFrom": "2024-12-07"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        ids = [m["id"] for m in json.loads(result["body"])["matches"]]
        assert ids == ["m2", "m3", "m4"]

    def test_date_to_filters_matches(self) -> None:
        from list_matches import handler

        mock_s3 = _mock_s3()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(
            return_value=json.dumps(self._payload()).encode()))}

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "sc"},
            "queryStringParameters": {"dateTo": "2024-12-07"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        ids = [m["id"] for m in json.loads(result["body"])["matches"]]
        assert ids == ["m1"]

    def test_no_filters_returns_all_backwards_compat(self) -> None:
        from list_matches import handler

        mock_s3 = _mock_s3()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(
            return_value=json.dumps(self._payload()).encode()))}

        event = {
            "headers": {"authorization": "Bearer own-tok"},
            "pathParameters": {"provider": "sc"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        assert len(json.loads(result["body"])["matches"]) == 4

    def test_visibility_filter_applied_before_query_filters(self) -> None:
        from list_matches import handler

        mock_s3 = _mock_s3()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(
            return_value=json.dumps(self._payload()).encode()))}

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc"},
            "queryStringParameters": {"dateFrom": "2024-12-21"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        ids = [m["id"] for m in json.loads(result["body"])["matches"]]
        # m3 is public + matches date; m4 is private, filtered out by visibility
        assert ids == ["m3"]

    def test_invalid_updated_since_returns_400(self) -> None:
        from list_matches import handler

        mock_s3 = _mock_s3()

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc"},
            "queryStringParameters": {"updatedSince": "not-a-date"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "updatedSince" in json.loads(result["body"])["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src && uv run pytest tests/test_lambda_handlers.py::TestListMatchesFiltering -v`
Expected: FAIL — `queryStringParameters` not handled, filters not applied.

- [ ] **Step 3: Wire filters into `list_matches.py`**

Replace the full content of `terraform/modules/functions/src/list_matches.py`:

```python
"""GET /v1/{provider}/matches — list available games for a provider, filtered by tier."""

from __future__ import annotations

import json
import os

from shared import (
    Tier,
    apply_filters,
    get_s3_client,
    json_response,
    logger,
    parse_query_filters,
    validate_path_param,
    validate_token,
)

BUCKET = os.environ.get("DATA_BUCKET", "")

_ALLOWED_FILTERS = {"updatedSince", "dateFrom", "dateTo"}


def handler(event: dict, context: object) -> dict:
    """Return the matches index for a provider, filtered by caller's tier.

    Spec §4.2: PUBLIC tier sees only entries with visibility == "public" (or
    missing, treated as public). OWNER tier sees all entries. Empty filtered
    list returns 200 with `{"matches": []}` — not 404 — so the public tier
    cannot probe for the existence of any private matches.

    Query filters (spec: 2026-05-07-query-parameter-filtering §3):
    updatedSince, dateFrom, dateTo applied after visibility filtering.
    """
    tier = validate_token(event)
    if isinstance(tier, dict):
        logger.warning("auth_failure", extra={"handler": "list_matches"})
        return tier

    provider = (event.get("pathParameters") or {}).get("provider", "")
    param_error = validate_path_param(provider, "provider")
    if param_error:
        logger.warning("validation_failure", extra={"handler": "list_matches", "param": "provider"})
        return param_error

    filters = parse_query_filters(event, allowed=_ALLOWED_FILTERS)
    if isinstance(filters, dict) and "statusCode" in filters:
        return filters

    s3 = get_s3_client()
    key = f"{provider}/matches.json"
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        matches = json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        return json_response(404, {"error": "Provider not found"})
    except Exception:
        logger.exception("s3_error", extra={"handler": "list_matches"})
        return json_response(500, {"error": "Internal server error"})

    match_list = matches.get("matches", [])

    if tier != Tier.OWNER:
        # Filter to public entries (missing `visibility` field defaults to public).
        match_list = [m for m in match_list if m.get("visibility", "public") == "public"]

    match_list = apply_filters(match_list, filters)

    return json_response(200, {**matches, "matches": match_list})
```

- [ ] **Step 4: Run filtering tests to verify they pass**

Run: `cd src && uv run pytest tests/test_lambda_handlers.py::TestListMatchesFiltering -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Run all tests to verify no regressions**

Run: `cd src && uv run pytest tests/test_lambda_handlers.py -v`
Expected: All tests PASS (existing `TestListMatches` still green).

---

### Task 3: Wire filters into `list_players` handler

**Files:**
- Modify: `terraform/modules/functions/src/list_players.py`
- Test: `src/tests/test_lambda_handlers.py`

- [ ] **Step 1: Write failing tests for filtered `list_players`**

Add to `src/tests/test_lambda_handlers.py` after the existing `TestListPlayers` class:

```python
class TestListPlayersFiltering(_PlayersWiring):
    def test_updated_since_filters_players(self) -> None:
        from list_players import handler

        mock_s3 = _mock_s3()
        self._wire(
            mock_s3,
            public_payload={"provider": "sc", "players": [
                {"id": "p1", "nickname": "Old", "updated_at": "2026-05-01T00:00:00Z"},
                {"id": "p2", "nickname": "New", "updated_at": "2026-05-03T00:00:00Z"},
            ]},
        )

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc"},
            "queryStringParameters": {"updatedSince": "2026-05-02T00:00:00Z"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        ids = [p["id"] for p in json.loads(result["body"])["players"]]
        assert ids == ["p2"]

    def test_date_from_on_players_returns_400(self) -> None:
        from list_players import handler

        mock_s3 = _mock_s3()
        self._wire(mock_s3)

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc"},
            "queryStringParameters": {"dateFrom": "2022-11-20"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "dateFrom" in json.loads(result["body"])["error"]

    def test_date_to_on_players_returns_400(self) -> None:
        from list_players import handler

        mock_s3 = _mock_s3()
        self._wire(mock_s3)

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc"},
            "queryStringParameters": {"dateTo": "2022-12-19"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "dateTo" in json.loads(result["body"])["error"]

    def test_no_filters_returns_all_backwards_compat(self) -> None:
        from list_players import handler

        mock_s3 = _mock_s3()
        self._wire(
            mock_s3,
            public_payload={"provider": "sc", "players": [
                {"id": "p1", "nickname": "A"},
                {"id": "p2", "nickname": "B"},
            ]},
        )

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 200
        assert len(json.loads(result["body"])["players"]) == 2

    def test_invalid_updated_since_returns_400(self) -> None:
        from list_players import handler

        mock_s3 = _mock_s3()
        self._wire(mock_s3)

        event = {
            "headers": {"authorization": "Bearer pub-tok"},
            "pathParameters": {"provider": "sc"},
            "queryStringParameters": {"updatedSince": "garbage"},
        }
        result = handler(event, None)
        assert result["statusCode"] == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src && uv run pytest tests/test_lambda_handlers.py::TestListPlayersFiltering -v`
Expected: FAIL — filters not wired in `list_players.py`.

- [ ] **Step 3: Wire filters into `list_players.py`**

Replace the full content of `terraform/modules/functions/src/list_players.py`:

```python
"""GET /v1/{provider}/players — list players for a provider, filtered by tier."""

from __future__ import annotations

import json
import os

from shared import (
    Tier,
    apply_filters,
    get_s3_client,
    json_response,
    logger,
    parse_query_filters,
    validate_path_param,
    validate_token,
)

BUCKET = os.environ.get("DATA_BUCKET", "")

_ALLOWED_FILTERS = {"updatedSince"}


def handler(event: dict, context: object) -> dict:
    """Return the player catalogue for a provider, merged across visible tiers.

    Gates on providers.json membership for unknown-provider 404 (spec §6.4).
    Owner-tier merge applies private-wins precedence on cross-tier ID
    collision (spec §6.3.1).

    Query filters (spec: 2026-05-07-query-parameter-filtering §3.1):
    Only updatedSince is supported. dateFrom/dateTo return 400.
    """
    tier = validate_token(event)
    if isinstance(tier, dict):
        logger.warning("auth_failure", extra={"handler": "list_players"})
        return tier

    provider = (event.get("pathParameters") or {}).get("provider", "")
    param_error = validate_path_param(provider, "provider")
    if param_error:
        logger.warning("validation_failure", extra={"handler": "list_players", "param": "provider"})
        return param_error

    filters = parse_query_filters(event, allowed=_ALLOWED_FILTERS)
    if isinstance(filters, dict) and "statusCode" in filters:
        return filters

    s3 = get_s3_client()
    if not _provider_known(s3, provider):
        return json_response(404, {"error": "Provider not found"})

    public_players = _read_index(s3, f"{provider}/players.json")
    private_players = _read_index(s3, f"{provider}/_private/players.json") if tier == Tier.OWNER else []

    # Private-wins precedence on cross-tier ID collision (spec §6.3.1).
    by_id: dict[str, dict] = {}
    for pub in public_players:
        pid = pub.get("id")
        if isinstance(pid, str):
            by_id[pid] = pub
    for priv in private_players:
        pid = priv.get("id")
        if isinstance(pid, str):
            by_id[pid] = priv  # overwrite any same-id public entry

    players = apply_filters(list(by_id.values()), filters)

    return json_response(200, {"provider": provider, "players": players})


def _provider_known(s3, provider: str) -> bool:
    """Check that `provider` appears in providers.json. Returns True if so."""
    try:
        obj = s3.get_object(Bucket=BUCKET, Key="providers.json")
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return provider in (data.get("providers") or [])
    except s3.exceptions.NoSuchKey:
        return False
    except Exception:
        logger.exception("s3_error", extra={"handler": "list_players", "key": "providers.json"})
        return False


def _read_index(s3, key: str) -> list[dict]:
    """Read a players index from S3. Returns [] if the index doesn't exist."""
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return data.get("players", [])
    except s3.exceptions.NoSuchKey:
        return []
    except Exception:
        logger.exception("s3_error", extra={"handler": "list_players", "key": key})
        return []
```

- [ ] **Step 4: Run filtering tests to verify they pass**

Run: `cd src && uv run pytest tests/test_lambda_handlers.py::TestListPlayersFiltering -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Run full test suite to verify no regressions**

Run: `cd src && uv run pytest tests/ -v`
Expected: All tests PASS across all test files.

---

### Task 4: Lint, type-check, and commit

**Files:**
- All modified files from Tasks 1–3

- [ ] **Step 1: Run ruff linter**

Run: `cd src && uv run ruff check ../terraform/modules/functions/src/ tests/test_lambda_handlers.py`
Expected: No errors. If any, fix and re-run.

- [ ] **Step 2: Run ruff formatter**

Run: `cd src && uv run ruff format ../terraform/modules/functions/src/ tests/test_lambda_handlers.py`
Expected: Files reformatted (or already formatted).

- [ ] **Step 3: Run pyright type-check**

Run: `cd src && uv run pyright ../terraform/modules/functions/src/shared.py`
Expected: No errors. The `datetime` import and new functions should type-check cleanly.

- [ ] **Step 4: Run the full test suite one final time**

Run: `cd src && uv run pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit all changes on a feature branch**

```bash
git checkout -b feat/query-parameter-filtering
git add \
  docs/superpowers/specs/2026-05-07-query-parameter-filtering.md \
  docs/superpowers/plans/2026-05-07-query-parameter-filtering.md \
  terraform/modules/functions/src/shared.py \
  terraform/modules/functions/src/list_matches.py \
  terraform/modules/functions/src/list_players.py \
  src/tests/test_lambda_handlers.py
git commit -m "feat(api): add updatedSince, dateFrom, dateTo query filters to list endpoints

Adds server-side filtering to GET /{provider}/matches and
GET /{provider}/players for incremental polling. Aligned with
football-data.org and Sportradar conventions.

- updatedSince (ISO 8601, exclusive) on both endpoints
- dateFrom (YYYY-MM-DD, inclusive) and dateTo (exclusive) on matches only
- dateFrom/dateTo on list_players returns 400
- Invalid values return 400 with descriptive error
- Null fields excluded when filter active
- No Terraform changes needed (API Gateway passes query strings through)

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```
