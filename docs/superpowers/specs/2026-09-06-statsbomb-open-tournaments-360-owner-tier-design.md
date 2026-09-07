# StatsBomb Open-Data Tournaments 360 — Owner-Tier Second Source Family

**Status:** Draft (revision 2)
**Date:** 2026-09-06
**Revision 2:** addresses independent-review findings — SBO-SPEC-01 (full
`build_metadata_open` field-source map, §4.1), SBO-SPEC-02 (shared-staging extraction,
§6.5), SBO-SPEC-03 (transposed-pair guard, §6.4), SBO-SPEC-04 (intra-run player dedup,
§5.2).
**Related:** ADR 0002 (private-prefix tier separation), ADR 0005 (single-bucket
multi-tier), ADR 0008 (role-aligned artifact keys), ADR 0009 (restricted tier under
an existing public provider), ADR 0010 (faithful-feed mimicry), ADR 0011 (canonical
owner-tier SkillCorner Parquet — *considered and rejected here*, §3 D-2), and the
commercial-360 design (`2026-08-12-statsbomb-commercial-360-owner-tier-design.md`).

---

## 1. Context

The existing `statsbomb` provider serves one thing: a **commercial** 360 club
delivery (Racing Louisville, NWSL), owner-tier only, `provenance="original"`. This
design adds a **second source** under the same slug: StatsBomb's **public open-data**
360 corpus, for the six competitions whose 360 coverage is a *complete tournament*.

**The six tournaments (292 matches, all with 360):**

| Competition | Gender | `competition_id` | `season_id` | Matches |
|---|---|--:|--:|--:|
| FIFA World Cup 2022 | M | 43 | 106 | 64 |
| Women's World Cup 2023 | F | 72 | 107 | 64 |
| UEFA Euro 2020 | M | 55 | 43 | 51 |
| UEFA Euro 2024 | M | 55 | 282 | 51 |
| UEFA Women's Euro 2022 | F | 53 | 106 | 31 |
| UEFA Women's Euro 2025 | F | 53 | 315 | 31 |

These six were chosen because their 360 coverage is **100% of a real tournament**
(every match, all teams) — the highest-signal, least-caveated slice of the open 360
corpus. The single-team open seasons (Barcelona/PSG/Leverkusen/Inter Miami) and the
AFCON-2023 one-match stray are explicitly **out of scope** (§10).

### 1.1 The licence is the load-bearing fact

StatsBomb open data is **freely accessible but not freely redistributable**. Two
documents govern it and they differ:

- The repo `README.md` carries a soft note: *"If you publish, share or distribute any
  research, analysis or insights **based on this data**, please state the data source
  as StatsBomb and use our logo."* — an attribution ask about **derived analysis**,
  not permission to redistribute the raw data.
- The binding `LICENSE.pdf` — **"StatsBomb Public Data User Agreement"** (last updated
  8 September 2023) — is restrictive:
  - **1.2.1** the User may not *"edit, distort, distribute, reproduce, sell or in any
    way **provide the data to any external or third party**."*
  - **1.2.2** no commercial exploitation of the data or derived analysis.
  - **7 (IP Rights)** all data is StatsBomb's property; no *"transfer, distribute,
    license, sell or otherwise exploit … without the express prior written consent of
    StatsBomb."*

**Consequence for the tier.** A pining **public** tier hands the documented public
`api_token` to consumers — literally "provide the data to a third party" — so it is
**forbidden**. The **owner** tier, gated to a single bearer token held only by the
operator, is a **party of one**: no external or third party exists, so 1.2.1/1.2.2 are
not triggered. Copying the JSON onto our own S3 for our own use is materially the same
act as `statsbombpy` caching the raw feed to local disk. The load-bearing condition is
that the data **stays single-user end-to-end** (§8) — the analysis flips to a breach
the moment the raw bytes reach another person.

So this data lives at the **owner tier**, with `provenance="redistributed"` recording
that it is StatsBomb's data we re-host (not our original), and a `source.licence`
string recording the restriction. This is a **new (`redistributed`, `private`)
combination**: existing redistributed providers (SkillCorner A-League, IDSSE) are
`public`; existing private data (`gradientsports`, restricted SkillCorner, commercial
`statsbomb`) is `original`. The combination is meaningful, not accidental.

### 1.2 Why this needs a design rather than a passthrough

Unlike the commercial delivery — a shape-mixed archive whose two pandas
*column-orient* files were "the entire risk surface" (commercial spec §1.1) — the open
data **is StatsBomb's real published feed**. There is no de-pivot, no competition
join, no team-id resolution to do. That makes the artifact transforms trivial and
moves the risk surface elsewhere:

1. **Selection & fetch correctness** — pulling the right matches (the six
   tournaments, only the 360-available ones) from 876 per-match files (plus six
   season files) over the network, cached and idempotent (§6.3–6.4, §7.3).
2. **Cross-source identity** — the open tournaments and the commercial NWSL delivery
   share the `statsbomb` slug and the *same private tier*, and StatsBomb player ids
   are **global**. Women's national-team players who also play for Racing Louisville
   appear in **both**. The player catalogue must not let the sparser open record
   clobber the richer commercial one (§5.2).
3. **Metadata schema consistency** — the open match object is *richer* than the
   commercial `build_metadata` output (real stadium/referee/manager ids, real
   `country`, real `home_team_group`). It must be reshaped to the **same canonical
   schema** so one slug serves one metadata shape (§4).

The concrete `(competition_id, season_id)` pairs are **public open-data identifiers**,
not licensed `(id → entity)` tuples, so — unlike the commercial family — they are
committed directly in the loader (§3 D-6).

---

## 2. Goal and non-goals

**Goal.** Serve the six complete-tournament open-data 360 competitions (292 matches)
at the owner tier, under the existing `statsbomb` slug, in the *same* faithful
StatsBomb feed shape and *same* artifact vocabulary the commercial family already
uses — so a consumer sees one unified owner-tier StatsBomb 360 corpus.

**Non-goals.**

- No public tier for any StatsBomb data (§1.1 — licence forbids it).
- No columnar/Parquet format (§3 D-2 — measured 80 MB corpus saving does not justify
  reversing ADR 0010).
- No new provider slug, no Terraform/Lambda change (a provider is an S3 prefix).
- No de-identification (owner-tier gating is the control, as for every restricted
  provider).
- No derived or computed artifacts — we stage the real feed.
- No `statsbombpy`/pandas dependency (§6.3).
- No migration of the existing 30 commercial matches — they already sit in the target
  format; this work is purely additive.

**Provider-name visibility.** As with the commercial family, `list_providers` is
tier-blind, so `statsbomb` already appears in `GET /v1/providers` for public callers
while every match/artifact/player stays owner-gated. No change.

---

## 3. Decisions

| # | Decision |
|---|---|
| D-1 | Open tournaments join the **existing `statsbomb` slug**, owner tier (`visibility="private"`), **`provenance="redistributed"`** (vs the commercial family's `"original"`). No new slug. |
| D-2 | **Faithful-feed, gzip JSON — ADR 0010 upheld, ADR 0011-style Parquet rejected.** The whole 292-match corpus is ~0.37 GB gzipped; Parquet/zstd would save ~80 MB on the 360 artifact while reversing a deliberate ADR, forcing a commercial-data migration, and imposing a bespoke parser on the consumer. |
| D-3 | A **second source-family adapter** (`formats/statsbomb_open.py`) sharing the commercial family's coherence/roster/staging/upload path. The open adapter is *simpler*: the source is the real feed, so no de-pivot, no competition join, no `resolve_team_ids`, no lineup-gender helper. |
| D-4 | **Honesty (ADR 0010 D-4) preserved.** Open lineups lack `birth_date`/`player_height`/`player_gender`; those become `null`/absent, never invented. Team ids and gender come from **inside** the match object, never an outside lookup (ADR 0010 D-5). |
| D-5 | **Player-catalogue: the open adapter skips ids already present in the private index** (skip-and-report), so a sparser open record never overwrites a richer commercial one — order-independent. The commercial adapter keeps replace semantics. `upload_players` is untouched. |
| D-6 | The six `(competition_id, season_id)` pairs are **public identifiers and are committed** in the loader (a named constant), unlike the commercial family's operator-local licensed note. |
| D-7 | **Owner tier is the only tier; the single-user basis is the licence rationale.** No public tier, ever, without StatsBomb's express written consent. |

### D-1 — same slug, new provenance, same private tier

ADR 0009 established the principle: data from the same provider goes under the same
slug; the *tier* dimension expresses restriction, and fragmenting one provider into
two nouns is rejected. Here both StatsBomb sources are already at the same
(private) tier, so tier does not distinguish them — **`provenance` does**
(`redistributed` vs `original`), and `source.licence` records which agreement governs
each match. A consumer that wants only one kind filters on `provenance`.

This differs from ADR 0009 in one way that *simplifies* it: ADR 0009 had to handle
public-vs-private **cross-tier** player collisions because SkillCorner had a public
tier. Here both StatsBomb sources are **private**, so there is no cross-tier case — the
collision that does arise is *same-tier* and is handled by D-5.

### D-2 — faithful gzip JSON, not Parquet (measured)

Measured on three WC-2022 matches, extrapolated to all 292:

| Artifact | per match (gzip) | 292 matches (gzip) | 292 matches (Parquet/zstd) |
|---|--:|--:|--:|
| events | 0.30 MB | 0.09 GB | *(kept JSON)* |
| freeze_frames | 0.97 MB | 0.28 GB | 0.20 GB |
| roster | ~0 | ~0 | — |
| **corpus total** | ~1.3 MB | **~0.37 GB** | — |

Parquet's entire benefit is **~80 MB across the corpus**. Against that: reversing
ADR 0010 (which exists precisely so consumers parse StatsBomb's *real* published feed —
and open data is the strongest case, since the feed is public and the consumer already
parses it); migrating the 30 existing commercial matches (or running two formats under
one slug — the opposite of "unified"); and forcing the consumer to write a
pining-proprietary SB360-Parquet parser. ADR 0011 adopted Parquet for SkillCorner
because continuous 25 Hz tracking was tens of GB; freeze-frames are per-event
snapshots and carry no such pressure. **Gzip JSON is the unified *and* efficient
answer here, and it requires zero migration of existing data.**

### D-3 — second source family, sharing one upload path

Mirrors the SkillCorner precedent (raw-JSON adapter + parquet-processed adapter
sharing one verify). The commercial reader (`formats/statsbomb.py`) stays untouched;
the open reader is new and reuses every value-neutral helper:

| Helper | Commercial | Open |
|---|---|---|
| de-pivot / `is_column_orient` | needed | **not needed** (real feed) |
| `join_competition` | needed (row lacks ids) | **not needed** (match nests `competition{}`/`season{}`) |
| `resolve_team_ids` | needed (row has names only) | **not needed** (`home_team_id`/`away_team_id` present) |
| `team_gender` (from lineups) | needed | **not needed** (`home_team_gender` on the match object) |
| `assert_delivery_coherent` | ✅ reused | ✅ reused |
| `players_from_lineups` | ✅ reused | ✅ reused |
| `match_info` | ✅ reused | ✅ reused |
| `upload_game` / `upload_players` (`src/mock_api/`) | ✅ reused | ✅ reused |
| `stage_bundle` | script-local to `upload_statsbomb_club.py:67`, bound to commercial source names | **extracted to a shared module + parameterized by per-family artifact specs — §6.5** |

The last row is the one non-trivial reuse. `stage_bundle` is **not** a shared helper
today: it lives in the commercial *script* and iterates `ARTIFACT_SPECS`
(`statsbomb.py:40`), whose source filenames resolve through `SOURCE_FILES` to
`events.json` / **`frames.json`** / `lineups.json` (`statsbomb.py:20-31`) — but the
open family's 360 source file is `three-sixty`, not `frames.json`. §6.5 states the
extraction that makes it genuinely shared; without it an implementer would either
import a function across two scripts or fork staging into two divergent paths under one
slug (the opposite of "unified").

### D-6 — public ids are committable

The commercial family keeps every id out of the repo because `(id → club)` tuples are
licensed. Open-data competition/season ids are **published by StatsBomb for public
use** and appear across the public analytics literature; committing the six pairs
leaks nothing. This is the one place the two families deliberately diverge on secrecy.

---

## 4. The `metadata` artifact

All four artifacts derive from the real feed. Three are staged byte-for-byte
(`events` → `events.json.gz`, `three-sixty` → `freeze_frames.json.gz`, `lineups` →
`roster.json`). Only `metadata` is reshaped — and even that is a **whitelist-and-nest
pass over an already-nested object**, not a reconstruction.

### 4.1 `build_metadata_open` — reshape, don't reconstruct

The open-data match object (from `matches/{cid}/{sid}.json`) already carries
`competition{}`, `season{}`, `home_team{home_team_id,…,home_team_gender,
home_team_group,country,managers[]}`, `away_team{}`, `competition_stage{id,name}`,
`stadium{id,name,country}`, `referee{id,name,country}`, `metadata{data_version,…}`,
`match_status`, `match_status_360`, `last_updated`, `last_updated_360`, `match_week`,
`home_score`, `away_score`, `match_date`, `kick_off`, `match_id`.

`build_metadata_open(match)` emits **exactly the canonical key set that the commercial
`build_metadata` produces** (`statsbomb.py:263-330`) — one slug, one metadata schema —
sourcing every slot from the nested match object. This is the **complete** field-source
map (mirroring §5.1's player table); the point of enumerating it in full is that
several slots the commercial `build_metadata` **hard-nulls** are **populated** in the
open feed, and a partial list would silently leave them `null`:

| Canonical slot | Open-match source | Commercial `build_metadata` |
|---|---|---|
| `match_id`, `match_date`, `kick_off` | `match[...]` | present |
| `competition.{competition_id,country_name,competition_name}` | `match["competition"][...]` (nested) | from joined competitions row |
| `season.{season_id,season_name}` | `match["season"][...]` (nested) | from joined competitions row |
| `home_team.home_team_id` / `away_team.away_team_id` | `match["home_team"]["home_team_id"]` / `away…` | resolved from events |
| `…_team_name` | `match["home_team"]["home_team_name"]` / `away…` | flat name |
| `…_team_gender` | `match["home_team"]["home_team_gender"]` / `away…` | from lineups |
| `…_team_group` | `match["home_team"]["home_team_group"]` / `away…` — **populated** (e.g. `"F"`) | **`null`** |
| `home_team.country` / `away_team.country` | `match["home_team"]["country"]` (**{id,name}**) / `away…` | **`null`** |
| `…managers` | `match["home_team"]["managers"]` (**full `{id,name,nickname,dob,country}`**) / `away…` | only `name`, rest `null` |
| `home_score`, `away_score` | `match[...]` | present |
| `match_status`, `match_status_360`, `last_updated`, `last_updated_360` | `match[...]` | present |
| `metadata.{data_version,shot_fidelity_version,xy_fidelity_version}` | **`match["metadata"][...]` (nested)** — note commercial reads these **flat** via `match.get(...)` (`:314-318`), so the open accessor differs | flat |
| `match_week` | `match["match_week"]` | present |
| `competition_stage.name` | `match["competition_stage"]["name"]` | present |
| `competition_stage.id` | `match["competition_stage"]["id"]` — **populated** | **`null`** (`:320`) |
| `stadium.name` | `match["stadium"]["name"]` | present |
| `stadium.id`, `stadium.country` | `match["stadium"]["id"]`, `["country"]` — **both populated** | **`null`** (`:321`) |
| `referee.name` | `match["referee"]["name"]` | present |
| `referee.id`, `referee.country` | `match["referee"]["id"]`, `["country"]` — **both populated** | **`null`** (`:322`) |
| `attendance`, `behind_closed_doors`, `neutral_ground`, `collection_status`, `play_status` | `match.get(...)` → **`null`** (absent from open) | present (commercial-only feed fields) |

Two accessor differences are load-bearing and must be coded, not assumed: the
`metadata{}` triplet is **nested** in the open feed (`match["metadata"]`) where the
commercial path reads it flat; and the `competition{}`/`season{}` objects come from the
match itself, not a joined competitions row. Integer ids/scores are already integers in
the open feed (no pandas float upcast), so `_int_or_none` is unnecessary — applying it
is harmless and kept for symmetry.

**No competition join.** ADR 0010's composition rule is satisfied trivially: the match
object *itself* co-locates `competition{}` and `season{}`, so there is nothing to
merge from a second file. The `competitions.json` global catalogue is **not fetched**.

**No team-id resolution.** `home_team.home_team_id` / `away_team.away_team_id` are
present and authoritative; `resolve_team_ids` (an events-scan the commercial family
needs because its row had names only) is not used.

### 4.2 Envelope and index entry

- **Envelope:** a single match object, not a one-element array — identical to the
  commercial family (commercial spec §4.4), governed by ADR 0008.
- **Index entry** (`MatchEntry`, written by `upload_game`): `id = str(match_id)`,
  `date = match_date` (**required** — a dateless entry is invisible to `dateFrom`/
  `dateTo`, so a missing `match_date` raises), `home`/`away` from the team names,
  `visibility="private"`, **`provenance="redistributed"`**, `source = {name:
  "StatsBomb", licence: "StatsBomb Public Data User Agreement (open data);
  redistribution to third parties not permitted"}`.

`match_info` (reused) already derives `date`/`home`/`away` from the built metadata and
raises on a missing date; it needs no change.

### 4.3 Match-id disjointness across sources

StatsBomb `match_id` is **globally unique**; open-tournament ids (e.g. `3857276`) and
the commercial NWSL ids occupy the same global space but never collide (distinct
fixtures). `_check_no_tier_mixing` (upload.py) only fires when the *same* id exists at
a *different* tier — impossible here, since both sources are private. If two sources
ever presented the same id (they will not), it would be an idempotent same-tier
refresh, not an error. Stated so the absence of a guard is a reasoned conclusion, not
an oversight.

---

## 5. Player catalogue

### 5.1 Source-field map (reused)

`players_from_lineups` is reused unchanged. Open-data lineups carry `player_id`,
`player_name`, `player_nickname` (often empty), `jersey_number`, `country{id,name}`,
and `positions[]` (with `from_period`) — but **not** `birth_date`, `player_height`, or
`player_gender`. So for open players:

| `PlayerRecord` | Open value |
|---|---|
| `id` | `str(player_id)` |
| `nickname` | `player_nickname` **or** `player_name` (full-name fallback; the D-4/ADR-0010 no-whitespace-split rule holds) |
| `dob` | **`null`** (absent from open lineups) |
| `height` | **`null`** (absent) |
| `nationality` | `country.name` |
| `position` | earliest `positions[]` spell (unchanged §5.2 rule) |

Gender is **not** taken from the lineup here (open lineups have no `player_gender`) —
it lives on the match object and feeds `build_metadata_open` (§4.1), not the player
record. As with the commercial family, `model_dump(exclude_none=True)` means `dob`/
`height` are **absent keys** on the wire, not explicit nulls.

### 5.2 Same-tier collision: the open adapter yields to the richer record

This is the design's second real risk surface (§1.2). Both StatsBomb sources write to
the **same** `statsbomb/_private/players.json`. StatsBomb player ids are global, and
Racing Louisville's roster includes national-team players who also appear in the
women's open tournaments — so **overlap is expected**, not hypothetical.

`upload_players` merges by **whole-record replace** (`by_id[new["id"]] = new`,
`upload_players.py:114`), so a naive open upload running *after* the commercial one
would overwrite a commercial record (with real `dob`/`height`) with the sparser open
record (both `null`). That is order-dependent data loss.

**Decision (D-5): the open upload script skips any player id already present in the
private index**, uploading only genuinely-new ids and reporting the skip count. This
makes the outcome **order-independent and lossless**:

- commercial first, then open → open **skips** the shared id → richer record survives.
- open first, then commercial → commercial **replaces** with the richer record → richer
  record survives.

The asymmetry is intentional: the *sparser* source (open) yields; the *richer* source
(commercial) always writes its full record. This extends ADR 0009's skip-and-report
pattern from the cross-tier case to the **same-tier richer-vs-sparser** case, and keeps
the shared `upload_players` writer untouched (a field-level merge there would change a
writer every provider depends on — out of scope, per commercial spec §5.1).

The skip is a **catalogue-level dedup only**: every open match's full lineup remains in
that match's `roster` artifact, so no tournament-context player data is lost — only the
provider-level reference entry is not duplicated.

**The skip set must see intra-run adds, not just the startup snapshot.** The loader
seeds an in-memory known-id set from the private index read **once** at start, then adds
each id as it is uploaded, and skips against that growing set. So a genuinely-new id
shared by two open tournaments (e.g. a player in both UEFA Euro 2020 and 2024) is
created on its first appearance and skipped on the second — even though both are new
relative to the startup snapshot. (Re-reading the live index per match would also work
but costs an S3 read per match; the in-memory set is the intended mechanism.)

**Scale.** The catalogue grows from the current 391 (Racing) to several thousand
(WC-2022 alone is ~800+ players); the overlap that D-5 protects is the small
women's-national-team ∩ Racing intersection. `players.json` stays a small file.

---

## 6. Implementation surface

**New**

| Path | Purpose |
|---|---|
| `src/formats/statsbomb_open.py` | Open source-family adapter: the six-tournament selection constant, per-match bundle assembly from fetched files, `build_metadata_open`. Pure transforms; the network fetch is injected (§6.1). |
| `scripts/upload_statsbomb_open.py` | Fetch+cache, coherence pre-flight, stage + upload at the owner tier, skip-and-report players (§5.2). `--dry-run`/`--execute`, `--cache-dir`, optional `--source-dir`, optional `--competition-season` override. |
| `src/tests/test_statsbomb_open_format.py` | Reader/reshape/selection unit tests (synthetic fixtures). |
| `src/tests/test_upload_statsbomb_open.py` | Assembly, pre-flight, skip-and-report, staging tests. |
| `src/mock_api/staging.py` (or equivalent shared location) | The `stage_bundle` staging concern extracted from the commercial script and parameterized by artifact specs (§6.5). |
| `docs/decisions/0012-statsbomb-open-data-second-source-family.md` | ADR for D-1/D-2/D-5/D-7. |

**Changed** — `scripts/upload_statsbomb_club.py` (imports the extracted `stage_bundle`
from its new shared location — behaviour-preserving, §6.5), `CLAUDE.md` (provider/source
description, scripts list), `README.md`, `ARCHITECTURE.md`, `CHANGELOG.md`,
`docs/api-reference.md`, `docs/decisions/README.md` (ADR index),
`verify_statsbomb_load.py` (also sample one `provenance="redistributed"` match),
`docs/c4/architecture.dsl` **and the regenerated `docs/c4/architecture.html`**, and
`pyproject.toml` (ruff per-file-ignore for `scripts/upload_statsbomb_open.py` — `S310`,
`urllib.request` against the pinned raw.githubusercontent host — plus the `0.6.0` →
`0.7.0` version bump; no `[project.scripts]` entry, matching precedent).

No runtime dependency is added.

### 6.1 Module boundaries

`formats/statsbomb_open.py` stays pure and independently testable: given already-fetched
per-match file contents (dicts/lists), it selects, assembles, reshapes, and derives —
no network, no S3. The upload script owns the network fetch, the local cache, gzip,
S3, the pre-flight assertions, and the skip-and-report read of the existing index. The
fetch function is injected into the pure layer (or the pure layer simply takes parsed
inputs) so tests never touch the network. This mirrors `formats/statsbomb.py` +
`scripts/upload_statsbomb_club.py`.

### 6.2 Id types

StatsBomb ids are integers; `MatchEntry.id`/`PlayerRecord.id` are regex-validated
strings and pydantic v2 does not coerce. Every id is `str(...)`-cast at the reader
boundary, as in the commercial family.

### 6.3 Source and caching

Data is fetched over **raw HTTP** from
`https://raw.githubusercontent.com/statsbomb/open-data/master/data/` (no
`statsbombpy`, no pandas). Per run, the loader needs, for each selected match: one
`events/{id}.json`, one `three-sixty/{id}.json`, one `lineups/{id}.json`, plus one
`matches/{cid}/{sid}.json` per tournament (six total). Fetched files are written to a
`--cache-dir` (operator-local, not committed) so a re-run, a `--dry-run`, and an
`--execute` reuse the same bytes; an existing local clone of `statsbomb/open-data` may
be pointed at with `--source-dir` to skip the network entirely. Fetch failures raise
(with the URL); a partially-fetched match is never staged (§7.1 pre-flight runs first).

### 6.4 Selection

For each `(competition_id, season_id)` in the committed six-tournament constant: fetch
the season `matches` file, keep only matches with **`match_status_360 == "available"`**
(the authoritative per-match 360 signal — the competition-row flag is not used, since
it only means "≥1 match has 360"; for these six that filter passes all matches, but it
is the durable guard against a future non-360 fixture). `--competition-season CID:SID`
overrides the constant for ad-hoc runs.

**Transposed-pair guard.** `season_id` is **per-competition** in StatsBomb's scheme, so
the same value recurs across competitions — e.g. `season_id=106` is *both* the FIFA
World Cup 2022 (under `competition_id=43`) *and* the UEFA Women's Euro 2022 (under
`competition_id=53`). A transposed `(competition_id, season_id)` pair would therefore
fetch a **real but wrong** tournament with no downstream signal. After fetching a
season file the loader **asserts every match's `competition.competition_id` equals the
requested `competition_id`** (the open match object carries it inline, §4.1), so a
transposed pair fails loud before any staging. The six committed pairs were **verified
against the live `matches` endpoint** when the constant was written (returned match
counts 64 / 64 / 51 / 51 / 31 / 31, matching the tournament sizes).

**Defect-skip (real-data hardening).** A real-data dry-run over all 292 matches found
one — match `3845506` (Women's Euro 2022) — whose upstream `three-sixty` file is
**NUL-byte corrupted in StatsBomb's published open data** (a run of `\x00` injected
mid-JSON; confirmed a real upstream defect — byte-identical on re-fetch, failing at the
same offset — not a truncated download). This class of defect recurs with open data, so
the loader **skips a defective match and reports it rather than aborting the run**. In
`run()`, each match's fetch + parse + coherence + reshape + player validation is wrapped
so that a `JSONDecodeError` (unparseable artifact), a fetch `RuntimeError` (missing/404
artifact), or a `ValueError` / `ValidationError` (incoherent delivery, un-dateable
match, invalid player record) **skips that match with a recorded reason and continues** —
never a silent drop (the reasons are printed and `run()` returns the skipped list).
Validation is **structurally separated** from upload: only the S3-free `validate_open_match`
(coherence + reshape + `match_info` + player validation) is inside the defect-catch, while
`_stage_and_upload_open_match` runs **outside** it. So a skip can never leave a partial
load, and an *upload*-phase failure **propagates** rather than being mis-reported as a
defect — a guarantee enforced by the split (and covered by a test), not merely by which
checks happen to run first. This matches the repo's established convention (SkillCorner
`partition_ingestible`, the commercial family's skip-frameless). Net ingest is therefore
**291 of 292** until StatsBomb republishes the corrupted file (a re-run then picks it up
idempotently). The one skipped id is public open-data, so it is named in the CHANGELOG.

### 6.5 Shared staging

`stage_bundle` is extracted from `scripts/upload_statsbomb_club.py` into a shared module
(e.g. `src/mock_api/staging.py`) and **parameterized by the artifact-spec mapping** —
its signature takes the `(role, source_filename, staged_filename)` tuples rather than
closing over the commercial `ARTIFACT_SPECS`. The commercial script then imports it and
passes `ARTIFACT_SPECS` (behaviour identical to today — a pure refactor, covered by the
existing `test_upload_statsbomb.py` staging tests). The open family defines its **own**
specs in `formats/statsbomb_open.py` (e.g. `("freeze_frames", "three-sixty.json",
"freeze_frames.json.gz")`) whose 360 source name matches the file the open assembly
writes — so no file is ever mis-named `frames.json` and no staging logic is duplicated.

This is a change to the commercial **script**, not the commercial **reader**
(`formats/statsbomb.py`) — D-3's "commercial reader stays untouched" holds. The
compression rule is unchanged: gzip the multi-megabyte bodies (`events`,
`freeze_frames`), stage the kilobyte ones (`roster`, `metadata`) plain.

---

## 7. Testing

Fixtures are **wholly synthetic** — invented competition/season/match/player ids and
invented team/player names, no real StatsBomb bytes in the repo (per
`feedback_synthetic_fixtures_must_be_wholly_invented` and the restricted-fixture rule).
Because the six competition/season ids are public (D-6), the *selection constant* may
use the real pairs, but **test fixtures do not** — they exercise the code paths with
invented ids so a fixture is never mistaken for licensed content.

| Test | Asserts |
|---|---|
| reshape — structure | `build_metadata_open` output key set is **identical** to the commercial `build_metadata` output (one schema per slug), asserted against the canonical key set |
| **reshape — richer values preserved (full set)** | **every** slot `build_metadata` hard-nulls is populated from the open match, asserted field-by-field against the §4.1 map, not a sample: `competition_stage.id`, `stadium.{id,country}`, `referee.{id,country}`, `home_team_group`/`away_team_group`, `home_team.country`/`away_team.country`, `managers[].{id,dob,country}` |
| reshape — metadata nesting | the `metadata{data_version,shot_fidelity_version,xy_fidelity_version}` triplet is read from **nested** `match["metadata"]`, not flat `match.get(...)` |
| reshape — credentialed fields null | `attendance`/`behind_closed_doors`/`neutral_ground`/`collection_status`/`play_status` are `null` (absent from the open match) |
| reshape — no external fetch | reshape uses only the match object; no `competitions.json` is read |
| team ids from match | `home_team_id`/`away_team_id` taken from the match object, not resolved from events; an events-only fixture still yields ids |
| selection — 360 filter | a season fixture mixing `match_status_360` `available`/`unscheduled` yields only the available matches |
| selection — ids | the committed constant holds exactly the six documented pairs |
| players — sparse fields | open player yields `dob`/`height` `null`; full-name fallback into `nickname`; **no whitespace split** |
| **players — same-tier skip** | given an existing private index containing id X, an open batch containing X and Y uploads **only Y**; X's prior (richer) record is untouched; skip count reported |
| players — order independence | commercial-then-open and open-then-commercial both leave the **richer** record for a shared id (asserted by value) |
| players — intra-run dedup | two open matches introducing the same **new** id (absent from the startup snapshot) create it once; the second occurrence is skipped via the in-run known-id set (§5.2) |
| selection — transposed-pair guard | a `(competition_id, season_id)` whose fetched matches carry a different `competition.competition_id` raises before staging (§6.4) |
| pre-flight (reused suite) | orphan `event_uuid`, zero frames, zero/empty lineups, lineups/events team disagreement, <2 periods, missing `Half End`, ≠2 teams, non-array files — each raises before staging |
| artifact keys | staged filenames yield exactly `events`/`freeze_frames`/`roster`/`metadata`, each matching the path-param regex |
| index entry | `provenance="redistributed"`, `visibility="private"`, `source.licence` populated; missing `match_date` raises |
| id types | every emitted id is `str` |
| fetch/cache (upload script) | a cached file is not re-fetched; a fetch failure raises naming the URL; `--source-dir` bypasses the network |
| **defect-skip** | in a 2-match season where the second match's `three-sixty` fetch raises `JSONDecodeError`, the good match still uploads (`upload_game` called once) and the defective one is skipped; `run()` returns the `(id, reason)` skipped list (§6.4) |

The two bolded rows are the new-risk-surface tests (§1.2). The skip test is written
**red-first** against a fixture whose shared id would otherwise be clobbered.

### 7.1 Delivery coherence pre-flight (reused)

`assert_delivery_coherent(events, frames, lineups)` runs unchanged, per assembled
match, **before anything is staged**: non-empty frames/lineups, no empty per-team
squads, every frame `event_uuid` resolving to an event id, ≥2 periods, a `Half End`,
exactly two teams, and lineups/events team agreement. There is deliberately **no
`--force`** — an abnormal fixture is handled by amending the specific assertion. The
synthetic tests prove the helper; the dry-run over the 292 real matches proves the
*fetch* delivered coherent bundles — and a defective one (an unparseable/incoherent
upstream artifact) is skipped-and-reported (§6.4), not fatal.

### 7.2 Freeze-frame privacy (unchanged, precise)

Open 360 frames carry position + `teammate`/`actor`/`keeper` flags only — **no player
identity in the frame payload**. Identity is reachable via the `event_uuid` join to
`events`, so frames are **pseudonymous-linkable, not anonymous**. Owner-tier gating is
the control; no claim here depends on the stronger "anonymous" reading.

### 7.3 Dry-run over the full corpus before any upload

Before `--execute`, a `--dry-run` fetches (or reads from cache) and validates all 292
matches through assembly + `assert_delivery_coherent` + `build_metadata_open` +
`PlayerRecord.model_validate`, with **zero S3 calls**, reporting the ingestible count,
player count, player-dedup skips, and any **defective** matches (§6.4) — parsing ~2 GB
of JSON, so it runs as a background job. The run that motivated §6.4's defect-skip
confirmed **291 ingestible, 1 defective** (match `3845506`, NUL-corrupted `three-sixty`).

---

## 8. Cross-repo note (luxury-lakehouse)

luxury-lakehouse keys its per-match HuggingFace `access_tier` on pining's
`visibility`. These 292 matches are `visibility="private"`, so lakehouse must map them
to its **private** tier and **must not** publish them (or any derivative that embeds
the raw data) to a public HF dataset — that would be exactly the third-party
redistribution `LICENSE.pdf` 1.2.1 forbids, and pining's owner-tier compliance (§1.1)
depends on the data staying single-user end-to-end. The new `provenance="redistributed"`
value is additive; lakehouse consumers keying on `visibility` need no change, but a
consumer that keys on `provenance` should treat `redistributed`+`private` as
"restricted, do not republish."

pining's `visibility` vocabulary (`public`/`private`) is unchanged — the load-bearing
cross-repo contract is untouched. No change is made to that repo from this work; this
note is a heads-up to coordinate before any downstream publication decision.

**Operator compliance actions (outside the code):** register at
`statsbomb.com/resource-centre` (the agreement's 2.2 ask), and ensure any *published*
analysis derived from this data attributes StatsBomb with its logo (1.4). These are
one-time operator actions, not implemented here.

---

## 9. ADR impact

**New — ADR 0012, StatsBomb open-data as a second source family.** Records D-1 (same
slug, `provenance="redistributed"`, new `redistributed`+`private` combination), D-2
(faithful gzip JSON; ADR 0011-style Parquet considered and rejected with the measured
80 MB figure), D-5 (same-tier skip-and-report to protect the richer record), and D-7
(single-user owner-tier licence basis; no public tier without written consent). Worth
its own ADR because it establishes that **`provenance`, not tier, distinguishes two
same-tier sources under one slug**, and because the `redistributed`+`private`
combination is a first.

**Not amended — ADR 0010.** Faithful-feed mimicry is *upheld*, not changed: the open
data is the real feed, so this design is the purest application of ADR 0010, and D-2
explicitly declines the Parquet path that would have strained it. ADR 0008's
vocabulary is used as written (`events`/`freeze_frames`/`roster`/`metadata`, no
`tracking`) with no new key, so no amendment is needed there either.

`docs/decisions/README.md` gains ADR 0012 in the index table only.

---

## 10. Out of scope

- The single-team open 360 seasons (Barcelona La Liga 2020/21, PSG Ligue 1 2021/22 &
  2022/23, Leverkusen Bundesliga 2023/24, Inter Miami MLS 2023) and the AFCON-2023
  one-match 360 stray. The adapter *could* ingest them (they are the same shape), but
  they are single-team slices, not complete tournaments — a separate scope decision.
- Any public tier for StatsBomb data.
- Any Parquet/columnar format (D-2).
- The events-only open competitions (no 360).
- Changes to the commercial family's reader or to the shared `upload_players` writer.
- Changes to luxury-lakehouse.

---

## 11. Open questions

- **OQ-A.** Whether to fetch per-file over HTTP or shallow-clone
  `statsbomb/open-data`. The design supports both (`--source-dir`); the default is
  per-file fetch to avoid pulling the multi-GB whole repo. If the operator already
  keeps a clone (silly-kicks' `statsbombpy` cache is *not* a git clone), `--source-dir`
  is faster.
- **OQ-B.** Whether `verify_statsbomb_load.py` should assert the *mix* (at least one
  `original` and one `redistributed` match visible to the owner) or just sample one of
  each. Leaning: sample one `redistributed` match in addition to the existing sample,
  keeping the verify a smoke test rather than a census.
- **OQ-C.** If StatsBomb later promotes any of these competitions' *other* seasons to
  full-360, or adds new full tournaments, the selection constant is the single edit
  point — but that is a new scope decision, not an automatic pickup.
