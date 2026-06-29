# Architecture Decision Records

This directory contains [Architecture Decision Records](https://adr.github.io/) (ADRs) — short documents capturing the context, decision, and consequences of non-obvious architectural choices in the project.

## Index

| # | Title | Status | Context |
|---|-------|--------|---------|
| [0001](0001-owner-token-in-ssm-parameter-store.md) | Owner Token Storage: SSM Parameter Store, not Secrets Manager | Accepted | Private data tier auth (spec §3.4) |
| [0002](0002-private-prefix-tier-separation.md) | Tier Separation via `_private/` S3 Prefix | Accepted | Defense in depth (spec §5) |
| [0003](0003-resource-noun-endpoints-no-files-escape-hatch.md) | Resource-Noun Endpoints (`/players`), Not a Generic `/files` Escape Hatch | Accepted | Reference data API design (spec §6) |
| [0004](0004-cloudtrail-data-events-on-data-bucket.md) | CloudTrail Data Events on the Data Bucket | Accepted | Audit trail for restricted content (spec §7) |
| [0005](0005-single-bucket-multi-tier-prefix-isolation.md) | Single Bucket + Single KMS Key, Multi-Tier via Prefix Isolation | Accepted | S3 layout for tier-aware serving (spec §5.3) |
| [0006](0006-canonical-models-outside-lambda-source.md) | Canonical Pydantic Models Live Outside the Lambda Source Directory | Accepted | Lambda runtime hardening; emerged at deploy (no spec section) |
| [0007](0007-observability-baseline.md) | Observability Baseline and SLI/SLO Definitions | Accepted | Observability module (audit remediation) |
| [0008](0008-role-aligned-artifact-key-vocabulary.md) | Role-Aligned Artifact-Key Vocabulary Across Providers | Accepted | IDSSE public redistribution (spec §3.1) |
| [0009](0009-restricted-tier-under-existing-public-provider.md) | Restricted Data Under an Existing Public Provider | Accepted | SkillCorner RM owner-tier ingest (spec §13) |

## Format

Filename: `NNNN-title-of-decision.md` (zero-padded sequential).

Sections:

1. **Status** — Proposed / Accepted / Deprecated / Superseded by ADR-NNNN
2. **Date** — ISO 8601
3. **Context** — what problem we're solving and what constraints apply
4. **Decision** — what we chose
5. **Consequences** — positive, negative, reversal cost
6. **Alternatives Considered** — what we rejected and why
7. **See Also** — links to spec sections, related ADRs, implementation files

ADRs are immutable once Accepted: a decision change becomes a NEW ADR (with `Superseded by` set on the original), not an edit. The Status and Consequences sections may be updated to reflect lived experience over time.
