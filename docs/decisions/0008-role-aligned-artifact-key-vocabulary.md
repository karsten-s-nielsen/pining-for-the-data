# ADR 0008 — Role-Aligned Artifact-Key Vocabulary Across Providers

**Status:** Accepted
**Date:** 2026-05-29
**Context:** IDSSE public redistribution (spec §3.1); cross-repo consumer contract

## Context

The API serves match artifacts by name via `GET /v1/{provider}/matches/{id}/{artifact}`. The artifact keys are the API allowlist (`MatchEntry.artifacts` maps key → filename) and are what consumers request. Different providers ship different file sets in different wire formats:

- SkillCorner — V3 match JSON + tracking JSONL
- Gradient Sports — `metadata` / `events` / `roster` / `tracking` (JSON, JSONL.bz2)
- IDSSE/DFL — raw DFL XML: matchinformation, events_raw, positions_raw_observed

Adding IDSSE forced the question: what should its artifact keys be? Without a shared convention, every consumer needs a per-provider artifact-key map. This matters now because a downstream consumer (the `silly-kicks` TF-24 calibration loader) is being built provider-agnostic — "switch `provider="idsse"` for free" — on the assumption of a uniform vocabulary.

## Decision

**Artifact keys denote a *role*, aligned across providers to a shared vocabulary** — `metadata`, `events`, `tracking` (plus `roster` where a provider has one). The key describes the role, **not** the wire format.

IDSSE aligns to this vocabulary:

| Artifact key | IDSSE DFL source file |
|---|---|
| `metadata` | `…matchinformation…` |
| `events` | `…events_raw…` |
| `tracking` | `…positions_raw_observed…` |

A provider exposing only a *subset* of the vocabulary is expected and fine (IDSSE has no `roster` — roster data lives inside `metadata`). The contract is only that a shared key denotes the same role across providers.

The legacy **`skillcorner`** provider predates this convention and uses id-prefixed keys (`<match_id>_match`, `tracking`). It is documented as a **known exception**: a single uniform vocabulary does not exist across *all* current providers, and consumers still need a small per-provider map for SkillCorner specifically.

## Consequences

**Positive**
- A provider-agnostic consumer selects artifacts by role key, then dispatches to a provider-specific parser (`idsse → Sportec/floodlight`). New providers that follow the vocabulary need no consumer-side key map.
- Keys are self-describing by role, decoupled from the (provider-specific) wire format.
- The decision is now a first-class, discoverable contract rather than an implicit detail buried in one spec.

**Negative**
- `skillcorner` remains an exception requiring a per-provider map — a Hyrum's Law cost of not retrofitting it.
- The wire format is intentionally **not** encoded in the key, so consumers must still know each provider's format out-of-band (e.g. `idsse` ⇒ XML, `gradientsports` ⇒ JSON/JSONL).
- Onboarding a new provider requires a deliberate role-mapping step rather than passing source filenames through verbatim.

**Reversal cost**: low — the vocabulary is convention, applied per provider at upload time; a future provider could deviate, at the cost of consumer-side mapping.

## Alternatives Considered

- **Format-encoded keys** (e.g. `tracking_xml`, `tracking_jsonl`): rejected — couples the API allowlist to the wire format and breaks the "switch provider for free" consumer pattern.
- **Per-provider arbitrary keys** (pass DFL source filenames through verbatim): rejected — pushes a key-mapping burden onto every consumer for every provider.
- **Backfill `skillcorner` to the vocabulary**: deferred — existing consumers depend on the current SkillCorner keys (Hyrum's Law); a migration is possible later but out of scope here. Documented as the known exception instead.

## See Also

- `docs/superpowers/specs/2026-05-29-idsse-bundesliga-redistribution-design.md` §3.1 — artifact-key vocabulary + SkillCorner divergence
- ADR 0003 — resource-noun endpoints (related API-shape decision)
