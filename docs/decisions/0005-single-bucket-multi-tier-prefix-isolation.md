# ADR 0005 — Single Bucket + Single KMS Key, Multi-Tier via Prefix Isolation

**Status:** Accepted
**Date:** 2026-05-03
**Context:** Private data tier (spec §5.3)

## Context

The mock provider API serves both public-tier (redistributed open data) and private-tier (restricted Gradient Sports, etc.) content from the same infrastructure. AWS supports two reasonable shapes for tier-isolated S3:

- One bucket per tier (separate `pining-for-the-data-public`, `pining-for-the-data-private` buckets, separate KMS keys, separate IAM roles)
- One bucket with prefix isolation (`{provider}/...` for public, `{provider}/_private/...` for private; one KMS key; one IAM role)

## Decision

Use **one bucket and one KMS key**, with tier separation via the `_private/` path prefix (ADR 0002). The Lambda IAM role has read access to the entire bucket; tier enforcement is at the application layer (validate_token returns Tier; handlers filter content by tier).

## Consequences

**Positive**
- Single KMS rotation cycle, single bucket policy, single set of CloudTrail data events to maintain.
- Cost: one bucket vs two (storage and KMS pricing identical, but per-bucket fixed costs avoided).
- Operationally simpler: `aws s3 ls`, `aws s3 cp`, debugging, all happen in one place.
- Adequate blast-radius isolation for v1's single-private-consumer threat model: the only party with the OWNER token is the data owner themselves; an application-layer tier-check bug leaks data to a token holder who, by construction, IS the data owner.

**Negative**
- Cannot use IAM-level enforcement to add a second layer of defense against application bugs. Defense in depth comes only from the path-prefix convention (ADR 0002), not from the IAM policy.
- A future second private consumer (with access to a subset of restricted content) would need to be modelled at the application layer (per-token scope lists), not at the IAM layer (separate role per consumer scope).

**Reversal cost**: medium. Adding a second bucket later is mechanical (new Terraform resources, new IAM role); the data migration (moving private content to the new bucket) is the operationally significant part. The current design leaves the door open: ADR 0002's `_private/` prefix is exactly the boundary along which a future bucket split would occur.

## Alternatives Considered

- **One bucket per tier (separate KMS keys)**: rejected for v1 — doubles operational surface for blast-radius isolation that's already adequate at the application layer for the single-private-consumer setup. Re-evaluated when a second private consumer is onboarded.
- **One bucket per provider**: rejected — wrong axis. Tier (visibility) is the security boundary, not provider identity. A single provider can serve both public and private content (planned for SkillCorner straddling open + Soccermatics Pro cohort data).

## See Also

- `docs/superpowers/specs/2026-05-02-private-data-tier.md` §5.3 — full single-bucket rationale
- ADR 0002 — `_private/` reserved S3 prefix (the corollary defense-in-depth control)
- ADR 0001 — Owner token in SSM (the auth-side counterpart)
