# IDSSE Bundesliga Public Redistribution — Design Spec

**Date:** 2026-05-29
**Status:** Draft — revised after silly-kicks/TF-24 cross-repo review (rounds 1–2)
**Scope:** Add the IDSSE/Sportec open Bundesliga dataset as a new **public** provider (`idsse`) served through the existing mock provider API. Raw DFL/Sportec XML redistributed as-is.

---

## 1. Purpose

Several companion repos (e.g. `silly-kicks`) need the open IDSSE Bundesliga tracking + event data. Today they reach into Databricks bronze tables to get it, which couples those repos to the lakehouse's internal storage layout and credentials. Routing the data through `pining-for-the-data` gives consumers a stable, documented, provider-shaped contract — the same one they already use for SkillCorner — and consolidates licence attribution at a single surface.

This spec adds the IDSSE dataset as a public provider. It is pure redistribution of CC-BY-4.0-licensed open data; no de-identification and no format transformation are applied.

## 2. Licence Basis

The dataset is **CC-BY 4.0**, published *with authorization of the Deutsche Fußball Liga (DFL)* — the rights-holder itself consented to the open release (consent secured through the player-registration process). CC-BY permits redistribution and adaptation, including commercial use, with attribution. This removes the usual redistribution risk and makes the **public** tier appropriate (no owner-gating required).

- **Source publication:** Bassek, M., Rein, R., Weber, H., & Memmert, D. (2025). *An integrated dataset of spatiotemporal and event data in elite soccer.* Scientific Data, 12(1), 195.
- **Data host (authoritative bytes):** figshare, DOI [10.6084/m9.figshare.28196177](https://doi.org/10.6084/m9.figshare.28196177). The load pins the **version-specific** DOI (e.g. `…28196177.v1`), not the always-latest base DOI — see §2.2.
- **Coverage:** 7 full matches (raw position data at 25 fps + event data + match information), German Bundesliga 1. & 2. division, season 2022/23.

CC-BY attribution is a binding condition, addressed in §7.

### 2.1 Provenance constraint

Only the figshare release is CC-BY. The bronze Delta tables hold *parsed* data, not the original XML, and the licence travels with the figshare artifact, not with whatever has been derived downstream. Therefore the raw XML is sourced **directly from the figshare DOI** at load time, not reconstructed from bronze. This is the gold-standard provenance path: the exact licensed bytes, untouched.

### 2.2 Reproducible, byte-stable fetch (review M1)

figshare's base article endpoint (`/v2/articles/28196177`) resolves to the *latest* version of an article. If the authors publish a new version, a re-run that queries the base endpoint would silently fetch different bytes (and different md5s, so md5 verification alone would not detect the drift). To make the load reproducible **and** fetchable at a fixed version:

- **The loader queries the version-specific endpoint** — `/v2/articles/28196177/versions/1` (N2) — not the base article. This both pins the bytes and keeps the correct file listing reachable after a hypothetical v2 is published. The version-pinned DOI (`…28196177.v1`) is the human-facing form of the same pin.
- A committed manifest (`scripts/idsse_figshare_manifest.json`) records the **expected file inventory**: 21 files, each with its filename and md5. The loader asserts the live versioned listing matches the manifest (count + per-file md5) before downloading, and verifies each downloaded file's md5 against it.
- **The manifest is reproducible, not hand-built** (N6): the loader exposes a `--write-manifest` mode that fetches the versioned listing and writes the manifest file, so the pin can be re-derived and diffed in review rather than being a hand-curated artifact of unknown origin. A mismatch on a normal run is a hard failure with a clear message, not a silent re-pull.

(figshare-provided md5 is an acceptable integrity source — it is the checksum the host itself exposes.)

## 3. Data Form & Naming

**Form:** raw DFL/Sportec XML, served byte-for-byte. This is the licence-cleanest option (pure redistribution, not an "adaptation"), the least code, and consistent with the SkillCorner precedent (redistributed in its native format). Consumers parse the DFL XML with existing tooling (`floodlight`, `kloppy`).

**Provider slug:** `idsse`.

**figshare file inventory** (confirmed via the figshare API for article 28196177): 21 individual XML files (no archive), three per match across 7 matches, ~2.63 GB total. Naming pattern:

| DFL file pattern | Role | Approx. size |
|---|---|---|
| `DFL_02_01_matchinformation_<COM-ID>_<MAT-ID>.xml` | Match metadata (teams, players, kickoff) | ~12 KB |
| `DFL_03_02_events_raw_<COM-ID>_<MAT-ID>.xml` | Event data | large |
| `DFL_04_03_positions_raw_observed_<COM-ID>_<MAT-ID>.xml` | Position/tracking data (25 fps) | large (dominates the 2.63 GB) |

Company IDs observed: `DFL-COM-000001` (2 matches), `DFL-COM-000002` (5 matches).

### 3.1 Provider-side naming

- **`game_id`** = the real `DFL-MAT-…` match id parsed from the matchinformation XML. It is stable, regex-safe (`^[a-zA-Z0-9][a-zA-Z0-9_-]*$`), ≤128 chars, and part of the public CC-BY release (not licensed/restricted data).
- **Artifact keys** (the API allowlist, per `MatchEntry.artifacts`): clean names aligned to the **Gradient Sports artifact vocabulary** so the keys describe the *role*, not the DFL filename or the wire format (review H1):

  | Artifact key | Staged filename | DFL source file | Role |
  |---|---|---|---|
  | `metadata` | `metadata.xml` | `…matchinformation…` | match/team/player metadata |
  | `events` | `events.xml` | `…events_raw…` | event data |
  | `tracking` | `tracking.xml` | `…positions_raw_observed…` | position/tracking data (25 fps) |

  Each key satisfies the path-param regex, so the API will serve it. IDSSE has no `roster` artifact — roster lives inside `metadata` (matchinformation) — and a provider exposing a *subset* of the vocabulary is expected; the contract is that a shared key denotes the same role across providers.

  **Role-alignment, not format-alignment.** `tracking` for `idsse` returns position *XML*; for Gradient Sports it returns JSONL. A provider-agnostic consumer (e.g. the silly-kicks TF-24 loader) selects artifacts by role key, then dispatches to a provider-specific parser (`idsse → Sportec/floodlight`). The wire format is intentionally not encoded in the key.

  **Known divergence (documented for consumers):** the legacy `skillcorner` provider predates this convention and uses id-prefixed keys (`<match_id>_match`, `tracking`) per `scripts/backfill_skillcorner_artifacts.py`. A single uniform vocabulary does not exist across *all* current providers; `idsse` deliberately matches the **newer Gradient Sports** convention. Consumers still need a small per-provider map for SkillCorner specifically. This convention is codified in **ADR 0008** (role-aligned artifact-key vocabulary).

## 4. Architecture & Components

No Lambda or Terraform changes — the API is already provider-generic (`list_matches`, `get_artifact`, `list_providers` operate off `{provider}/matches.json` and `providers.json` for any provider). New code units (reader, loader, post-load verifier) plus a committed manifest and attribution updates, all following existing patterns.

| Component | Responsibility | Depends on |
|---|---|---|
| `src/formats/idsse.py` | Lightweight reader. Parses **only** the ~12 KB (namespace-free) matchinformation XML into index metadata `{match_id, date, home, away}` (date via `Europe/Berlin` conversion, §6). Exposes the DFL filename-pattern constants and `group_files_by_match(filenames)`. **Does not** parse positions/events. | stdlib `xml.etree`, `zoneinfo` |
| `scripts/upload_idsse_bundesliga.py` | Public-tier worked ops adapter (sibling of `upload_gradient_wc2022.py`). Fetches the **versioned** figshare endpoint → manifest-verify (count + md5) → group by match → stage with role-aligned names → `upload_game(...)`. Idempotent. Also exposes `--write-manifest` to (re)generate the committed manifest from the versioned listing (N6). | `formats.idsse`, `mock_api.upload.upload_game`, figshare API |
| `scripts/idsse_figshare_manifest.json` | Committed expected-file manifest (21 files + md5s) pinning the figshare version for byte-stable re-runs; reproducible via `--write-manifest` (§2.2). | — |
| `scripts/verify_idsse_load.py` | Post-load verification (mirror of `verify_gradient_load.py`): asserts 7 matches / 21 artifacts, **public**-tier visibility, a date-filtered query returns the expected window, and artifact downloads return 200 + non-empty (§9). | live API |
| `NOTICE` | Add IDSSE attribution stanza (§7). | — |
| `README.md` / provider docs | Document the new public provider + attribution. | — |
| `src/tests/test_idsse_format.py` | Reader + grouping unit tests, plus the opt-in real-XML reader e2e (§9). | synthetic XML fixture; figshare (e2e only) |

### 4.1 Why a `formats/idsse.py` module rather than inline script logic

It mirrors the repo's established provider-onboarding shape (`formats/` reader + worked adapter in `scripts/`), keeps the only parsing surface small and unit-testable in isolation, and isolates the one fragile piece (DFL XML attribute names) behind a tested interface. The positions/events files are never parsed — preserving "as-is" — so the module stays tiny.

## 5. Data Flow

```
figshare (pinned version DOI, article 28196177)
  → list files; assert listing matches committed manifest (21 files, md5 set) — §2.2
  → download each to a temp dir (streamed to disk), verify md5 against manifest
  → formats.idsse.group_files_by_match()  →  7 matches × {metadata, events, tracking}
  → for each complete match:
        meta = formats.idsse.read_match_information(<matchinformation file>)
               → {match_id, date (YYYY-MM-DD), home, away}
        stage the 3 files into a temp dir under role-aligned names:
               metadata.xml / events.xml / tracking.xml
        upload_game(
            game_dir=<staging>, provider="idsse", game_id=meta.match_id,
            bucket=..., visibility="public", provenance="redistributed",
            date=meta.date, home=meta.home, away=meta.away,
            source_name="IDSSE — Bassek, Rein, Weber & Memmert (2025); provided by DFL / Sportec Solutions",
            source_url="https://doi.org/10.6084/m9.figshare.28196177.v1",
            source_licence="CC-BY 4.0",
        )
  → upload_game updates {idsse}/matches.json + providers.json atomically (existing primitives)
```

Consumer access uses the **unchanged** API. The artifact route is `/{provider}/matches/{id}/{artifact}` — there is **no** `/artifacts/` path segment (confirmed against `get_artifact.py` and `verify_gradient_load.py`; review M3):

```
GET /v1/idsse/matches?dateFrom=2022-11-01&dateTo=2023-06-01      # filtered list
GET /v1/idsse/matches/{DFL-MAT-id}/tracking                      # 302 → presigned S3 URL
```

### 5.1 Large-file handling

Positions/events files are hundreds of MB. `boto3.upload_file` performs multipart upload automatically. The `get_artifact` handler already serves via a presigned-URL 302 redirect, so file size is not a concern at serve time. The adapter streams downloads to disk (never holds a full file in memory) and stages per-match to bound temp-disk usage.

## 6. Index Metadata & API Filter Parity

The requirement is full parity with other providers' API functionality, in particular date filtering. The existing filters (`src/.../shared.py: apply_filters`) key on:

| Filter | MatchEntry field | Populated by |
|---|---|---|
| `dateFrom` / `dateTo` (YYYY-MM-DD) | `date` | matchinformation XML parse |
| `updatedSince` (ISO 8601) | `updated_at` | set automatically by `upload_game` |

So parity is achieved purely by populating `date` on each `MatchEntry`. `home`/`away` are also populated for display consistency with the SkillCorner and Gradient Sports providers (they are not filtered fields today, but absent values would be an inconsistency).

**Timezone (N3, refined against real v1 data):** `General@KickoffTime` is a **tz-aware** ISO 8601 timestamp, stored with a **UTC** offset (e.g. `2023-05-27T13:31:08.640+00:00`) — *not* a local CET/CEST wall-clock as round-1 assumed. `date` must be the **local match date**, so the reader parses the tz-aware timestamp and converts to `Europe/Berlin` (`zoneinfo.ZoneInfo`) before taking `.date()`. This is correct regardless of which offset DFL encodes (taking the raw UTC date component happens to work for afternoon/evening kickoffs but is fragile; the Berlin conversion is principled and free). Fall back to `PlannedKickoffTime` if `KickoffTime` is absent.

### 6.1 DFL XML attribute verification

Real v1 schema (confirmed by reading one matchinformation file): root `<PutDataRequest>` → `<MatchInformation>` → `<General>` with the metadata as **attributes** — `MatchId`, `KickoffTime`/`PlannedKickoffTime`, `HomeTeamName`/`HomeTeamId`, `GuestTeamName`/`GuestTeamId` (home/away role is encoded by the attribute *prefix*, Home vs Guest). The file is **namespace-free** — no `xmlns` on any element — so the `xml.etree` reader reads bare tags/attributes; no namespace resolution is needed for the only file we parse. (The positions/events files are never parsed.)

The reader is still guarded against schema surprises (review H2), not deferred to a manual first run:

1. The unit-test fixture is a **synthetic** matchinformation XML (no real licensed identifiers in the repo, per project convention) reproducing this real attribute *shape* (root → `MatchInformation` → `General` with the attributes above, plus a tz-aware `+00:00` `KickoffTime`).
2. An **opt-in, network-gated e2e test** (§9) fetches one real ~12 KB matchinformation file from the pinned versioned figshare endpoint and asserts the reader extracts well-formed `match_id` / `date` / `home` / `away` (asserting *shape*, not hardcoded real values — see §9, N5). This catches any real-schema drift automatically — the class of bug a synthetic fixture cannot. It is skipped by default (gated on an env var / marker) so the core unit suite stays hermetic and CI is not figshare-dependent.

## 7. Attribution — CC-BY 4.0 Compliance

CC-BY requires attributing the **creators** (the dataset authors), not only the rights-holder/authorizer (review M2). The per-match `source` block is what propagates downstream to consumers, so it must carry the author attribution — not just "DFL/Sportec". Attribution is surfaced in three places (items 1, 3, 4), with item 2 clarifying the related `provenance` field:

1. **Per-match `source` block in `matches.json`** (served to consumers via the API). `_SourceMeta` has exactly `name`/`url`/`licence` (no `extra`), so the author attribution lives in `name`:
   - `name`: `"IDSSE — Bassek, Rein, Weber & Memmert (2025); provided by DFL / Sportec Solutions"`
   - `url`: `"https://doi.org/10.6084/m9.figshare.28196177.v1"` (version-pinned, §2.2)
   - `licence`: `"CC-BY 4.0"`
2. **`provenance` is a separate top-level `MatchEntry` field** (N1), *not* part of the `source` block — set to `"redistributed"` and passed as the standalone `upload_game(provenance=…)` kwarg (see `_build_match_entry`, which writes `payload["provenance"]` alongside `source`). It is also served to consumers in each match entry. This matches how Gradient Sports records provenance.
3. **`NOTICE`** — add an IDSSE stanza with the **full** Bassek et al. (2025) *Scientific Data* citation and the DFL authorization statement.
4. **`README.md` / provider docs** — list `idsse` as a public provider with its source and licence.

## 8. Error Handling

| Condition | Behaviour |
|---|---|
| figshare API unreachable / non-200 | Fail with a clear message including the DOI; no partial upload. |
| MD5 mismatch or short download | Fail that file loudly; do not upload an incomplete match. |
| Incomplete triplet (a match missing one of its 3 files) | Skip the match with an explicit error (mirrors the gradient adapter's missing-file guard). |
| Malformed matchinformation XML | Reader raises a clear exception; match skipped, not silently defaulted. |
| Re-run / partial prior run | Idempotent: `upload_game` updates in place by `game_id`; the existing no-tier-mixing guard blocks an accidental public↔private flip. |
| `--limit N` (smoke test) | Upload only the first N matches, like the gradient adapter. |

## 9. Testing

- **Reader unit** (`test_idsse_format.py`): synthetic, **namespaced** matchinformation XML fixture → asserts extracted `match_id`, `date` (YYYY-MM-DD), `home`, `away`; malformed XML raises.
- **Grouping unit**: a list of 21 filenames → 7 complete triplets keyed by match id; a missing file → match flagged as incomplete; manifest-mismatch (wrong count/md5) → hard failure.
- **Adapter staging logic**: mocked figshare listing + mocked downloads (no real network, no real S3) → asserts role-aligned artifact names (`metadata`/`events`/`tracking`) are staged and `upload_game` is called with the expected kwargs (provider, visibility, provenance, date/home/away, source\_\*).
- **Reader real-XML e2e (opt-in, network-gated; review H2)**: fetches one real ~12 KB matchinformation file from the pinned versioned figshare endpoint and asserts the reader extracts well-formed values — `match_id` matches the path-param regex, `date` matches `YYYY-MM-DD`, `home`/`away` non-empty (N5: **assert shape, not hardcoded real identifiers**, so no licensed DFL ids land in committed test code, consistent with the synthetic-fixture convention). Skipped by default (env-var/marker gated) so the core suite is hermetic. This is the highest-value test — the only one that exercises the reader against the real DFL schema + namespaces.
- **Post-load verification** (`scripts/verify_idsse_load.py`; review H2): run after the loader against the live API — asserts 7 matches / 21 artifacts present, **public**-tier visibility (public token sees all 7; existence in `/providers` is fine), and a `dateFrom`/`dateTo` query returns the expected match window. Artifact checks are size-aware (N4): the large `tracking`/`events` artifacts are validated by following the 302 to the presigned URL and issuing a **`Range: bytes=0-0` GET** (a GET — the presigned URL is signed for GET, so HEAD would fail signature validation) and asserting `206` + a positive `Content-Range` total; only the ~12 KB `metadata` is fully downloaded for one real-body check.
- **Gate (Shift Left)**: `ruff`, `pyright` (basic), and `pytest` (hermetic subset) all green before completion; run the `final-review` skill before the final commit.

## 10. Out of Scope

- **HuggingFace Hub** — `hf_push.py` stays SkillCorner-only; the chosen channel is the provider API.
- **`/players` catalogue** — match artifacts only (`metadata` / `events` / `tracking`); no separate players catalogue. Player metadata already lives inside the served `metadata` (matchinformation) XML; a catalogue can be added later if a consumer needs it.
- **Format normalization / conversion** — none. Raw XML only.
- **Terraform / Lambda changes** — none. The API is already provider-generic.

### 10.1 Consumer cost (informational; not a blocker)

Serving raw DFL XML means consumers parse positions/events at load time via `kloppy`/`floodlight` (vs. the Gradient Sports path where bronze had pre-converted SPADL actions). The silly-kicks side has accepted this: the pining-IDSSE path is heavier than reading bronze directly, which is fine for a full calibration sweep. Recorded here so the tradeoff is explicit and not rediscovered later (Hyrum's Law / no-surprises).

## 11. Resolved Review Items

Round-1 review (silly-kicks/TF-24) is incorporated above:

| Item | Resolution |
|---|---|
| H1 — artifact-key vocabulary | Role-aligned to Gradient Sports: `metadata` / `events` / `tracking` (§3.1). SkillCorner's id-prefixed keys documented as a known divergence. |
| H2 — TDD/e2e gap | Opt-in network-gated real-XML reader e2e + `verify_idsse_load.py` post-load check (§4, §6.1, §9). |
| M1 — figshare version drift | Version-pinned DOI + committed 21-file/md5 manifest with assertion on re-run (§2.2). |
| M2 — author attribution | `source.name` now names the dataset authors, not only DFL/Sportec (§7). |
| M3 — route path | Corrected to `/{provider}/matches/{id}/{artifact}` (no `/artifacts/`) (§5). |
| Namespaces | Investigated against real data — see the round-2 table; the matchinformation XML is namespace-free, so the handling is not needed. |

Round-2 review:

| Item | Resolution |
|---|---|
| N1 — provenance contract | Clarified: `provenance` is a top-level `MatchEntry` field (passed as the standalone `upload_game(provenance=…)` kwarg), **not** part of the `name`/`url`/`licence`-only `source` block; served to consumers like Gradient Sports (§7). |
| N2 — versioned fetch | Loader queries the version-specific endpoint `/v2/articles/28196177/versions/1`, not the base article (§2.2, §4, §5). |
| N3 — date timezone | Refined against real data: `KickoffTime` is tz-aware **UTC** (`+00:00`), not local. Reader converts to `Europe/Berlin` then takes `.date()` for the correct local match date (§6). |
| N4 — verify download size | Large `tracking`/`events` validated via a `Range: bytes=0-0` GET (206 + positive total), not a full body read; only ~12 KB `metadata` fully downloaded (§9). |
| N5 — e2e identifiers | Real-XML e2e asserts value *shape* (regex / `YYYY-MM-DD` / non-empty), not hardcoded real DFL ids (§9). |
| N6 — reproducible manifest | Loader `--write-manifest` mode regenerates the manifest from the versioned listing (§2.2, §4). |
| Namespaces (round-1/2 note) | **Withdrawn against real data:** the matchinformation XML is namespace-free, so no namespace handling is needed in the reader (§6.1). |

Open items from round 1, all resolved by the reviewer:

1. Provider slug `idsse` — **confirmed** (matches published dataset name and silly-kicks bronze tables `idsse_tracking`/`idsse_events`).
2. `game_id` = real `DFL-MAT-…` id — **confirmed** (stable, regex-safe, preserves link to the authoritative id).
3. Attribution wording — **resolved** per M2 (authors named in `source.name`).
