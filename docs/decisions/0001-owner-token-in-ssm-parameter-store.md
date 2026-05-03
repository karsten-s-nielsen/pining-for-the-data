# ADR 0001 — Owner Token Storage: SSM Parameter Store, not Secrets Manager

**Status:** Accepted
**Date:** 2026-05-03
**Context:** Private data tier (spec §3.4)

## Context

The mock provider API gains a second bearer token (the "owner" token) to authorise access to restricted-tier content. The token must live somewhere outside the repo (no plaintext in tfvars), encrypted at rest, with auditable access. AWS provides two managed primitives that fit: SSM Parameter Store SecureString and Secrets Manager.

## Decision

Use **SSM Parameter Store SecureString** (`/pining-for-the-data/api_token_owner`), encrypted with the same KMS key as the data bucket. Lambdas fetch the token via `boto3.client("ssm").get_parameter(WithDecryption=True)` and cache it in module scope (`functools.cache`-decorated) for the lifetime of the warm container. Rotation is operator-driven via `aws ssm put-parameter --overwrite`; warm-container cache invalidation is handled by bumping a no-op `LAST_ROTATION` env var on every Lambda via `terraform apply`.

## Consequences

**Positive**
- Maintenance profile is identical to Secrets Manager for manual rotation (one CLI command).
- Cost is trivial (~$0.05/month per advanced parameter).
- Fewer moving parts: no rotation Lambda to author and maintain.
- KMS key is already provisioned for the data bucket; reused for SSM SecureString.

**Negative**
- No built-in automatic rotation (Secrets Manager's differentiator).
- No native dual-validity grace window during rotation — consumers must implement 401 retry (spec §3.5).

**Reversal cost**: low. Swapping to Secrets Manager later requires changing one Terraform resource and the boto3 fetch call (two lines).

## Alternatives Considered

- **Secrets Manager**: rejected because automatic rotation is the only feature-differentiator and we don't need it for a single-owner static bearer.
- **Vault env-var injection**: rejected as out-of-scope infrastructure for a single-Lambda fleet.
- **Vault file in S3**: rejected — adds a private bucket plus retrieval logic for no security uplift over SSM.

## See Also

- `docs/superpowers/specs/2026-05-02-private-data-tier.md` §3.3, §3.4 — full design rationale
- `docs/superpowers/specs/2026-05-02-private-data-tier.md` §3.5, §11.6 — rotation handshake and future zero-downtime upgrade path
