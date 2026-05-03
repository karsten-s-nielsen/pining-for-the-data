# ADR 0003 — Resource-Noun Endpoints (`/players`), Not a Generic `/files` Escape Hatch

**Status:** Accepted
**Date:** 2026-05-03
**Context:** Private data tier (spec §6)

## Context

Some provider data is not associated with a single match: player biographical data, team metadata, competition catalogues. The API needs a place to serve this. Two shapes were considered:

- A generic `/files` (or `/blobs`) endpoint that takes a path and returns whatever's there
- First-class resource nouns (`/players`, `/teams`, `/competitions`) modelled like REST resources

## Decision

Use **first-class resource nouns**, modelled after industry providers (StatsBomb, Wyscout, Opta). v1 ships `/v1/{provider}/players` (list) and `/v1/{provider}/players/{id}` (single record). Future nouns (`/teams`, `/competitions`, etc.) follow the same template and add no architectural complexity.

A canonical Pydantic model (`PlayerRecord`) defines the shape per resource type. Provider-specific extensions (PFF's `firstName`/`lastName`/`positionGroupType`) are recognised verbatim via `additionalProperties: true`.

## Consequences

**Positive**
- Self-documenting API: endpoint names explain what's there. A consumer reading the OpenAPI surface knows what the API offers without an out-of-band file index.
- Forces explicit modelling: adding `/teams` makes the operator decide what a team is and what its canonical shape is, rather than dumping arbitrary blobs and asking consumers to guess.
- Pydantic models enable schema validation at write time and drift testing in CI (spec §6.6).
- Maps cleanly onto existing tier-aware filtering (`/players` reuses the same visibility/Tier machinery as `/matches`).

**Negative**
- Each new resource type requires new Lambda handler(s) + Terraform routes + tests. ~150-200 lines of code per resource, mostly mechanical.
- Cannot serve "miscellaneous" provider files without first deciding what the noun is.

**Reversal cost**: low for adding a `/files` endpoint later if genuinely needed (it would be additive). High for retrofitting noun resources back over an existing `/files` consumer base.

## Alternatives Considered

- **Generic `/files` endpoint with whitelist**: rejected — incentivises lazy modelling. The first time someone needs to serve "that PDF schema doc", they'd reach for `/files` and the API surface degrades into a path-traversal-shaped opaque blob store. Better to add a noun (e.g., `/schemas`) when there's actual demand.
- **OpenAPI codegen from a single endpoint definition**: rejected — overkill for v1; would mean adopting a code generator and OpenAPI contract before the first consumer even exists.

## See Also

- `docs/superpowers/specs/2026-05-02-private-data-tier.md` §6 — reference resources design
- `docs/superpowers/specs/2026-05-02-private-data-tier.md` §6.6 — Pydantic + JSON Schema lifecycle
- `docs/superpowers/specs/2026-05-02-private-data-tier.md` §6.7 — what we explicitly do NOT add
