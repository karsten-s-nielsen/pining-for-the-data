# 0012 — StatsBomb Open-Data as a Second Source Family

## Status

Accepted

## Date

2026-09-06

## Context

The `statsbomb` provider serves one thing: a **commercial** 360 club delivery
(Racing Louisville, NWSL) at the owner tier, `provenance="original"` (ADR 0010). This
decision adds a **second source** under the same slug — StatsBomb's **public
open-data** 360 corpus for the six competitions whose 360 coverage is a *complete
tournament* (FIFA World Cup 2022, Women's World Cup 2023, UEFA Euro 2020/2024, UEFA
Women's Euro 2022/2025 — 292 matches, all with 360).

**The licence is the load-bearing fact.** StatsBomb open data is freely accessible but
**not freely redistributable**. The binding *StatsBomb Public Data User Agreement*
(`LICENSE.pdf`) forbids providing the data to any external or third party (1.2.1),
forbids commercial exploitation (1.2.2), and reserves all IP to StatsBomb (§7). The
repo `README.md`'s attribution note governs *derived analysis*, not raw redistribution.

That constrains the tier. A pining **public** tier hands the documented public
`api_token` to every consumer — literally "provide the data to a third party" — so it
is forbidden. The **owner** tier, gated to a single bearer token held only by the
operator, is a **party of one**: no external or third party exists, so 1.2.1/1.2.2 are
not triggered. Copying the JSON onto our own S3 for our own use is materially the same
act as `statsbombpy` caching the raw feed to local disk. The load-bearing condition is
that the data **stays single-user end-to-end** — the analysis flips to a breach the
moment the raw bytes reach another person.

Two same-tier sources now sit under one slug, which raises a question ADR 0009 never
faced: when both sources are already private, what distinguishes them, and how does the
shared player catalogue avoid one clobbering the other?

## Decision

**StatsBomb open data is a second source family under the existing `statsbomb` slug,
owner tier, `provenance="redistributed"`, in the same faithful-feed shape and artifact
vocabulary the commercial family already uses.** Four choices bound it.

**D-1 — Same slug, new provenance, same private tier.** ADR 0009 established that data
from the same provider goes under the same slug and that the *tier* dimension expresses
restriction. Here both StatsBomb sources are already at the same (private) tier, so
tier does not distinguish them — **`provenance` does** (`redistributed` vs the
commercial family's `original`), and `source.licence` records which agreement governs
each match (open: *"StatsBomb Public Data User Agreement (open data); redistribution to
third parties not permitted"*). A consumer wanting only one kind filters on
`provenance`. This is a **new `(redistributed, private)` combination**: existing
redistributed providers (SkillCorner A-League, IDSSE) are `public`; existing private
data (`gradientsports`, restricted SkillCorner, commercial `statsbomb`) is `original`.
The combination is meaningful, not accidental — it is the machine-readable statement
"data we re-host under someone else's restrictive licence."

**D-2 — Faithful-feed, gzip JSON; ADR 0011-style Parquet considered and rejected.**
Artifacts stay `events.json.gz` / `freeze_frames.json.gz` / `roster.json` /
`metadata.json`, upholding ADR 0010 (open data is the real published feed — the purest
case for serving it as-is). Parquet was evaluated the way ADR 0011 adopted it for
SkillCorner, and rejected here on the measurement: the whole 292-match corpus is
~0.37 GB gzipped, and Parquet/zstd would save only **~80 MB** across the corpus (the
freeze-frame artifact). ADR 0011's premise does not transfer — it adopted Parquet
because continuous 25 Hz SkillCorner tracking was tens of GB per match; StatsBomb 360
freeze frames are per-event snapshots and carry no such pressure. Against the 80 MB:
reversing a deliberate ADR, migrating the 30 existing commercial matches (or running
two formats under one slug — the opposite of "unified"), and forcing the consumer to
write a pining-proprietary SB360-Parquet parser. Gzip JSON is the unified *and*
efficient answer and requires **zero migration** of existing data.

**D-5 — Same-tier player collision: the open adapter yields to the richer record
(skip-and-report).** Both StatsBomb sources write the same
`statsbomb/_private/players.json`, StatsBomb player ids are global, and Racing
Louisville's roster includes national-team players who also appear in the women's open
tournaments — so overlap is expected. Open lineups lack `birth_date`/`player_height`,
so a naive open upload running after the commercial one would replace a richer record
(real dob/height) with a sparser one (both null) — order-dependent data loss. The open
upload script therefore **skips any player id already present in the private index**,
uploading only genuinely-new ids and reporting the skip count, seeded from a startup
snapshot that also absorbs intra-run adds. The outcome is order-independent and
lossless: the *sparser* source (open) yields; the *richer* source (commercial) always
writes its full record. This extends ADR 0009's cross-tier skip-and-report pattern to
the **same-tier richer-vs-sparser** case, leaving the shared `upload_players` writer
untouched. The skip is a catalogue-level dedup only — every match's full lineup remains
in that match's `roster` artifact.

**D-7 — Owner tier is the only tier; the single-user basis is the licence rationale.**
There is **no public tier for any StatsBomb open data, ever, without StatsBomb's
express prior written consent.** The compliance argument in the Context (a party of
one) holds only while the data stays single-user end-to-end; a public tier would
dissolve it.

## Consequences

**Positive:** One unified owner-tier StatsBomb 360 corpus under one slug, one metadata
schema, one artifact vocabulary — a consumer sees a single contract and filters the two
families on `provenance`. No new slug, no Terraform/Lambda change (a provider is an S3
prefix). No migration of the 30 existing commercial matches (D-2). The open adapter is
*simpler* than the commercial one — the source is the real feed, so no de-pivot, no
competition join, no team-id resolution, no lineup-gender helper.

**Negative / accepted:** Two source families now coexist under `statsbomb`, so onboarding
and verification must exercise both `original` and `redistributed`. The licence
compliance is an operational invariant, not a code guarantee: it rests on the data
staying single-user, so any downstream republication (including a luxury-lakehouse
public HuggingFace dataset) would breach `LICENSE.pdf` 1.2.1 — a consumer keying on
`provenance` should treat `redistributed`+`private` as "restricted, do not republish."

**Reversal cost:** Low for the format choice (D-2 is per-family at ingest). Moderate for
the tier/provenance decision once published — re-tiering or re-slugging means
re-uploading under a new prefix and rebuilding indexes.

## Alternatives Considered

- **A public tier for the open data** (it is "open", after all). Rejected: `LICENSE.pdf`
  1.2.1 forbids providing the data to any third party, and the documented public token
  does exactly that. The "open" in "open data" is free *access*, not free
  *redistribution* (D-7).
- **A new owner-only slug (e.g. `statsbomb-open`).** Rejected: fragments one data
  provider into two nouns and duplicates attribution — the same reason ADR 0009
  rejected `skillcorner-restricted`. `provenance` already distinguishes the two sources
  without a new slug (D-1).
- **Columnar Parquet/zstd, following ADR 0011.** Rejected on the measured ~80 MB corpus
  saving, which does not justify reversing ADR 0010, migrating the commercial matches,
  and imposing a bespoke parser on the consumer (D-2).
- **Field-level merge in `upload_players`** so open and commercial records combine.
  Rejected: it changes a writer every provider depends on. Skip-and-report in the open
  adapter achieves order-independent losslessness without touching shared code (D-5).

## See Also

- Spec: `docs/superpowers/specs/2026-09-06-statsbomb-open-tournaments-360-owner-tier-design.md`
- ADR 0009 (restricted tier under an existing public provider — same-slug/tier
  principle and the cross-tier skip-and-report this extends)
- ADR 0010 (faithful-feed mimicry — upheld here, not amended; the open feed is its
  purest case)
- ADR 0011 (canonical owner-tier SkillCorner Parquet — the Parquet path D-2 considered
  and rejected for this corpus)
- Implementation: `src/formats/statsbomb_open.py`, `scripts/upload_statsbomb_open.py`
