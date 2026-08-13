# StatsBomb Commercial 360 — Owner-Tier Provider

**Status:** Draft (revision 3)
**Date:** 2026-08-12
**Related:** ADR 0002 (private-prefix tier separation), ADR 0005 (single-bucket
multi-tier), ADR 0008 (role-aligned artifact keys), ADR 0009 (restricted tier under
an existing public provider)

---

## 1. Context

We have obtained commercial StatsBomb data — events plus 360 freeze frames — for a
single match, under a club subscription. The data is **not redistributable**: it is
served only to the owner bearer token, exactly as the restricted SkillCorner and
Gradient Sports content already is.

The delivery arrives as an operator-local archive, not an API pull. This is
deliberate. Acquiring the data through a credentialed StatsBomb account would give
us access to far more of the catalogue than we need or are expected to touch; taking
a single-match file drop bounds the acquisition surface to exactly what was granted.

**One match now, more later.** The reader discovers matches from the bundle rather
than hardcoding an id, so the second match is a re-run rather than a rewrite.

Concrete identifiers (match id, competition id, season id, club names) are
**licensed `(id → entity)` tuples and are deliberately absent from this repo**. They
live in an operator-local delivery note alongside `$STATSBOMB_RESTRICTED_DIR`. Where
this spec needs to make a checkable claim about them (§8), it names the note rather
than the value.

That note also records the **`statsbombpy` version that produced the delivery**, on
which §4.2.1's competition join depends, and the archive's receipt date. Both are
provenance a future maintainer cannot recover from the files themselves.

### 1.1 Why this needs a design rather than a passthrough

The archive is **shape-mixed**. Three files (`events`, `frames`, `lineups`) are raw
feed arrays. Two (`competitions`, `matches`) are pandas *column-orient* dumps — the
result of the export passing through `statsbombpy`'s default `fmt="dataframe"` mode,
which flattens nested objects:

```
{"match_id": {"7": 9999999}, "match_date": {"7": "2026-01-01"}}   <- column-orient
[{"match_id": 9999999, "match_date": "2026-01-01", ...}]         <- feed shape

(illustrative ids only; real identifiers are licensed and stay out of this repo)
```

Serving that verbatim would publish an artifact of the *export tooling* rather than
of the provider. Since this repo is a **mock provider API**, and since the downstream
consumer will build its parser against what we serve, the flattened shape would
become a contract we do not want and would have to break later.

**The two flattened files are the entire risk surface of this design.** The three raw
files are staged byte-for-byte and cannot go wrong. Every silent-wrong-value failure
mode in this spec lives in the `metadata` reconstruction (§4.2), which is why that
section carries explicit join keys, fail-loud rules, and value-level tests rather
than structural ones.

---

## 2. Goal and non-goals

**Goal.** Serve one commercial StatsBomb match at the owner tier, in a shape that is
as faithful to the real StatsBomb feed as the source archive allows.

**Non-goals.**

- No de-identification. Owner-tier gating is the control (consistent with ADR 0009).
- No redistribution, no HuggingFace publication, no public-tier entry.
- No derived or computed artifacts. We stage what we received.
- No new API surface. A provider is an S3 prefix; no Terraform or Lambda change.

**Provider-name visibility.** `list_providers` is deliberately tier-blind
(`list_providers.py:16-18`), so the slug `statsbomb` will appear in `GET /v1/providers`
for public-token callers, while every match, artifact and player under it stays
owner-gated. This is existing precedent, not a new disclosure — `gradientsports` has
been owner-only from birth and publicly listed since it shipped. Stated here because
"owner tier only" otherwise reads as "invisible."

---

## 3. Decisions

| # | Decision |
|---|---|
| D-1 | New provider slug **`statsbomb`**, owner tier only (`visibility="private"`, `provenance="original"`). |
| D-2 | **Faithful-feed mimicry** — provider-native substructures are served in the provider's own published shape, never in the shape of whatever tooling produced our copy. |
| D-3 | Artifact keys follow the ADR 0008 role vocabulary exactly: `events`, `freeze_frames`, `roster`, `metadata`. No `tracking`. |
| D-4 | Structure may be **reconstructed**; values may never be **invented**. Fields the export dropped are emitted as `null`. |
| D-5 | Inference **from inside the delivery** is permitted; inference **from outside it** is not. |
| D-6 | The queryable player index carries canonical fields only. Proprietary ratings stay inside the `roster` artifact. |

### D-1 — new slug, not a tier under an existing provider

ADR 0009 reused the `skillcorner` slug because a public SkillCorner provider already
existed and the restricted data came from the same provider. Here there is no
existing `statsbomb` provider in this repo, so the question does not arise: the slug
is new and owner-only from birth.

`statsbomb` also matches the downstream consumer's existing `data_source='statsbomb'`
naming, so no translation layer is needed on either side.

### D-2 — faithful-feed mimicry, and what it governs

> **A provider-native substructure served by this API represents the provider's
> format, not the format of the pipeline that happened to deliver our copy.**

The consumer builds against what we serve. If we serve a flattened export, the
consumer writes a flattened parser, and that parser breaks the day a full-fidelity
copy arrives.

**Scope boundary (added in revision 2).** D-2 governs the *content* of
provider-native structures. It does **not** govern the artifact envelope — how many
entities an artifact holds and how it is scoped. That is ADR 0008's role vocabulary,
which is match-scoped by construction.

The boundary matters because the `metadata` artifact is scoped differently from any
single StatsBomb file, and without the carve-out a future provider could cite
whichever half of D-2 suited it. See §4.4 for the envelope decision and its
reasoning.

**What D-2 is not.** It is not a licence to compose. `metadata` merges two delivered
files, and that is legitimate **only because the real feed's match object natively
contains both** — see §4.2. Merging two provider files whose contents the feed does
*not* co-locate would be synthesis, and D-2 forbids it.

### D-5 — the inference boundary

§4.2 recovers team ids from `events` and gender from `lineups`, while §4.3 forbids
recovering a stadium id from the public catalogue. Revision 1 stated both rules but
not the principle separating them, and its stated reason ("the inference could
silently be wrong") condemned both equally.

The real distinction:

- **Inside the delivery** — the archive is one coherent export of one match. A team
  id in `events` and a team name in `matches` describe the same fixture, and a
  mismatch between them is detectable (§4.2's fail-loud rules) rather than silent.
- **Outside the delivery** — the public catalogue describes *different* seasons of
  the same competition. A stadium or referee id looked up there could be correct,
  stale, or belong to a different entity entirely, and **nothing in the delivery can
  contradict it**. The error is undetectable by construction.

**The delivery is a single coherent export of one fixture, so a disagreement inside
it is a contradiction and can be made to raise. An outside source carries no such
guarantee — it may simply describe something else, and agreeing or disagreeing with
it proves nothing either way.** This is the summary that belongs in ADR 0010
alongside the honesty rule.

Detectability follows from that guarantee rather than defining it: §4.2.2's arity
checks can be written precisely *because* two references to the same fixture must
agree. An outside lookup could be given a cross-check too, but it would be checking
consistency with a different entity, which is not the same assurance.

### D-3 — role-aligned keys

| Key | Source file | Staged filename | Treatment |
|---|---|---|---|
| `events` | `events.json` | `events.json.gz` | passthrough |
| `freeze_frames` | `frames.json` | `freeze_frames.json.gz` | passthrough |
| `roster` | `lineups.json` | `roster.json` | passthrough |
| `metadata` | `matches.json` + `competitions.json` | `metadata.json` | de-pivot + re-nest (§4) |

`upload_game` derives the artifact key from the staged filename stem
(`name.split(".", 1)[0]`), so the staged names above *are* the API allowlist.

**Compression rule:** gzip bodies over ~1 MB, stage smaller ones plain. `events` and
`freeze_frames` are multi-megabyte; `roster` and `metadata` are kilobytes. This
matches the SkillCorner precedent, which gzips `tracking` and leaves `metadata`
plain; stated here because it was previously left implicit.

There is deliberately **no `tracking` key**. StatsBomb 360 supplies freeze frames at
event moments, not continuous tracking. ADR 0008 explicitly permits a provider to
expose a subset of the vocabulary.

This is the first *provider* to use the shared vocabulary with no per-provider
exceptions anywhere in its key set. The restricted SkillCorner tier already uses
role-aligned keys (ADR 0009 §1); what differs is that `skillcorner` as a slug still
carries legacy id-prefixed keys on its public tier, so consumers need a per-tier map
for it and need none here.

---

## 4. The `metadata` artifact

Three of the four artifacts are already in feed shape and are staged byte-for-byte.
Only `metadata` requires work.

### 4.1 Step one — de-pivot (lossless, mechanical)

A column-orient dump is transposed back to records. Every value is preserved
exactly; only the container changes. This is not estimation.

**Detection is shape-based, not filename-based**, so a future fully-raw delivery
flows through the same code path with no flag and no edit. A payload is treated as
column-orient only if **all** of the following hold:

1. The top level is an object, not an array.
2. It has **≥ 2** keys.
3. **Every** value is an object, and all of them share one identical key set.
4. Every key of that shared set is a **stringified integer** (the DataFrame index).

Anything else passes through untouched. Conditions 2–4 exist because condition 3
alone is too weak to be a rule: an object whose values happen to be uniformly-shaped
sub-objects would be transposed into garbage. Requiring an integer-like index is what
makes the discriminator a rule rather than an accident of this particular file.

**Post-condition, asserted not assumed:** every de-pivoted record must contain the
key expected for its file (`match_id` for matches, `competition_id` for competitions).
A de-pivot that produces records without it raises rather than proceeding.

**Non-finite floats.** `pandas.to_json()` emits `null` for `NaN`/`Inf`, and the
delivered files contain **zero** bare `NaN`/`Infinity` tokens (verified). Python's
`json` module would nonetheless *accept* such tokens and re-emit them, producing a
document that `JSON.parse` rejects. The loader therefore passes `parse_constant=` to
raise on them. This is a cheap guard against a non-pandas producer, not a fix for an
observed defect — and normalising a non-finite float to `null` would be a **format
correction, not a value invention**, so it sits inside D-4 either way.

### 4.2 Step two — re-nest to feed shape

The published StatsBomb open-data catalogue is the authority for the target
structure, and was read directly to confirm it. Two findings:

1. **`competitions.json` needs no reconstruction.** Its twelve keys are *identical*
   to a public competition entry. The de-pivot alone lands on the canonical shape.
2. **`matches.json` does.** The real match object nests `competition`, `season`,
   `home_team`, `away_team`, `metadata`, `competition_stage`, `stadium`, and
   `referee`. Our export flattened all eight.

Critically, **the real match object already contains `competition{}` and `season{}`
inline.** Pulling those fields from the competitions file is therefore *restoring*
what the feed co-locates, not inventing a composite — which is what licenses the
merge under D-2.

#### 4.2.1 Competition join — explicit key, fail loud

The matches row carries **no `competition_id` or `season_id`**; that is precisely why
the competitions file is needed. A join on those columns is therefore not available.
`statsbombpy` renders the competition as `f"{country_name} - {competition_name}"`,
so the join is on that reconstructed string plus the season name:

```
competitions row R matches the match row M  iff
    f"{R.country_name} - {R.competition_name}" == M.competition
and str(R.season_name) == str(M.season)
```

**This key is a tooling convention, not a feed contract.** The `"{country} - {competition}"`
rendering is `statsbombpy`'s private presentation of two columns, not something
StatsBomb publishes; a library change could stop it matching. The
`statsbombpy` version that produced a delivery is therefore recorded in the
operator-local delivery note (§1) alongside the ids, so a future mismatch is
diagnosable rather than mysterious.

**If the join stops matching on a future delivery, re-derive the key — never loosen
the comparison.** Substring, case-insensitive or fuzzy matching would convert a
fail-loud stop into exactly the silent wrong-competition attachment §4.2.1 exists to
prevent. The failure is safe by construction: a wrong assumption blocks the upload
rather than corrupting the artifact.

**Exactly one row must match. Zero or many raises.** The delivered competitions dump
holds a single row, but a credentialed export can legitimately carry the club's whole
entitlement list, and taking row 0 from such a file would attach a confidently wrong
competition to the match while every structural test still passed.

#### 4.2.2 Home/away resolution — explicit key, fail loud, non-inversion asserted

`events` carries `team.id` + `team.name`; the flattened matches row carries
`home_team` / `away_team` as names only. Resolution is therefore by **exact** name
equality against the distinct teams appearing in `events`, and it must satisfy all
of:

- each side resolves to **exactly one** team id — zero or multiple raises;
- the two sides resolve to **different** ids — equal ids raise.

An inverted fixture is strictly worse than a null: it is indistinguishable from
correct downstream and silently corrupts every home/away-dependent computation. No
fuzzy or normalised matching is used, because a near-match is exactly the case that
should stop the upload rather than guess.

Lineup ordering is **not** used as a fallback — the delivered `lineups.json` lists
the away team first, so ordering carries no home/away signal.

#### 4.2.3 Field-by-field sourcing

| Target | Value | Source |
|---|---|---|
| `competition{competition_id, country_name, competition_name}` | present | joined competitions row (§4.2.1) |
| `season{season_id, season_name}` | present | joined competitions row |
| `home_team`/`away_team` `.{*_team_id}` | present | resolved from `events` (§4.2.2) |
| `home_team`/`away_team` `.{*_team_gender}` | present | `lineups` player gender |
| `home_team`/`away_team` `.{*_team_group, country}` | `null` | not in export |
| `…managers[].name` | present | de-pivoted `matches` |
| `…managers[].{id, nickname, dob, country}` | `null` | not in export |
| `metadata{data_version, shot_fidelity_version, xy_fidelity_version}` | present | re-nested from top level |
| `competition_stage{name}` / `stadium{name}` / `referee{name}` | present | de-pivoted `matches` |
| `competition_stage{id}` / `stadium{id, country}` / `referee{id, country}` | `null` | not in export |

**Three numeric fields are cast float → int** by `_int_or_none`, not one:
`home_score`/`away_score` (`2.0` → `2`), `match_week`, and `match_id`. All three are
integers in the feed; the export delivers them as floats because a single null
anywhere in a pandas column upcasts the whole column to `float64`.

The `match_id` cast is load-bearing, not cosmetic. `match_info` stringifies it
straight into `MatchEntry.id`, so an uncast value would yield `"9999999.0"` — which
fails `MatchEntry.id`'s path-param regex (`^[a-zA-Z0-9][a-zA-Z0-9_-]*$`) at upload
time. Removing the cast does not corrupt the load; it stops it.

**Commercial-only fields are preserved at top level.** The export carries
`attendance`, `behind_closed_doors`, `neutral_ground`, `collection_status` and
`play_status`, which the open catalogue does not. These are genuine
credentialed-endpoint fields, not export artifacts, and are kept.

### 4.3 The honesty rule

`null` is a truthful statement that our copy lacks the value. A plausible-looking
invented id is not, and would be indistinguishable from a real one downstream. No
identifier is ever synthesised from outside the delivery — see D-5 for why that line
sits where it does.

### 4.4 Envelope — a single object, not an array

The artifact is a **single match object**, not a one-element array.

The feed's per-season file is an array because it is scoped to a season. This
artifact is scoped to one match by its URL (`/matches/{id}/metadata`), so an array
wrapper would carry no information, and the `metadata` role already denotes a single
per-match document for every other provider — IDSSE's `metadata` is one
matchinformation document, SkillCorner's is one meta JSON. Serving an array here
would make `statsbomb` the odd one out in exactly the vocabulary ADR 0008 exists to
keep uniform.

This is an **envelope** decision, governed by ADR 0008 rather than D-2 — see the
scope boundary in D-2. Recorded explicitly because the artifact's scoping genuinely
differs from any single delivered file, and that difference should be a decision on
the record rather than an accident.

### 4.5 Index entry

`upload_game` also writes the provider index entry, whose fields are **not** the
artifact's:

| `MatchEntry` field | Source |
|---|---|
| `id` | `str(match_id)` — see §6.2 |
| `date` | `match_date` from the de-pivoted matches row |
| `home` / `away` | `home_team` / `away_team` names |
| `visibility` | `"private"`, constant |
| `provenance` | `"original"`, constant |
| `source` | name `StatsBomb`; licence `Restricted; redistribution not permitted` |

**`date` is required, and its absence is silent.** `apply_filters` excludes an empty
date from both range filters (`shared.py:296,299` — `(r.get("date") or "") >= threshold`
and `"" < (r.get("date") or "") < threshold`), so a dateless entry is invisible to
every `dateFrom`/`dateTo` query while still returning 200 on the unfiltered list. The
upload therefore raises on a missing or empty `match_date` rather than uploading a
match that cannot be found by date.

---

## 5. Player catalogue

### 5.1 Source-field map

StatsBomb's lineups feed carries **no first/last name split** — it has a single
`player_name` plus an often-empty `player_nickname`. In the delivered archive,
**23 of 40 players have an empty `player_nickname`**.

`PlayerRecord` requires `nickname OR (firstName AND lastName)`
(`src/canonical/models.py:105-109`). With no split available, satisfying that
validator via `firstName`/`lastName` would require splitting `player_name` on
whitespace — which invents a boundary the feed does not assert and mangles compound
surnames. **D-4 forbids it.** The mapping therefore routes the full name into
`nickname`:

| `PlayerRecord` | Source | Rule |
|---|---|---|
| `id` | `player_id` | `str(...)` — §6.2 |
| `nickname` | `player_nickname` **or** `player_name` | fall back to the full name when the nickname is empty; always populated, so the validator is always satisfied |
| `firstName` / `lastName` | — | **never populated** — the feed asserts no split (see the wire note below) |
| `dob` | `birth_date` | verbatim |
| `height` | `player_height` | verbatim |
| `nationality` | `country.name` | verbatim |
| `position` | `positions[]` | see §5.2 |
| `positionGroupType` | — | not populated; StatsBomb supplies no position group |

**What reaches the wire.** `upload_players` serialises with
`model_dump(exclude_none=True)` (`upload_players.py:82`), so a field left unset is
**absent from the served `players.json`, not `null`**. `firstName`, `lastName` and
`positionGroupType` are therefore missing keys on the wire — a consumer must treat
absence, not a null value, as "this provider asserts no split."

This is the one place §4.3's "`null` is a truthful statement" does not reach the
served bytes. The `metadata` artifact is unaffected: it is staged as the reader's own
JSON and never passes through a pydantic dump, so §4.2.3's nulls survive verbatim.
Making the player index emit explicit nulls would require changing a shared writer
that every provider uses, which is **out of scope here**; the behaviour is identical
for SkillCorner today and is documented rather than changed.

Consequently the player-mapping tests in §7 assert against the **reader's output**,
not against uploaded records — written against the latter they would fail on an
absent key rather than a wrong value.

That `nickname` may hold a full name is a deliberate, recorded consequence of the
feed's shape, not a bug. It is a notable commercial-vs-canonical difference: the
SkillCorner bundle *does* carry a genuine first/last split, so a consumer must not
assume `firstName` is populated across providers.

### 5.2 Which position

`positions[]` is a per-spell array (up to 4 entries per player; 32 of 40 players have
any), each with `position_id`, `position`, `from`/`to`, `from_period`/`to_period`,
`start_reason` and `end_reason`. The catalogue takes the **earliest spell** by
`(from_period, from)` — the player's starting position — and `null` when the array is
empty. The full per-spell history remains in the `roster` artifact.

### 5.3 What is excluded

Weight, the proprietary skill-rating block and the (empty) stats array are excluded
from the queryable index but remain intact inside the `roster` artifact, so nothing
is lost and the restricted material is not spread across two surfaces.

Uploaded at `visibility="private"`.

**No cross-tier collision handling is needed.** ADR 0009's skip-and-report logic
exists because restricted SkillCorner players could already be public under the same
provider. Here the provider is owner-only from birth, so `upload_players`'
raise-on-collision guard (`upload_players.py:96-103`) stays as an untouched backstop.
Should a public `statsbomb` tier ever be added, that logic must be revisited —
recorded here so the omission is deliberate rather than forgotten.

---

## 6. Implementation surface

**New**

| Path | Purpose |
|---|---|
| `src/formats/statsbomb.py` | Pure reader: shape detection, de-pivot, join, id resolution, re-nest, player mapping. No I/O beyond reading the bundle. |
| `scripts/upload_statsbomb_club.py` | Coherence pre-flight (§7.1), stage + upload at the owner tier. |
| `scripts/verify_statsbomb_load.py` | Post-upload verification against the live API. |
| `src/tests/test_statsbomb_format.py` | Reader unit tests. |
| `src/tests/test_upload_statsbomb.py` | Upload/staging/pre-flight tests. |
| `docs/decisions/0010-faithful-feed-mimicry.md` | ADR for D-2, D-4, D-5. |
| `scripts/_verify_http.py` | HTTP helpers shared by all four verify scripts, which each carried a copy. Private *module*; public members. |
| `src/tests/test_verify_http.py` | Unit tests for the shared helpers. |
| `src/tests/test_verify_statsbomb_load.py` | Unit tests for the verify script's offline-testable metadata feed-shape check. |

**Changed** — `CLAUDE.md` (provider list, CLI entry points), `README.md`,
`ARCHITECTURE.md`, `CHANGELOG.md`, `docs/api-reference.md`,
`docs/decisions/README.md` (the ADR index table, which 0010 must join, **and the
immutability rule — see §9**), `docs/decisions/0008-role-aligned-artifact-key-vocabulary.md`
(§9's additive amendment), `docs/c4/architecture.dsl` **and the
`docs/c4/architecture.html` regenerated from it**, plus the three pre-existing verify
scripts (`verify_gradient_load.py`, `verify_idsse_load.py`,
`verify_skillcorner_realmadrid_load.py`) refactored onto `scripts/_verify_http.py`.

`pyproject.toml` needs **three** changes: ruff per-file-ignores for **two** scripts
(`scripts/verify_statsbomb_load.py` and `scripts/_verify_http.py` — `S310`,
`urllib.request` against a configurable HTTPS endpoint), matching the entries the
existing verify scripts already carry, plus the `0.3.0` → `0.4.0` version bump. No
`[project.scripts]` entry is added: the upload and verify scripts are run directly,
matching the SkillCorner precedent.

The source root is read from `$STATSBOMB_RESTRICTED_DIR`; no operator-local path is
committed.

### 6.1 Module boundaries

`formats/statsbomb.py` is pure and independently testable: bundle in, canonical dicts
out, no S3 and no network. The upload script owns staging, gzip, S3 and the
pre-flight assertions, and delegates every shape decision to the reader. This mirrors
`formats/skillcorner_bundle.py` and its upload script, and keeps the transform
testable without credentials.

### 6.2 Id types

StatsBomb ids are integers; `MatchEntry.id` and `PlayerRecord.id` are regex-validated
**strings** and pydantic v2 does not coerce int → str. Every id is cast with `str(...)`
at the reader boundary, matching `skillcorner_bundle.py:116`.

---

## 7. Testing

Fixtures are **synthetic** — no real club data enters the repo, matching the rule
already governing the restricted SkillCorner fixtures. Fixtures use placeholder ids
and invented team/player names, so no licensed `(id → entity)` tuple is committed.

| Test | Asserts |
|---|---|
| de-pivot round trip | column-orient input transposes to the exact record, value-for-value |
| shape detection — raw | a raw feed object passes through **unchanged** |
| shape detection — false positive | an object whose values are uniformly-shaped sub-objects but whose keys are **not** integer-like is **not** transposed |
| de-pivot post-condition | a dump yielding records without the expected id key raises |
| non-finite tokens | a payload containing bare `NaN` raises rather than propagating |
| **competition join — value** | the emitted `competition_id`/`season_id` are the joined row's, against a fixture with **multiple** competition rows where row 0 is the wrong one |
| competition join — arity | zero-match and multi-match fixtures both raise |
| **home/away — non-inversion** | with a fixture whose team ids would invert under a naive rule, `home_team_id` is the home side's; asserted by value, not by resolution succeeding |
| home/away — arity | unmatched name, duplicate name, and both-sides-same-id each raise |
| re-nest structure | output key set matches the canonical feed structure exactly |
| no fabrication | every field absent from the fixture export is `null` — asserted per field, not in aggregate |
| score coercion | float scores land as ints |
| envelope | `metadata` is a JSON object, not an array |
| index entry | `date`/`home`/`away` populated from the match row; a missing `match_date` raises |
| artifact keys | staged filenames yield exactly `events`, `freeze_frames`, `roster`, `metadata`, each satisfying the path-param regex |
| player names | a player with an empty nickname falls back to the full name; `firstName`/`lastName` are `null`; **no whitespace split occurs** |
| player position | the earliest spell wins; empty `positions[]` yields `null` |
| player exclusions | rating block and weight absent from the index |
| id types | every emitted id is `str` |
| visibility | every uploaded entry is `private`; a public value is rejected |
| pre-flight — frame/event join | a fixture with an orphan `event_uuid` raises **before** anything is staged |
| pre-flight — zero frames | an empty `frames` array raises — the orphan check alone passes vacuously |
| pre-flight — zero lineups | an empty `lineups` array raises — otherwise an empty roster is staged and the player upload is skipped silently |
| pre-flight — empty per-team squads | team blocks present but every per-team `lineup` empty (or the key absent) raises — the shape that passes both the zero-lineups guard and the team-id cross-check |
| pre-flight — lineups/events team agreement | lineups naming a team `events` never mention raises; so does a lineups file covering one side only |
| pre-flight — array shape | a passthrough file that is not a JSON array raises, naming the file; so does an array whose elements are not objects, naming the offending index |
| pre-flight — period coverage | a single-period fixture raises; one missing `Half End` raises |
| pre-flight — team count | a fixture with one or three distinct teams in `events` raises |

The two bolded rows are the tests that would catch a silently-wrong artifact; both
are written **red-first against a fixture engineered to fail the naive
implementation**, since a fixture with one competition row and unambiguous names
would pass either way.

### 7.1 Delivery coherence is a pre-flight assertion, not a manual step

The first archive was profiled by hand before design and is internally coherent: two
periods ending after regulation, **every freeze-frame `event_uuid` resolving to an
event id in the same bundle** (zero orphans), and anonymous freeze frames.

Hand-profiling does not survive "re-run, not rewrite." These checks therefore run as
**fail-loud assertions in `upload_statsbomb_club.py` before anything is staged**:

- `frames` is **non-empty** — the orphan check below passes vacuously on an empty
  list, and a 360 provider must not ship a match with no 360 data;
- `lineups` is **non-empty** — the same vacuous pass, one file over. An empty array
  is a valid JSON array, stages a valid-but-empty `roster` artifact, and skips the
  player-catalogue upload, so the match is indexed with no squad and no error;
- **every team block's `lineup` is non-empty** — the guard above tests the *outer*
  array, so a delivery whose two team blocks are present but whose per-team squads are
  all empty passes it, and passes the team-id cross-check below as well, because the
  `team_id`s are still there. That shape produces exactly the failure the previous
  bullet describes, with nothing left to catch it;
- every `event_uuid` in `frames` resolves to an `events` id;
- each of `events`, `frames` and `lineups` is a JSON array **of objects**. The
  element check is not pedantry: `["a", "b"]` is a list, so a container-only check
  passes it and the `AttributeError` resurfaces mid-coherence-pass with no file name
  attached;
- `events` covers ≥ 2 periods and contains a `Half End`;
- exactly two distinct teams appear in `events`;
- the team ids in `lineups` are **the same two** as in `events` — a contradiction
  inside a single export of a single fixture, which D-5's inference boundary says is
  precisely the detectable kind that must raise. It also catches a half-delivery
  (lineups for one side only), which otherwise resolves that side's gender to `null`
  and looks like a legitimately absent value;
- the §4.2 join and resolution rules succeed.

The synthetic-fixture tests prove the helpers work (§7's eight `pre-flight` rows);
the pre-flight run proves *this delivery* is coherent. Both are needed.

**A legitimately abnormal delivery** — an abandoned match, an unusual period
structure — is handled by the operator amending the **specific** assertion that no
longer applies, with the reason recorded in the delivery note. There is deliberately
**no `--force` flag**: a blanket override is what gets reached for under time
pressure, and it would disable every check at once, including the ones that were not
the problem.

### 7.2 Freeze-frame privacy — precise claim

Freeze frames carry no player identity **in the frame payload** — position plus
teammate/actor/keeper flags only. Identity is reachable via the `event_uuid` join to
`events`, so the frames are **pseudonymous-linkable, not anonymous**. Nothing in this
design depends on the stronger claim (owner tier, no de-identification in scope), and
it is stated precisely here so it is not later quoted in a context where it would be
load-bearing and wrong.

---

## 8. Cross-repo note (luxury-lakehouse)

The companion repo is mid-way through its own `statsbomb-commercial-360-containment`
plan, whose visibility plumbing has already shipped and been applied. It has **not
yet seen the real data format**, which is the direct motivation for D-2: what this
repo serves will define the contract that repo builds against.

This delivery bears on three of its open questions:

- **OQ-5 (blocked its commercial ingestion path) — resolved: the file-drop route.**
  The data arrives as an archive and is served from this repo under the owner token,
  not pulled from a separate credentialed StatsBomb account.
- **OQ-3 — resolved: events *and* 360.** Both are present, so the 360-dependent
  downstream models are in scope.
- **OQ-2 (its flip guard) — not resolved; unblocked for this delivery only.** The
  public catalogue carries no season overlapping this delivery, so no match here
  exists under both tiers. That is a **fact about this archive, not a policy
  decision**, and it must not be read as one. The recommendation is that the guard
  **stays armed** and is exercised by a synthetic overlap fixture on their side, so
  the policy question can remain open without leaving the guard unproven at the
  moment it first matters. The competition and season ids backing this claim are in
  the operator-local delivery note (§1), not in this repo.

Its `visibility` vocabulary already matches ours (`public` / `private`), which this
repo treats as a load-bearing cross-repo contract; nothing here changes it.

No change is made to that repo from this work.

---

## 9. ADR impact

**New — ADR 0010, faithful-feed mimicry.** Records D-2 with its envelope scope
boundary, D-4's honesty rule, and D-5's inside/outside-the-delivery inference line.
Worth its own ADR because it binds every future provider, and because it resolves a
genuine tension: the delivered copy and the provider's published format disagreed,
and we chose the provider's.

**Amended — ADR 0008, role-aligned artifact-key vocabulary.** This provider needs no
*new* role key: it uses the vocabulary as written, a provider exposing a subset is
already sanctioned, and §4.4's envelope decision applies that ADR rather than altering
it. What the work surfaced instead is a **gap in the record**. ADR 0009 extended the
vocabulary with `freeze_frames` and `physical` in v0.3.0 but wrote the extension only
into itself — so the canonical list lived in an ADR about *restricted tiers*, where no
consumer looking up the artifact-key contract would think to look. `statsbomb` is the
second provider to use `freeze_frames`, which is exactly the point at which a key stops
being one provider's detail and becomes vocabulary.

ADR 0008 therefore gains a purely **additive** `Amendments` section: a table of the
extensions to date, each attributed to the ADR that introduced it, plus the resulting
full vocabulary and a note that `statsbomb` deliberately has no `tracking`. Two `See
Also` entries are added — ADR 0009 (which extended the vocabulary) and ADR 0010 (which
governs artifact *content*, whereas 0008 governs the *envelope*). No original decision
text is rewritten and no consequence is reversed; a reader of the 2026-05-29 decision
still finds it intact.

`docs/decisions/README.md`'s immutability rule is widened in the same change to permit
exactly this: an additive, non-revisionary amendment recording a later ADR's extension
of a vocabulary the original defines, with the amending ADR named. Left unwidened, the
governance doc and the artifact would contradict each other from day one. The
supersession rule — a decision *change* is a new ADR, never an edit — is unchanged.

---

## 10. Out of scope

- Any public tier for `statsbomb`.
- Backfilling additional matches — the mechanism supports it; the data does not exist yet.
- Changes to the companion repo.
- De-identification of commercial StatsBomb data.

---

## 11. Open questions

- **OQ-A.** The two index files were byte-identical across both delivered archives,
  so a future delivery may still arrive column-orient. §4.1's shape detection makes
  this a non-event either way — but if fully raw index files can be obtained, §4.2's
  re-nest becomes unnecessary and its `null`s become real values.
- **OQ-B.** Whether the club receives its own copy of any derived artifact. Affects
  distribution, not this ingest.
