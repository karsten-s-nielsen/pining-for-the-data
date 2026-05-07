# ADR 0007: Observability Baseline and SLI/SLO Definitions

## Status

Accepted

## Context

The five-audit review identified zero alerting, zero dashboards, and no defined SLIs or SLOs. The API was running with CloudTrail audit logging and X-Ray tracing, but no operational observability for detecting failures in real time.

## Decision

### SLIs (Service Level Indicators)

| SLI | Definition | Measurement |
|-----|-----------|-------------|
| Availability | Percentage of requests returning non-5xx responses | CloudWatch `5xx` / `Count` on API Gateway |
| Latency (p99) | 99th percentile response time | CloudWatch `Latency` p99 on API Gateway |
| Error rate | Percentage of Lambda invocations resulting in errors | CloudWatch `Errors` / `Invocations` per function |

### SLOs (Service Level Objectives)

| SLO | Target | Window | Rationale |
|-----|--------|--------|-----------|
| Availability | >= 99.5% | 30 days rolling | Low-traffic dev API; S3 + Lambda provide inherent 99.9%+ but cold starts and transient errors lower effective availability |
| Latency (p99) | < 3 seconds | 30 days rolling | Accounts for cold starts + S3 GET; warm path should be < 500ms |
| Error rate per function | < 1% | 30 days rolling | Individual function failures should be rare; most errors are expected 4xx |

### Alerting

CloudWatch alarms fire to an SNS topic (email) when:
- Any Lambda function errors >= 1 in a 5-minute window
- Lambda p99 duration > 8 seconds (approaching 10s timeout) for 2 consecutive periods
- Any Lambda throttles >= 1
- API Gateway 5xx count >= 1

### Dashboard

A single CloudWatch dashboard (`pining-for-the-data`) provides:
- API Gateway request count, 4xx, 5xx, and latency (p50/p99)
- Per-Lambda invocations, errors, duration (p99), and throttles

## Consequences

- Alarm noise will be low at current traffic levels; increase thresholds if alert fatigue occurs.
- Dashboard is Terraform-managed (survives destroy/recreate cycles).
- SLOs are internal targets, not external commitments. Revisit if the API serves external consumers at scale.
