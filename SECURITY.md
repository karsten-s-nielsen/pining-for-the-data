# Security Policy

## Scope

This project serves two tiers of data through the mock provider API:

- **Public tier**: SkillCorner open data (MIT-licensed) and any other operator-loaded public-tier content. Served to holders of the documented `api_token` (default `test-token-pining-for-the-data`, configurable via `terraform.tfvars`). The public token is documented by design — its purpose is to exercise authentication code paths, not real access control. Public-tier content is not sensitive.
- **Owner tier**: Operator-loaded restricted content (e.g., PFF FC FIFA World Cup 2022). Served only to holders of the OWNER bearer token, which lives in AWS SSM Parameter Store as a SecureString (`/pining-for-the-data/api_token_owner`), encrypted with the data bucket's KMS key. The OWNER token is never committed to the repo. Restricted-tier content lives under the reserved `_private/` S3 prefix; defense in depth enforces tier separation at both the application layer (uniform 404 on tier mismatch) and the S3 path layer.

CloudTrail data events on the data bucket (excluding `/providers.json` only) provide a 365-day audit trail of all reads and writes, including the GETs that follow presigned URLs.

For full design rationale see `docs/superpowers/specs/2026-05-02-private-data-tier.md` and ADRs 0001-0005 in `docs/decisions/`.

## Reporting a Vulnerability

If you discover a security issue in this project's code or infrastructure:

1. **Do not** open a public GitHub issue.
2. Email the maintainer directly (see GitHub profile for contact information).
3. Include a description of the vulnerability, steps to reproduce, and potential impact.

You should receive an acknowledgment within 72 hours. Fixes for confirmed vulnerabilities will be released as soon as practical.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x | Yes |
