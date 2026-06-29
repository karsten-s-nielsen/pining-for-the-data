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
