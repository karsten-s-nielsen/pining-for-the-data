# Query Parameter Filtering for List Endpoints — Design Spec

**Date:** 2026-05-07
**Status:** Approved
**Scope:** Server-side filtering on `list_matches` and `list_players` Lambda handlers

---

## 1. Purpose

Downstream consumers polling the mock provider API need an efficient way to detect new or updated data without re-processing the entire catalogue every time. This is standard practice across commercial sports data providers (football-data.org, Sportradar, Sportmonks, API-Football).

Adding query-parameter filtering to the two collection endpoints (`list_matches`, `list_players`) brings the mock API in line with industry conventions before the repo goes public.

## 2. Research Summary

Six major providers were investigated. Three established patterns emerged:

| Pattern | Providers | Mechanism |
|---------|-----------|-----------|
| Date-range filtering on list endpoint | football-data.org, API-Football, Sportmonks | `dateFrom` + `dateTo` query params (YYYY-MM-DD) |
| Dedicated delta/changelog endpoints | Sportradar | Separate endpoints returning IDs changed in last 24h |
| `updated_at` field + client-side filtering | StatsBomb, Sportmonks (fallback) | Timestamp on each record, consumer sorts/filters locally |

**Decision:** Date-range + `updatedSince` query parameters on existing endpoints. Dedicated delta endpoints are overkill for our scale (tens/hundreds of matches). No new Lambdas or Terraform routes required.

## 3. Query Parameters

Three optional query-string parameters on `GET /{provider}/matches` and `GET /{provider}/players`:

| Parameter | Format | Semantics | Field filtered |
|-----------|--------|-----------|----------------|
| `updatedSince` | ISO 8601 UTC (e.g., `2026-05-01T00:00:00Z`) | Exclusive: `updated_at > value` | `updated_at` |
| `dateFrom` | `YYYY-MM-DD` | Inclusive: `date >= value` | `date` |
| `dateTo` | `YYYY-MM-DD` | Exclusive: `date < value` | `date` |

### 3.1 Applicability

| Parameter | `list_matches` | `list_players` |
|-----------|---------------|---------------|
| `updatedSince` | Yes | Yes |
| `dateFrom` | Yes | **400 Bad Request** |
| `dateTo` | Yes | **400 Bad Request** |

`dateFrom`/`dateTo` filter on the match `date` field, which players do not have. Passing either to `list_players` returns `400` with a descriptive error.

### 3.2 Combination

All three parameters can be combined freely on `list_matches`:

```
GET /v1/skillcorner/matches?updatedSince=2026-05-01T00:00:00Z&dateFrom=2022-11-20&dateTo=2022-12-19
```

Filters are applied conjunctively (AND). A match must pass all active filters to be included.

### 3.3 Boundary Semantics

- **`updatedSince`** — exclusive (`updated_at > value`). Consumers store the `updated_at` of the most recent record they received and pass it back verbatim on the next poll. No off-by-one risk.
- **`dateFrom`** — inclusive (`date >= value`). "Give me matches from this date onward."
- **`dateTo`** — exclusive (`date < value`). Matches football-data.org convention: `dateTo=2022-12-19` includes matches through 2022-12-18.
- **`dateFrom` and `dateTo` are independent** — either can be used alone. `dateFrom` without `dateTo` means "everything from that date onward." `dateTo` without `dateFrom` means "everything before that date."

### 3.4 Null Field Handling

Records where the filtered field is `null` or missing are **excluded** when that filter is active. Rationale: returning unclassified records risks leaking data that hasn't been properly tagged. If no filters are active, all records are returned (existing behavior preserved).

### 3.5 Validation

- Invalid `updatedSince` (not parseable as ISO 8601) → `400` with `{"error": "Invalid updatedSince: expected ISO 8601 UTC timestamp (e.g., 2026-05-01T00:00:00Z)"}`
- Invalid `dateFrom` or `dateTo` (not parseable as YYYY-MM-DD) → `400` with `{"error": "Invalid dateFrom: expected YYYY-MM-DD"}`
- Unrecognized query parameters are silently ignored (standard REST practice).

### 3.6 Consumer Polling Pattern

Recommended polling pattern for a downstream consumer (e.g., luxury-lakehouse ingestion):

```python
# First poll — get everything
resp = GET /v1/skillcorner/matches
high_water = max(m["updated_at"] for m in resp["matches"])

# Subsequent polls — incremental
resp = GET /v1/skillcorner/matches?updatedSince={high_water}
if resp["matches"]:
    process(resp["matches"])
    high_water = max(m["updated_at"] for m in resp["matches"])
```

## 4. Implementation Scope

### 4.1 Lambda Handler Changes (no new Lambdas)

**`shared.py`** — add `parse_query_filters(event, allowed)` helper:
- Extracts `updatedSince`, `dateFrom`, `dateTo` from `event["queryStringParameters"]`.
- Validates format. Returns parsed filter dict on success, or error response dict on failure (same pattern as `validate_token` / `validate_path_param`).
- `allowed` parameter controls which filters the caller accepts (so `list_players` can reject `dateFrom`/`dateTo`).

**`list_matches.py`** — after the existing visibility filter, apply filters to the matches list using the parsed filter dict.

**`list_players.py`** — after the tier merge, apply `updatedSince` filter. Reject `dateFrom`/`dateTo` with `400`.

### 4.2 Canonical Model Changes

None. `MatchEntry.updated_at`, `MatchEntry.date`, and `PlayerRecord.updated_at` already exist with the right types.

### 4.3 Terraform Changes

None. API Gateway HTTP API (v2) with `AWS_PROXY` integration passes query strings through to the Lambda event automatically. No route, integration, or permission changes needed.

### 4.4 Upload CLI Changes

None. The upload CLIs already set `updated_at` on every write and `date` on match entries.

## 5. Test Plan

Unit tests in `test_lambda_handlers.py`:

| Test case | Endpoint | Asserts |
|-----------|----------|---------|
| `updatedSince` filters correctly (exclusive) | `list_matches` | Only records with `updated_at > value` returned |
| `dateFrom` filters correctly (inclusive) | `list_matches` | Only records with `date >= value` returned |
| `dateTo` filters correctly (exclusive) | `list_matches` | Only records with `date < value` returned |
| Combined `dateFrom` + `dateTo` | `list_matches` | AND logic, correct window |
| Combined all three filters | `list_matches` | AND logic across all filters |
| Null `date` excluded when `dateFrom` active | `list_matches` | Record with `date: null` not in results |
| No filters → full list (backwards compat) | `list_matches` | Existing behavior preserved |
| `updatedSince` on `list_players` | `list_players` | Filters correctly |
| `dateFrom` on `list_players` → 400 | `list_players` | Error response, no data leakage |
| `dateTo` on `list_players` → 400 | `list_players` | Error response, no data leakage |
| Invalid `updatedSince` → 400 | `list_matches` | Descriptive error message |
| Invalid `dateFrom` → 400 | `list_matches` | Descriptive error message |
| Visibility filter still applied with query filters | `list_matches` | Public tier cannot see private matches even with filters |

## 6. Non-Goals

- **Pagination** — not needed at current scale (tens/hundreds of records per provider).
- **`If-Modified-Since` / `ETag` headers** — useful optimization but orthogonal. Can be added later without breaking changes.
- **Dedicated delta endpoints** (Sportradar-style) — overkill for our scale. The query-parameter approach is the standard for our tier.
- **`updatedBefore`** — no identified use case. Can be added later if needed.
