# LIST Token Tier (List-Only Bearer Token) — Design

**Date:** 2026-10-27
**Status:** Approved by the owner on 2026-10-27 (revision 3, with D1–D8 and the 13 author's calls in §11). A and B approved revision 2 in round 2; revision 3 applies their round-2 CONSIDER items.
**Branch:** `feat/list-token-tier`
**Requested by:** the lakehouse v2 ingestion design, through the owner. The lakehouse references are in the luxury-lakehouse repository, `v2/docs/specs/2026-10-26-v2-ingestion-design.md` (§7.2, §26 item 16, §29.6, §29.8) and `v2/docs/plans/2026-10-26-v2-ingestion-plan.md` (gate G-F).

## 1. Purpose

Add a third bearer-token tier, `LIST`, to the mock provider API. A LIST token can see every match entry, public and private, on the match list endpoint. It cannot fetch any artifact, and it cannot see private player records.

The lakehouse v2 public Ops Space holds this token. Its filter preview lists private tournaments for signed-in operators. If the Space held the owner token, a compromised Space could read every private artifact, for example the commercial StatsBomb 360 delivery. The LIST token removes that risk: a compromised Space can read private match metadata, but no private data files.

The lakehouse cannot close its cycle 1 until this tier exists and passes its live check (lakehouse §29.8, plan gate G-F).

This cycle also hardens the owner token in two ways that the owner approved (D6, D8): the same unset guard, and a token comparison that cannot raise.

## 2. Current state

- `validate_token` (`terraform/modules/functions/src/shared.py`) returns `Tier.PUBLIC`, `Tier.OWNER`, or a 401 or 500 error response.
- The public token comes from the `API_TOKEN` env var. The owner token comes from the SSM SecureString `/pining-for-the-data/api_token_owner`, fetched once per warm container (`functools.cache`), through the `OWNER_TOKEN_PARAM` env var (ADR 0001). An owner-token fetch failure gives 500 to every request.
- If the presented token equals the public token, the result is `PUBLIC`, also when the owner token has the same value (fail closed).
- `hmac.compare_digest` compares `str` values. It raises `TypeError` when either string has a non-ASCII character. So today a client that presents a non-ASCII token gets an unhandled exception, not a 401.
- Handlers test the tier directly: `list_matches` filters with `tier != Tier.OWNER`, `get_artifact` gates private matches with `tier != Tier.OWNER`, and `list_players` and `get_player` read the private index only when `tier == Tier.OWNER`. `list_providers` ignores the tier. `health` has no auth.
- Terraform creates the owner SSM parameter with the placeholder value `placeholder-set-via-cli-after-first-apply` and `ignore_changes = [value]`. The placeholder is in this public repo. Nothing stops it from working as the owner token if the parameter is ever re-created and not set.
- `.github/workflows/terraform-apply.yml` runs `terraform apply -auto-approve` on every push to `main` that changes `terraform/**` or the workflow file. It has no manual trigger. **A merge of this cycle's PR is therefore a live infrastructure change.** `terraform-plan.yml` posts the plan on the PR.
- CI passes `TF_VAR_last_rotation: ${{ vars.LAST_ROTATION }}`. The GitHub repository variable `LAST_ROTATION` holds `20260504T024554Z` (read 2026-10-27). The CI/CD hardening spec (`2026-05-07-ci-cd-hardening.md` §3.3) says that the operator updates this variable together with the SSM parameter. `docs/api-reference.md` and `AGENTS.md` describe only the local `terraform apply -var=last_rotation=...` step.
- Live facts read on 2026-10-27, with no secret printed: the owner token is not the placeholder, has at least 32 characters, and matches the RFC 6750 `b64token` syntax. The owner parameter uses the project KMS key (customer managed), version 2.

## 3. Owner decisions

| # | Date | Decision |
|---|------|----------|
| D1 | 2026-10-26 | Pining issues a list-only token tier. The lakehouse Space holds it (lakehouse §29.6 option iii). The tier is a prerequisite for closing lakehouse cycle 1 (lakehouse §29.8). |
| D2 | 2026-10-26 | Own branch and spec, one commit with the owner's approval, A/B reviews, an ADR, and tests per tier on every endpoint. The LIST token lives in a new SSM SecureString. A token collision classifies as `PUBLIC` (fail closed, as in ADR 0001). |
| D3 | 2026-10-27 | LIST behaves like PUBLIC on `GET /v1/{provider}/players` and `GET /v1/{provider}/players/{id}`: no private player records. The lakehouse never calls these endpoints, and private records hold full personal data. |
| D4 | 2026-10-27 | LIST gets the uniform 404 on **every** artifact, public or private. This matches lakehouse §26 item 16 ("gets 404 on every artifact"). Public artifacts stay reachable with the public token. |
| D5 | 2026-10-27 | The owner coordinates with the lakehouse session. This cycle sends nothing to that session. The owner gives the LIST token value to the Space. |
| D6 | 2026-10-27 | The unset guard (§5.4) applies to the owner token too (B-SPEC-03). |
| D7 | 2026-10-27 | These stay out of this cycle: general per-token scopes, dual-validity or automatic rotation, alarms or metrics on LIST traffic, and the lakehouse Space setup (B-SPEC-03). |
| D8 | 2026-10-27 | Token comparison uses UTF-8 bytes, so it cannot raise. A non-ASCII presented token gets 401 on every tier. With D6, this is one of the two approved changes to PUBLIC and OWNER behaviour (LTT-SPEC-01). |

The author's calls in §11 need the owner's explicit approval at the spec gate (§6 step 1).

## 4. Goals, non-goals, anti-goals

**Goals**
- G1. A LIST token gets the same `GET /v1/{provider}/matches` response as the owner token, byte for byte, for every provider and every filter.
- G2. A LIST token gets the uniform 404 on every artifact request. The handler reads nothing from S3 and signs no URL for it.
- G3. A LIST token gets the same responses as the public token on both player endpoints.
- G4. PUBLIC and OWNER behaviour does not change on any endpoint, with two approved exceptions. D8: a non-ASCII presented token now gets 401. D6: an unusable owner value (§5.4) now disables the OWNER tier. The live owner value passes the guard (§2), so D6 changes nothing on the deployed API today.
- G5. A misconfiguration never gives a tier more access than this design states. An unusable LIST or owner value (§5.4) disables that tier. A collision between tokens classifies as `PUBLIC`.
- G6. A problem with the LIST parameter, including its value, never breaks the PUBLIC or OWNER tiers.
- G7. Proof. The live verify script (§5.7) proves G1 for every provider with no filter and with each filter, the 404 part of G2, G3, the placeholder part of G5, and G4 for the list endpoints and the owner artifact redirect (by comparison with a snapshot taken before the merge). The unit tests (§7.1) are the proof for the rest: the "no S3 read, no signed URL" part of G2, G4 on every endpoint, the short-value, syntax and collision parts of G5, and G6.

**Non-goals** (owner decision D7)
- General per-token scopes (private-data-tier spec §11.1 future work).
- Dual-validity or automatic rotation.
- New alarms or metrics for LIST traffic. Our verify script and the lakehouse live test request artifacts with the LIST token on purpose, so an alarm would fire on every verify run.
- The lakehouse Space configuration. The owner does that (D5).

**Anti-goals** (failure even if the code "works")
- A LIST token reaches artifact bytes by any path, including a presigned URL.
- A LIST token reaches a private player record.
- The documented Terraform placeholder works as a LIST or owner token. The repo is public, so anyone knows that string.
- A token value, a private match id or a private player id appears in a log line, a test fixture, a doc, or script output.

## 5. Design

### 5.1 Tier policy

`Tier` gains a third member: `LIST = "list"`. One table in `shared.py` decides what each tier may do:

- A frozen dataclass holds three booleans, one per capability below.
- A dict maps every `Tier` member to its policy.
- The policy lookup **fails closed**: a tier that is missing from the dict gets the policy with every capability `False`.
- Handlers pass the tier to the policy lookup only. They never compare a tier value, neither with a `Tier` member nor with a string literal. `Tier` is a `StrEnum`, so a comparison with `"owner"` would otherwise work silently.

| Capability | PUBLIC | LIST | OWNER |
|------------|--------|------|-------|
| See private entries in the match list | no | **yes** | yes |
| Fetch artifacts at all | yes | **no** | yes |
| Fetch private artifacts | no | no | yes |
| See private player records | no | no | yes |

Names are the builder's choice. Author's suggestion: `TierPolicy(lists_private_matches, serves_artifacts, reads_private_content)` and `policy_for(tier) -> TierPolicy`.

### 5.2 Endpoint behaviour

| Endpoint | PUBLIC | LIST | OWNER |
|----------|--------|------|-------|
| `GET /v1/health` | no auth, 200 | no auth, 200 | no auth, 200 |
| `GET /v1/providers` | 200, full list | 200, full list | 200, full list |
| `GET /v1/{provider}/matches` | public entries | all entries; body equals the OWNER body | all entries |
| `GET /v1/{provider}/matches/{id}/{artifact}` | 302 for public; 404 for private | **404 always**, no S3 read | 302 for both |
| `GET /v1/{provider}/players` | public records | equals the PUBLIC body | public plus private, private wins |
| `GET /v1/{provider}/players/{id}` | public record; 404 for private | equals the PUBLIC response | private first, then public |

These rules apply the same way to every tier:
- A malformed or unknown token gets 401, including a non-ASCII token (D8). A missing `API_TOKEN` gets 500. An owner-token fetch failure gets 500.
- An invalid path parameter or filter gets 400. `get_artifact` validates its path parameters **before** the LIST gate, so a LIST request with an invalid parameter gets 400, as for the other tiers.
- An unknown provider gets 404.

**The LIST 404 on artifacts.** The body is `{"error": "Match not found"}`, the same body as the existing tier-mismatch 404. The handler returns it after parameter validation and before it reads `matches.json`. It is the same for a public match, a private match, an unknown match, an unknown artifact key and an unknown provider.

### 5.3 Token classification

`validate_token` encodes the presented token and each configured token with `str.encode("utf-8", "surrogatepass")` and compares the bytes with `hmac.compare_digest`. That encoding accepts every Python string, including a lone surrogate, and gives distinct bytes for distinct strings. So neither the encoding nor the comparison can raise (D8). It runs every comparison on every request, without a short circuit. A disabled tier (§5.4) has no token and matches nothing. Then it classifies:

| The presented token matches | Result |
|-----------------------------|--------|
| the public token (whatever else it matches) | `PUBLIC` |
| the owner token and the LIST token, not the public token | `PUBLIC` |
| the owner token only | `OWNER` |
| the LIST token only | `LIST` |
| none | 401 `Invalid token` |

A presented token that matches two or more tokens logs `token_collision` (warning). That covers the LIST-equals-PUBLIC, OWNER-equals-PUBLIC and OWNER-equals-LIST cases. The log line names no token value and no tier token.

### 5.4 Fetching tokens and the unset guard

- A new env var, `LIST_TOKEN_PARAM`, names the LIST SSM parameter. A new fetcher, `_get_list_token() -> str | None`, reads it with `WithDecryption=True` and caches the result per warm container (`functools.cache`).
- `_get_owner_token()` changes its return type to `str | None` and applies the same guard (D6).
- **Unset guard.** A fetched value is **unusable** when one of these is true:
  - it equals the Terraform placeholder `placeholder-set-via-cli-after-first-apply`;
  - it has fewer than 32 characters;
  - the whole value does not match the RFC 6750 `b64token` syntax `[A-Za-z0-9\-._~+/]+=*`. Use `re.fullmatch`, not `re.match` with `$`, because `$` also matches before a final line break. A value with a space, a line break (also a trailing one), a smart quote or any other non-ASCII character fails here. Such a value cannot be sent in a bearer header anyway.
- For an unusable value, the fetcher returns `None` and logs once per container: `list_token_unset` or `owner_token_unset` (warning). `None` disables that tier: no presented token matches it. The guard closes the window between a `terraform apply` that creates a parameter and the owner's `put-parameter`, because the placeholder is public.
- **LIST isolation.** If the LIST fetch raises (SSM error, missing env var, access denied), `validate_token` logs `list_token_fetch_failed` (error) and treats the LIST tier as disabled for that request. PUBLIC and OWNER classification goes on as normal. `functools.cache` does not cache an exception, so the next request tries the fetch again.
  - Cost of a persistent LIST fetch failure: one extra SSM `GetParameter` call and one error log on every authed request of every tier, until the parameter or the IAM policy is fixed. The API Gateway throttle (rate 10, burst 50, `terraform/modules/api/main.tf`) bounds it. The kill switch (§6) does not help here, because the fetch fails before the guard runs. Fix the parameter or the IAM policy.
- **Owner fetch failure** keeps its current rule: 500 to every request.
- A cached unusable value stays cached until the container recycles. The rollout therefore bumps `LAST_ROTATION` after `put-parameter` (§6).

### 5.5 Infrastructure (Terraform)

- `terraform/environments/dev/main.tf`: a new `aws_ssm_parameter.api_token_list`, the same shape as `api_token_owner`: name `/${var.project_name}/api_token_list`, type `SecureString`, `key_id = module.storage.kms_key_arn`, value `placeholder-set-via-cli-after-first-apply`, `lifecycle { ignore_changes = [value] }`. Pass its ARN and name to the functions module.
- `terraform/modules/functions`: new variables `list_token_param_arn` and `list_token_param_name`. The Lambda role's `ssm:GetParameter` statement covers both parameter ARNs. The five authed Lambdas (`list_providers`, `list_matches`, `get_artifact`, `list_players`, `get_player`) get `LIST_TOKEN_PARAM`. `health` does not, and keeps its minimal IAM.
- The `last_rotation` variable descriptions say that the marker clears both cached tokens.
- `terraform/environments/dev/outputs.tf`: `list_token_param_arn` and `list_token_param_name`, like the owner outputs.
- No change to the GitHub OIDC role. Its SSM statement already covers `parameter/${var.project_name}/*`.
- No change to the workflows.

### 5.6 Logging

New log messages, all without token values: `token_collision` (warning), `list_token_unset` and `owner_token_unset` (warning, once per container), `list_token_fetch_failed` (error, with the exception), and `list_tier_artifact_denied` (info, with `handler`) when a LIST token requests an artifact. The last one lets the operator see whether the Space ever tries to fetch an artifact. No alarm watches these logs (D7).

### 5.7 Live verify script

`scripts/verify_list_token_tier.py` runs against the live API.

- **Tokens.** It reads the owner token from `PINING_FOR_THE_DATA_TOKEN`. It reads the LIST token from `PINING_LIST_TOKEN`, or, with `--list-token-from-ssm`, from the SSM parameter through boto3 (`WithDecryption=True`, the caller's AWS profile). The SSM option exists because a variable that the owner sets after this session started does not reach this session's shell. It uses the documented public token.
- **Output.** Counts and PASS or FAIL per check. Never a token, a private id or a private name. Exit code 0 means every check passed.
- **Modes.**
  - `--snapshot PATH` runs before the merge. It writes the SHA-256 of each PUBLIC and OWNER response body of `/providers`, `/{provider}/matches` and `/{provider}/players` for every provider, and the status codes of the check 4 control requests, to `PATH`. The file holds hashes and status codes only. Keep it outside the repo.
  - `--before-token` runs checks 1, 2 and 8. It is for the time between the merge and the owner's `put-parameter`.
  - The full mode runs every check.
- **Checks.**
  1. The placeholder string presented as a token gets 401.
  2. PUBLIC `/matches` has no private entry, for every provider.
  3. For every provider, the LIST `/matches` body equals the OWNER body, with no filter and with each of `updatedSince`, `dateFrom` and `dateTo` (fixed, valid values). At least one private entry exists over all providers, or the check fails, because it would prove nothing.
  4. For every provider: every artifact key of one public entry and of one private entry gets 404 with the LIST token. A provider without public entries, or without private entries, is reported and skipped for that part. The script never follows a redirect, and a 302 is a failure. As a control, the same public artifact gets 302 with the public token, and the same private artifact gets 302 with the owner token. The script does not follow those redirects either.
  5. For every provider, LIST `/players` equals PUBLIC `/players`.
  6. For one private player id (in the OWNER list, not in the PUBLIC list), LIST `GET /players/{id}` gets 404. For one public player id, the LIST response equals the PUBLIC response. A provider without private players is reported and skipped for the private part.
  7. LIST `/providers` equals PUBLIC `/providers`.
  8. With `--compare PATH`: every PUBLIC and OWNER hash and control status equals the snapshot (G4).
- Run the script while no loader writes to the bucket, from the snapshot to the last full run. A write in that time gives a false FAIL on checks 3 or 8.
- The script reuses the rate limit (8 requests per second), the retry on 429 and 5xx only, and the no-redirect handling of `scripts/verify_match_index_fields.py`. The plan decides whether it imports them or moves them to a shared module. A 404 is never retried.

## 6. Rollout and owner gates

Do these steps in this order. Every step marked **gate** waits for the owner's explicit go.

1. **Gate:** A/B spec review, then the owner approves the spec, including the author's calls in §11.
2. **Gate:** A/B plan review, then owner approval.
3. Build on `feat/list-token-tier`, run the full local gate and `final-review`, then the A/B implementation reviews.
4. **Gate:** the owner's go names the commit, the push and the PR. One message can give all three, as in the previous cycle. CI posts the Terraform plan. The author reads it and confirms it only adds the parameter, changes the IAM policy, adds `LIST_TOKEN_PARAM` to five Lambdas, and changes the code hash of the Lambdas.
5. **Gate:** the owner approves the admin merge. Directly before the merge, run `verify_list_token_tier.py --snapshot PATH`, with `PATH` outside the repo. The merge runs `terraform apply` in CI, which is the live change. LIST is now deployed but disabled, because its parameter holds the placeholder.
6. Run `verify_list_token_tier.py --before-token --compare PATH`. It must pass. This also proves that the owner guard did not disable the owner token.
7. **Owner step: set the LIST value.** The owner runs this in a Bash shell (it is safe to run through `!` in a Claude session):

   ```bash
   key_id="$(MSYS_NO_PATHCONV=1 aws ssm describe-parameters --parameter-filters Key=Name,Values=/pining-for-the-data/api_token_list --query 'Parameters[0].KeyId' --output text)"
   MSYS_NO_PATHCONV=1 aws ssm put-parameter --name /pining-for-the-data/api_token_list --type SecureString \
     --key-id "$key_id" --overwrite \
     --value "$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
   ```

   `MSYS_NO_PATHCONV=1` stops Git Bash on Windows from converting the parameter name into a Windows path. Other shells ignore it. The value never appears on the terminal or in the shell history. `put-parameter` prints only the version and tier. The value is visible in the local process list while the `aws` call runs. That is the remaining risk, and it is local to the owner's machine. `--key-id` keeps the project KMS key. Then `aws ssm describe-parameters` must show that key and a version one higher than before the command (2 for the first LIST value). The author never sees the value.
8. **Gate: clear the cached placeholder.** From a `main` checkout at the merge commit:

   ```bash
   ts="$(date -u +%Y%m%dT%H%M%SZ)"
   gh variable set LAST_ROTATION --body "$ts"
   terraform -chdir=terraform/environments/dev apply -var="last_rotation=$ts"
   ```

   Both get the same value, so the next CI apply makes no change. This is the procedure of the CI/CD hardening spec §3.3. The CI apply cannot do it alone, because it has no manual trigger. The owner runs it, or the author runs it after the owner's go.
9. Run `verify_list_token_tier.py --list-token-from-ssm --compare PATH` in full mode. It must pass.
10. **Gate:** tag `v0.10.0` and create the GitHub Release.
11. The owner gives the token to the lakehouse Space (D5). The owner reads the value in their own terminal, not through a Claude session.

**Rotation of either token** uses steps 7 and 8, with the parameter name changed. `docs/api-reference.md`, `AGENTS.md` and `terraform/docs/setup.md` get this procedure, including the GitHub variable.

**Kill switch for LIST.** Set the LIST parameter back to the placeholder (`put-parameter` as in step 7, with `--value placeholder-set-via-cli-after-first-apply`), then run step 8. The guard disables the tier. PUBLIC and OWNER are not affected.

**Rollback.** Revert the commit through a PR. The CI apply removes the LIST parameter and the env vars, and restores the previous owner-token code. PUBLIC and OWNER behaviour returns to the 0.9.0 behaviour.

## 7. Tests

### 7.1 Unit tests (`src/tests/test_lambda_handlers.py` or a new `test_list_tier.py`)

The autouse fixture stubs `_get_list_token` like `_get_owner_token`, with invented values of at least 32 characters in the `b64token` syntax. All token values in tests are invented.

**Classification**
- The LIST token gives `LIST`, with both header spellings (`authorization` and `Authorization`).
- LIST equals PUBLIC: that token gives `PUBLIC` and logs `token_collision`.
- LIST equals OWNER: that token gives `PUBLIC` and logs `token_collision`.
- OWNER equals PUBLIC (the existing test): that token gives `PUBLIC`, and the test now also asserts the `token_collision` log.
- A presented non-ASCII token gets 401, with all three tiers configured (D8). A presented token with a lone surrogate (`"\ud800"` in Python source) also gets 401 and raises nothing (B-SPEC-10).

**Unset guard and isolation** (through the real fetchers with a stubbed SSM client)
- LIST value = the placeholder: LIST is disabled, the placeholder presented as a token gets 401, and `list_token_unset` is logged.
- LIST value with 31 characters: disabled. With 32 characters: enabled.
- LIST value with a non-ASCII character, LIST value with a space, and LIST value with a trailing `\n`: disabled. The public token gives `PUBLIC` and the owner token gives `OWNER` (LTT-SPEC-01, LTT-SPEC-09).
- `list_token_unset` is logged once for two requests in the same container.
- The LIST fetch raises: the LIST token gets 401, the public token gets `PUBLIC`, the owner token gets `OWNER`, and `list_token_fetch_failed` is logged.
- Retry: the LIST fetch raises on the first request and succeeds on the second. The second request calls the fetcher again and the LIST token gets `LIST`.
- `LIST_TOKEN_PARAM` is not set: same result as a fetch failure.
- Owner value = the placeholder, with 31 characters, with a non-ASCII character, or with a trailing `\n`: OWNER is disabled, the owner token holder gets 401, the public token gives `PUBLIC`, and `owner_token_unset` is logged (D6).
- The owner fetch raises: 500 for every token, as today.
- No log record contains a token value (checked with `caplog` over every case above).

**Every endpoint, every tier** (a parametrized matrix over `Tier` and the six endpoints, with a mocked S3 that holds public and private entries and players):
- `list_providers`: 200 and the same body for all three tiers.
- `list_matches`: the LIST body equals the OWNER body, with no filter and with each of `updatedSince`, `dateFrom`, `dateTo`. The PUBLIC body has no private entry.
- `get_artifact`: LIST gets 404 with `{"error": "Match not found"}` for a public match, a private match, an unknown match, an unknown artifact key and an unknown provider. The mock records no `get_object` and no `generate_presigned_url` call. `list_tier_artifact_denied` is logged with `handler`. LIST with an invalid path parameter gets 400. PUBLIC and OWNER keep their current results.
- `list_players`: the LIST body equals the PUBLIC body, and the mock records no read of `{provider}/_private/players.json`.
- `get_player`: LIST gets 404 for a private-only id, the public record for a public id, and the public record for an id that exists in both indexes. No read of the private index.
- `health`: 200 without a token.

**Guards**
- The policy dict's key set equals `set(Tier)`. A tier that is missing from the dict gets the all-`False` policy (the test removes one key with `monkeypatch` and checks the lookup).
- The handler modules do not refer to the name `Tier`, and no comparison in a handler module has the variable that holds the result of `validate_token` as an operand. The test parses the handler sources with `ast`. Only `shared.py` refers to the tier values.
- The placeholder string of both parameters in `terraform/environments/dev/main.tf` equals the string that the unset guard rejects. The test reads the file as text.
- `terraform/modules/functions/main.tf` has exactly five `aws_lambda_function` blocks that set `OWNER_TOKEN_PARAM`, and each of them also sets `LIST_TOKEN_PARAM`. The test reads the file as text and asserts the count of 5.

### 7.2 Verify script tests (`src/tests/test_verify_list_token_tier.py`)

With a stubbed HTTP layer and invented data: each check passes on correct responses and fails on the wrong one, for example a LIST 302, a LIST `/matches` body that differs from OWNER with one filter, no private entry anywhere, or a hash that differs from the snapshot. A 404 is not retried. `--list-token-from-ssm` reads the parameter named in the script through a stubbed SSM client. `--before-token` runs only checks 1, 2 and 8. `--snapshot` writes hashes and status codes only. Output and the snapshot file never contain a token string or a private id (the test captures stdout and reads the file).

### 7.3 Gates

Full local gate before the commit: `ruff check`, `ruff format --check`, `pyright`, the full pytest suite, and `terraform fmt -check -recursive` plus `terraform validate` if Terraform is installed locally. CI runs the Terraform checks in any case.

## 8. Documentation and release

- `docs/api-reference.md`: the Authentication section describes three tiers (table, byte comparison, classification, collision rule, unset guard for both tokens, uniform 404) and the rotation procedure of §6, including the GitHub variable. Each endpoint section states LIST behaviour. The "Tier-blind" text of List Providers names all three tiers.
- `docs/decisions/0014-list-only-token-tier.md`: the decision, the policy table, the unset guard on both tokens, the byte comparison, the isolation rule, and the consequences. One consequence is that a compromised Space can read private match metadata (names, dates, artifact names, hashes) but no artifact bytes. Add it to `docs/decisions/README.md`. ADR 0001 gets a "See also" line that points to ADR 0014 for the guard on the owner token.
- `AGENTS.md`: the "two-tier auth" section describes three tiers and the rotation with the GitHub variable. The file must stay inside the `test_agents_md_budget.py` limits, and the `agents_md_invariants.json` anchors must still match.
- `README.md` (the "Two-tier auth" paragraph), `ARCHITECTURE.md` (status line, the ASCII diagram label, §3.5, the AWS resource list), `SECURITY.md` (scope and the guard), `terraform/docs/setup.md` (the owner step uses the §6 step 7 command, because its current example value has fewer than 32 characters and the guard would reject it; a new optional step sets the LIST token; rotation with the GitHub variable).
- `docs/c4/architecture.dsl` and `architecture.html`: the `validate_token` and SSM descriptions, a `_get_list_token` component, and the operator's out-of-band step for the LIST token. Follow the c4 rules file for the render.
- `CHANGELOG.md`: a `[0.10.0]` section and compare link. `pyproject.toml` and `uv.lock`: version `0.10.0` in the cycle commit.

## 9. What the lakehouse gets

This section records the contract. The owner relays it (D5).

- A LIST token value, delivered by the owner.
- `GET /v1/{provider}/matches` with that token returns the same body as the owner token, including the `artifacts`, `artifact_meta`, competition and season fields of private entries.
- Every artifact request with that token gets 404 `{"error": "Match not found"}`.
- Player endpoints with that token behave as with the public token. The lakehouse text "sees private entries on the list endpoints, like the owner tier" (lakehouse §7.2) is therefore inexact for `/players` after D3. Nothing breaks, because the lakehouse never calls `/players`.
- Rotation follows the owner-token rules: no dual validity, so the consumer retries 401 with backoff.

## 10. Risks

- **R1. Terraform ordering at apply time.** The new Lambda code can start before the IAM policy allows the new parameter. The isolation rule (§5.4) means only the LIST tier fails during that window, and the tier is disabled at that time anyway (placeholder).
- **R2. A cached unusable value.** Covered by the rollout's `LAST_ROTATION` step (§6 step 8).
- **R3. A token value leaks into a log.** Covered by the `caplog` test and the review.
- **R4. Exposure of private match metadata if the Space is compromised.** Accepted by the owner (lakehouse §29.6). Recorded in ADR 0014. Use the kill switch or rotate the token.
- **R5. The owner guard disables the live owner token.** The live owner token passes the guard (§2, read 2026-10-27). §6 step 6 checks OWNER against the snapshot directly after the merge. If the check fails, revert (§6 Rollback).
- **R6. A persistent LIST fetch failure.** Costs one SSM call and one error log per authed request (§5.4). Bounded by the API throttle.

## 11. Author's calls

The owner approves or overrides each of these at the spec gate.

1. `/providers` and `/health` do not change.
2. The SSM name is `/pining-for-the-data/api_token_list`.
3. Rotation of either token: `put-parameter`, then the GitHub variable and a local apply with the same `LAST_ROTATION` value (§6 steps 7 and 8).
4. The unset guard rejects the placeholder, any value shorter than 32 characters, and any value outside the RFC 6750 `b64token` syntax.
5. A LIST fetch failure disables only the LIST tier. It does not give 500 to every request. The cost is in §5.4.
6. The LIST artifact 404 uses the existing body `{"error": "Match not found"}` and comes before any S3 read.
7. `validate_token` runs every comparison without a short circuit and logs `token_collision`.
8. `list_tier_artifact_denied` is logged at info level.
9. The live verify script samples one public and one private entry per provider for the artifact checks, not every entry.
10. Version `0.10.0` in the cycle commit, as in the previous cycle.
11. The verify script can read the LIST token from SSM (`--list-token-from-ssm`), so the value never passes through chat or a shell variable that the author sets.
12. The verify script proves G4 live through a snapshot taken directly before the merge (`--snapshot`, `--compare`).
13. A missing tier in the policy dict fails closed with every capability `False`.

## 12. Left to the builder

- The names of the policy types and functions and of the fetchers.
- Whether the new unit tests go into `test_lambda_handlers.py` or a new file.
- How the verify script shares the rate limit, retry and no-redirect code with `verify_match_index_fields.py`.
- The fixed filter values of verify check 3.

## 13. Review dispositions

### Round 1 (A: `2026-10-27-list-token-tier-spec-A.md`, B: `2026-10-27-list-token-tier-spec-B.md`, both REQUEST CHANGES, no BLOCKING)

A and B do not disagree. Four findings overlap: LTT-SPEC-07 = B-SPEC-01, LTT-SPEC-03 = B-SPEC-04, LTT-SPEC-06 = B-SPEC-02, and part of LTT-SPEC-02 = B-SPEC-06.

| Finding | Disposition |
|---------|-------------|
| LTT-SPEC-01 (SHOULD FIX) non-ASCII value breaks PUBLIC and OWNER | Fixed. Owner decision D8: byte comparison (§5.3). The guard also rejects a value outside `b64token` (§5.4). Tests named in §7.1. |
| LTT-SPEC-02 (SHOULD FIX) four behaviours without a named test | Fixed. §7.1 names a test for the collision logs, `list_token_unset` once per container, `list_tier_artifact_denied` with `handler`, and the retry after a fetch failure. |
| LTT-SPEC-03 / B-SPEC-04 (CONSIDER) AST guard misses string comparisons | Fixed. Handlers do not refer to `Tier`, and no comparison has the tier variable as an operand (§5.1, §7.1). |
| LTT-SPEC-04 (CONSIDER) completeness guard is vacuous | Fixed. A policy dict with a fail-closed lookup, and a test that its key set equals `set(Tier)` (§5.1, §7.1). |
| LTT-SPEC-05 (CONSIDER) Terraform text guard can pass with zero blocks | Fixed. The test asserts exactly five blocks (§7.1). |
| LTT-SPEC-06 / B-SPEC-02 (CONSIDER / SHOULD FIX) G7 overclaims | Fixed. G7 names what the live script proves and what the unit tests prove. Check 3 gained the filters. A snapshot comparison (check 8) proves G4 for the list endpoints live. |
| LTT-SPEC-07 / B-SPEC-01 (CONSIDER / SHOULD FIX) wrong `LAST_ROTATION` fact | Fixed. §2 states the CI source and the live value. §6 step 8, the kill switch, the rotation text and author's call 3 use the GitHub variable and a local apply with the same value, as in the CI/CD hardening spec §3.3. |
| LTT-SPEC-08 (CONSIDER) step 7 prints the token | Fixed. §6 step 7 generates and stores the value without printing it. The remaining risk (the local process list) is stated. The sentence fragment is gone. The step also keeps the KMS key with `--key-id`. |
| B-SPEC-03 (SHOULD FIX) non-goals not in the owner record | Fixed. Owner decisions D6 (guard on the owner token, now in scope) and D7 (the other non-goals stay out). |
| B-SPEC-05 (CONSIDER) cost of a persistent LIST fetch failure | Fixed. The cost is stated in §5.4 and R6. |
| B-SPEC-06 (CONSIDER) collision log not asserted | Fixed with LTT-SPEC-02. |
| B-SPEC-07 (CONSIDER) scope of the commit approval | Fixed. §6 step 4: the owner's go names the commit, the push and the PR. |
| B-SPEC-08 (CONSIDER) no unknown-provider artifact test | Fixed. §7.1 `get_artifact` includes an unknown provider. |
| B-SPEC-09 (CONSIDER) lakehouse §7.2 wording | Fixed. §9 records the inexact wording for the owner's relay. |
| A and B "could not verify": the lakehouse references | The header now names the lakehouse documents by repository path. |
| A and B "could not verify": owner decisions | D1–D8 recorded. The owner confirms them, and the §11 calls, at the spec gate. |

### Round 2 (A: `2026-10-27-list-token-tier-spec-r2-A.md`, B: `2026-10-27-list-token-tier-spec-r2-B.md`, both APPROVE)

A and B do not disagree. Every round-1 finding is resolved. LTT-SPEC-10 and B-SPEC-11 are the same finding.

| Finding | Disposition |
|---------|-------------|
| LTT-SPEC-09 (CONSIDER) `$` matches before a final line break | Fixed. §5.4 requires `re.fullmatch` on the whole value. §7.1 names a trailing-newline case for both tokens. |
| LTT-SPEC-10 / B-SPEC-11 (CONSIDER) "version 2" is wrong for later uses of step 7 | Fixed. §6 step 7 says a version one higher than before the command. |
| B-SPEC-10 (CONSIDER) the UTF-8 encode can raise on a lone surrogate | Fixed. §5.3 encodes with `surrogatepass`, which accepts every Python string. §7.1 names a lone-surrogate case. |
| B-SPEC-12 (CONSIDER) G4 and D8 say "the one" change; D6 is a second | Fixed. G4 and D8 name both approved changes. |
| Author fix after round 2 | §6 step 7 prefixes both `aws` commands with `MSYS_NO_PATHCONV=1`. Without it, Git Bash on Windows converts `/pining-for-the-data/api_token_list` into a Windows path and the commands fail. |
