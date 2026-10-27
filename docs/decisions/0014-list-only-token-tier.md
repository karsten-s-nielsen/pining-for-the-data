# 0014 — List-Only Token Tier (`LIST`) and the Unset Guard for SSM Tokens

## Status

Accepted

## Date

2026-10-27

## Context

The lakehouse v2 public Ops Space shows a filter preview that lists private tournaments to signed-in operators. It needs the private match list, but never the private data files. The API had two bearer tokens. The public token sees no private entry. The owner token sees everything, including every private artifact, for example the commercial StatsBomb 360 delivery. A compromised public Space that held the owner token could therefore read all private data. The lakehouse design chose a third, list-only token for the Space (its §29.6 option iii).

Two weaknesses of the existing token handling also became visible:

- Terraform creates each SSM SecureString with the value `placeholder-set-via-cli-after-first-apply`. The repo is public, so anyone knows that string. Nothing stopped it from working as a token between `terraform apply` and the operator's `aws ssm put-parameter`.
- `hmac.compare_digest` raises `TypeError` on a `str` with a non-ASCII character. A client that presented such a token crashed the Lambda instead of getting `401`. A stored LIST value with such a character would have broken every tier.

## Decision

**A third tier, `LIST`.** The token lives in the SSM SecureString `/pining-for-the-data/api_token_list` (the same shape, KMS key and out-of-band setup as the owner token, ADR 0001). The five authed Lambdas read it through `LIST_TOKEN_PARAM`.

| Endpoint | `PUBLIC` | `LIST` | `OWNER` |
|----------|----------|--------|---------|
| `/v1/providers` | full list | full list | full list |
| `/v1/{provider}/matches` | public entries | all entries; the body equals the `OWNER` body | all entries |
| `/v1/{provider}/matches/{id}/{artifact}` | `302` public, `404` private | `404` always, before any S3 read | `302` both |
| `/v1/{provider}/players`, `/players/{id}` | public records | the same as `PUBLIC` | public plus private, private wins |

The lakehouse never calls the player endpoints, and private player records hold full personal data, so `LIST` behaves like `PUBLIC` there. `LIST` gets the uniform `404` on every artifact, public ones included: public files stay reachable with the public token.

**One policy table.** `shared.py` maps every `Tier` to a frozen `TierPolicy(lists_private_matches, serves_artifacts, reads_private_content)`. Handlers ask `policy_for(tier)` and never compare a tier value. A tier missing from the table gets no access (fail closed). Tests check that the table covers every `Tier` member and that no handler refers to `Tier` or compares the tier variable.

**Classification.** `validate_token` encodes the presented token and each configured token with `str.encode("utf-8", "surrogatepass")` and compares bytes. Neither step can raise, so a non-ASCII token gets `401`. It runs every comparison before it branches. A token that matches two configured tokens classifies as `PUBLIC` and logs `token_collision`.

**Unset guard on both SSM tokens.** A stored value is unusable when it is the placeholder, has fewer than 32 characters, or is not one RFC 6750 `b64token` (`re.fullmatch`, so a trailing line break also fails). The fetcher then returns `None`, logs `owner_token_unset` or `list_token_unset` once per container, and that tier is disabled.

**Isolation.** A failure to read the LIST parameter disables only the LIST tier for that request (`list_token_fetch_failed`); PUBLIC and OWNER keep working. A failure to read the owner parameter still gives `500` to every request, as before.

## Consequences

**Positive**
- The Space can show the live private match list without any path to private data files.
- The public placeholder can never work as an owner or LIST token, even if a parameter is re-created.
- A non-ASCII token can no longer crash the Lambda.
- A new tier cannot inherit the wrong branch of a handler: it must get a row in the policy table.

**Negative**
- A compromised Space can read private match metadata: teams, dates, competition and season fields, artifact names and `artifact_meta` hashes. The lakehouse owner accepted this (its §29.6).
- A persistent LIST-parameter failure costs one SSM call and one error log on every authed request until it is fixed. The API Gateway throttle bounds it.
- Two behaviour changes for the existing tiers: a non-ASCII token gets `401`, and an unusable owner value disables the owner tier. The live owner value passes the guard.

**Operations**
- Rotation of either token: `put-parameter`, then the GitHub variable `LAST_ROTATION` and a local `terraform apply` with the same value (`docs/api-reference.md`, "Rotation").
- Kill switch for `LIST`: store the placeholder as its value and run the rotation's last steps. The guard disables the tier; PUBLIC and OWNER are not affected.

**Reversal cost**: low. Reverting the change removes the parameter, the env vars and the `LIST` branch. PUBLIC and OWNER return to the 0.9.0 behaviour.

## Alternatives Considered

- **The owner token in the Space** (lakehouse §29.6 option i): rejected. A compromised Space could read every private artifact.
- **A cached private list written by the owner-token Job** (lakehouse §29.6 option ii): rejected by the lakehouse owner. The preview would not be live.
- **General per-token scopes** (private-data-tier spec §11.1): out of scope. One fixed list-only tier meets the need.
- **A placeholder check without a length and syntax rule**: rejected. A pasted value with a line break or a smart quote would pass and then match nothing, which disables the tier without a log.

## See Also

- `docs/superpowers/specs/2026-10-27-list-token-tier-design.md` — the design, owner decisions D1–D8
- ADR 0001 — SSM Parameter Store for bearer tokens
- `terraform/modules/functions/src/shared.py` — `TierPolicy`, `policy_for`, `_usable_token`, `validate_token`
- `scripts/verify_list_token_tier.py` — the live verify
