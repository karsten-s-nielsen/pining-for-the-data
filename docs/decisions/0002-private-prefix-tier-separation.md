# ADR 0002 — Tier Separation via `_private/` S3 Prefix

**Status:** Accepted
**Date:** 2026-05-03
**Context:** Private data tier (spec §5)

## Context

The mock provider API serves two visibility tiers (`public`, `private`) from a single S3 bucket. Restricted content must be unreachable to public-token holders even if the application-layer tier check fails.

## Decision

Restricted-tier content is written under a reserved S3 path segment `_private/`:

- Public match: `{bucket}/{provider}/{game_id}/...`
- Private match: `{bucket}/{provider}/_private/{game_id}/...`
- Public players index: `{bucket}/{provider}/players.json`
- Private players index: `{bucket}/{provider}/_private/players.json`

The path-param validator rejects any path component starting with `_` (regex `^[a-zA-Z0-9][a-zA-Z0-9_-]*$`), making `_private` an unreachable input from API callers. The reserved segment is enforced both at the API layer (handlers refuse to serve `_private` as a path-param value) and at the upload layer (CLIs refuse to write to it directly).

## Consequences

**Positive**
- Defense in depth: two independent layers (application tier check + S3 path) must both fail for a leak to occur.
- Future-proofs IAM-level enforcement: separate Lambdas with separate roles can later be scoped to `arn:aws:s3:::{bucket}/{provider}/*` (excluding `_private/`) without restructuring data.
- Visible at a glance in S3 console — operator can spot mistakes.
- Backwards-compatible with existing public content (no path change for current SkillCorner data).

**Negative**
- Reserves a path segment globally; provider IDs and match IDs starting with `_` are forbidden (no real provider does this; PFF and SkillCorner both qualify).
- Two physical files for the players index instead of one — slightly more upload-tool complexity.

**Reversal cost**: medium. Removing the convention requires moving objects and rewriting indexes; the path-param validator change is trivial but data migration is operationally significant.

## Alternatives Considered

- **Separate buckets per tier**: rejected — doubles operational surface (Terraform, KMS rotation, logging, monitoring) for blast-radius isolation already adequate at the application layer for v1's single-private-consumer setup. Easier to add later if needed.
- **Encrypted-per-object KMS keys (separate keys for private content)**: rejected — KMS key rotation, IAM granularity, and cost-per-key all scale poorly; current approach can adopt this later as a Phase-2 hardening if a second private consumer requires fine-grained access control.

## See Also

- `docs/superpowers/specs/2026-05-02-private-data-tier.md` §5 — full S3 layout design
- `docs/superpowers/specs/2026-05-02-private-data-tier.md` §5.2 — reserved-segment rules
- ADR 0005 — Single bucket + single KMS key (the corollary decision this ADR depends on)
