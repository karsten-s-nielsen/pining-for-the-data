# ADR 0004 — CloudTrail Data Events on the Data Bucket

**Status:** Accepted
**Date:** 2026-05-03
**Context:** Private data tier (spec §7)

## Context

The mock provider API now serves restricted-tier content. The operator needs an audit trail of who downloaded what, when — both as a defensive control (detect anomalous enumeration patterns) and as a record for licensing/compliance conversations with upstream data providers (Gradient Sports, future Soccermatics Pro).

## Decision

Enable **CloudTrail data events** on the data bucket, landing in a separate audit bucket (`{project}-audit-{account_id}`) with a 365-day retention lifecycle policy. The trail captures all S3 GET/PUT/DELETE on the data bucket, including the GETs that follow presigned URLs (those happen directly between requester and S3, attributed to the requester's IAM principal).

The trail's advanced event selector excludes only `/providers.json` reads (true bookkeeping; never reveals private content). `/matches.json` and `/players.json` reads stay logged because enumeration via `/matches` and `/players` is the most likely abuse vector and the trail is its forensic record.

## Consequences

**Positive**
- Defensible record: "did this artifact get downloaded, and by whom?" is answerable.
- Enumeration detection: repeated `/matches` or `/players` reads from the same source IP / IAM principal show up as a clear pattern.
- Cost is negligible (~$0.10/100k events, projected well under $1/year at current scale).
- 365-day retention is the regulatory floor for most "did anyone access this in the last year?" questions; configurable via `log_retention_days`.

**Negative**
- Adds a second bucket + KMS-SSE + lifecycle config + CloudTrail trail to manage (Terraform module).
- CloudTrail data events are eventually delivered, not real-time (typically 5-15 minutes lag) — not suitable as a runtime alarm signal.

**Reversal cost**: low. Disabling the trail and emptying the audit bucket is a one-resource Terraform destroy.

## Alternatives Considered

- **S3 Server Access Logging instead of CloudTrail**: rejected — server access logs are best-effort delivery, log-line-formatted (harder to query than CloudTrail's structured JSON), and don't capture IAM principal cleanly.
- **No audit logging in v1; add later if needed**: rejected — the licence ambiguity around the initial Gradient Sports load (later resolved) made a defensible access trail valuable from day one. Cost is low enough that "add later" trades against "don't have history when you need it".
- **VPC Flow Logs**: out of scope — the data bucket is accessed over public S3 endpoints with presigned URLs, not from inside a VPC.

## See Also

- `docs/superpowers/specs/2026-05-02-private-data-tier.md` §7 — full audit logging design
- `docs/superpowers/specs/2026-05-02-private-data-tier.md` §7.5 — exclusion rule rationale (`/providers.json` only)
- `terraform/modules/audit/` — implementation
