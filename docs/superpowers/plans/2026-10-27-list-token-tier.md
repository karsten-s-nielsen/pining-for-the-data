# LIST Token Tier — Implementation Plan

> **For agentic workers:** execute inline with superpowers:executing-plans. The owner's rules forbid subagents, worktrees and per-task commits. All work stays on branch `feat/list-token-tier`. There is exactly **one** commit, at Task 8, after the owner's explicit approval. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A third bearer-token tier, `LIST`, sees every match entry on `/matches`, gets the uniform 404 on every artifact, and behaves like PUBLIC on the player endpoints. The unset guard and a byte comparison harden both SSM tokens.

**Architecture:** `shared.py` gets one tier-policy table, a guarded fetcher per SSM token, and a classification that compares UTF-8 bytes. Handlers ask the policy table and never compare a tier. Terraform adds the LIST SecureString and wires it into the five authed Lambdas. A live verify script proves the deployed behaviour against a snapshot taken before the merge.

**Tech Stack:** Python 3.12 (Lambda runtime: standard library + boto3 only), Terraform (AWS provider ~> 6.44), pytest, ruff, pyright (basic), uv.

**Spec:** `docs/superpowers/specs/2026-10-27-list-token-tier-design.md` (revision 3, approved by reviewers A and B in round 2 and by the owner on 2026-10-27, including D1–D8 and the 13 author's calls). Read it with this plan. Section references (§) point to the spec.

## Global Constraints

- Tier values: `Tier.PUBLIC = "public"`, `Tier.LIST = "list"`, `Tier.OWNER = "owner"`.
- Placeholder: `placeholder-set-via-cli-after-first-apply`. Minimum usable length: 32 characters. Syntax: `re.fullmatch(r"[A-Za-z0-9\-._~+/]+=*", value)` (RFC 6750 `b64token`). Never `re.match` with `$`.
- Encoding for comparison: `value.encode("utf-8", "surrogatepass")`, then `hmac.compare_digest` on bytes.
- SSM: `/pining-for-the-data/api_token_list`; env var `LIST_TOKEN_PARAM`; Terraform variables `list_token_param_arn`, `list_token_param_name`.
- LIST artifact response: status 404, body `{"error": "Match not found"}`, before any S3 call.
- Log messages (exact): `token_collision` (warning), `list_token_unset` and `owner_token_unset` (warning), `list_token_fetch_failed` (error), `list_tier_artifact_denied` (info, `extra={"handler": "get_artifact"}`). The existing `owner_token_fetch_failed` stays.
- No log line, test fixture, doc or script output contains a token value, a private match id or a private player id. All token values in tests are invented. Every token-like string literal in tests and scripts carries `# pragma: allowlist secret`.
- Verify-script env vars: `PINING_FOR_THE_DATA_TOKEN` (owner), `PINING_LIST_TOKEN` (LIST). The public token is `test-token-pining-for-the-data`.
- Run tools with `uv run --no-sync` (Windows). In Git Bash, prefix `aws` commands that take a `/pining-for-the-data/...` argument with `MSYS_NO_PATHCONV=1`.
- No commit, push or PR without the owner's explicit yes (Task 8). The merge is a live `terraform apply` (CI) and needs the owner's explicit go (Task 9). The rotation apply and the tag need their own go.

## Review Focus

These five inputs are not in the spec's §7 test list. Each has its own test in the task that owns the code.

1. An empty bearer (`"Bearer "`) while a tier is disabled. A disabled token is `None`, and it must never turn into `""` and match. Expected: 401. → Task 2, `test_empty_bearer_never_matches_disabled_tier`.
2. A token with surrounding whitespace (`"Bearer <token> "`, two spaces after `Bearer`). There is no trimming today. Expected: 401 for every tier (G4). → Task 2, `test_whitespace_padded_token_gets_401`.
3. `/matches` for an unknown provider with the LIST token. Expected: the same 404 `{"error": "Provider not found"}` as OWNER. → Task 3, `test_list_matches_unknown_provider_same_for_list_and_owner`.
4. `--compare` with a snapshot whose provider set differs from the live set (a provider added or removed). Expected: a FAIL that names the missing and extra keys, not a `KeyError`. → Task 5, `test_compare_snapshot_key_mismatch_reported`.
5. `PINING_LIST_TOKEN` set to an empty string. Expected: the script stops with "LIST token required" and exit 2, and sends no request. → Task 5, `test_empty_list_token_rejected`.

---

### Task 1: Tier policy table

**Files:**
- Modify: `terraform/modules/functions/src/shared.py` (the `Tier` enum and a new policy section)
- Create: `src/tests/test_list_tier.py` (new; must pass `pyright`, so set `shared` stubs with `monkeypatch.setattr`, never by direct assignment)

**Interfaces:**
- Produces:
  - `class Tier(StrEnum): PUBLIC = "public"; LIST = "list"; OWNER = "owner"`
  - `@dataclass(frozen=True) class TierPolicy: lists_private_matches: bool; serves_artifacts: bool; reads_private_content: bool`
  - `_NO_ACCESS = TierPolicy(False, False, False)`
  - `_TIER_POLICIES: dict[Tier, TierPolicy]` = PUBLIC `(False, True, False)`, LIST `(True, False, False)`, OWNER `(True, True, True)` (§5.1)
  - `policy_for(tier: Tier) -> TierPolicy`: `_TIER_POLICIES.get(tier, _NO_ACCESS)` (fails closed)

- [ ] **Step 1: Write the failing tests** in `test_list_tier.py` (the module header inserts the Lambda `src` directory on `sys.path` as `test_lambda_handlers.py` does):
  - `test_tier_values`: `[t.value for t in Tier] == ["public", "list", "owner"]`.
  - `test_policy_table_matches_spec`: `policy_for(Tier.PUBLIC) == TierPolicy(False, True, False)`; `policy_for(Tier.LIST) == TierPolicy(True, False, False)`; `policy_for(Tier.OWNER) == TierPolicy(True, True, True)`.
  - `test_policy_keys_cover_every_tier`: `set(shared._TIER_POLICIES) == set(Tier)`.
  - `test_missing_tier_fails_closed`: `monkeypatch.delitem(shared._TIER_POLICIES, Tier.LIST)`; then `policy_for(Tier.LIST) == TierPolicy(False, False, False)`.
- [ ] **Step 2:** Run `uv run --no-sync pytest src/tests/test_list_tier.py -v`. Expected: FAIL (`ImportError: cannot import name 'TierPolicy'`).
- [ ] **Step 3:** Implement the Interfaces above.
- [ ] **Step 4:** Run the same command, then `uv run --no-sync pytest src/tests/test_lambda_handlers.py -q`. Expected: PASS for both. `TestTierEnum` still passes.

### Task 2: Guarded token fetchers and byte classification

**Files:**
- Modify: `terraform/modules/functions/src/shared.py` (`_get_owner_token`, `validate_token`, new fetcher and guard)
- Modify: `src/tests/test_lambda_handlers.py`:
  - The owner stub value becomes `"owner-token-invented-0123456789abcdefXYZ"` (spec §7.1: at least 32 characters, `b64token` syntax). Replace all 18 `own-tok` occurrences, through a module constant `OWN_TOK` with `# pragma: allowlist secret`.
  - The autouse fixture also calls `shared._get_list_token.cache_clear()` and stubs `_get_list_token` with `lambda: LIST_TOK`, where `LIST_TOK = "list-token-invented-0123456789abcdefXYZ"  # pragma: allowlist secret`.
  - Add `os.environ.setdefault("LIST_TOKEN_PARAM", "/test/api_token_list")`.
  - Extend `test_same_public_and_owner_classifies_as_public` with the `token_collision` log assertion.
- Modify: `src/tests/test_match_index_contract.py` (both `own-tok` occurrences become the same invented 40-character owner value; also stub `_get_list_token` with `lambda: None`)
- Test: `src/tests/test_list_tier.py`

**Interfaces:**
- Consumes: Task 1 `Tier`.
- Produces (all in `shared.py`):
  - `_PLACEHOLDER_TOKEN = "placeholder-set-via-cli-after-first-apply"`, `_MIN_TOKEN_LENGTH = 32`, `_B64TOKEN_RE = re.compile(r"[A-Za-z0-9\-._~+/]+=*")`
  - `_usable_token(value: str) -> bool`: not the placeholder, `len >= 32`, and `_B64TOKEN_RE.fullmatch(value)`.
  - `_fetch_ssm_token(env_var: str) -> str`: reads `os.environ[env_var]`, then `get_parameter(Name=..., WithDecryption=True)`. Every failure raises.
  - `@functools.cache _get_owner_token() -> str | None` (env `OWNER_TOKEN_PARAM`) and `@functools.cache _get_list_token() -> str | None` (env `LIST_TOKEN_PARAM`). An unusable value logs `owner_token_unset` or `list_token_unset` once (the cache makes it once) and returns `None`.
  - `_token_bytes(value: str) -> bytes`: `value.encode("utf-8", "surrogatepass")`.
  - `validate_token(event: dict) -> Tier | dict`: the order is `API_TOKEN` check (500), header check (401), owner fetch (an exception logs `owner_token_fetch_failed` and gives 500), LIST fetch (an exception logs `list_token_fetch_failed` with the exception and leaves LIST disabled), then all comparisons, then the §5.3 table. Compute one boolean per configured token before any branch; a `None` token gives `False` without a comparison. Two or more matches log `token_collision`.

Test constants in `test_list_tier.py` (each line with `# pragma: allowlist secret`): `PUB = "pub-tok"`, `OWN = "owner-token-invented-0123456789abcdefXYZ"`, `LST = "list-token-invented-0123456789abcdefXYZ"`. A `_FakeSSM` class maps a parameter name to a value or an exception and counts calls. Fixtures: `stubbed` (stubs both fetchers with `OWN` and `LST`) and `real_fetchers` (sets both env vars, clears both caches, puts a `_FakeSSM` on `shared._ssm_client`).

- [ ] **Step 1: Write the failing tests** in `test_list_tier.py`:
  - Classification (`stubbed`): `test_list_token_gives_list[authorization|Authorization]`; `test_list_equals_public_gives_public_and_logs_collision`; `test_list_equals_owner_gives_public_and_logs_collision`; `test_non_ascii_presented_token_gets_401` (`"Bearer tést"`); `test_lone_surrogate_presented_token_gets_401` (`"Bearer \ud800abc"`, no exception).
  - Review Focus 1, `test_empty_bearer_never_matches_disabled_tier`: stub `_get_list_token` with `lambda: None`, present `"Bearer "`. Expected: 401.
  - Review Focus 2, `test_whitespace_padded_token_gets_401[public|list|owner]`: present `f"Bearer {tok} "` and `f"Bearer  {tok}"`. Expected: 401.
  - Guard (`real_fetchers`). **Every guard case presents the stored value itself** and asserts the result, so a guard that logs but still returns the value fails (LTT-PLAN-01):
    - `test_list_placeholder_disables_tier_and_logs_unset`: stored placeholder; presenting the placeholder gets 401; one `list_token_unset` record.
    - `test_list_length_boundary`: stored `"a" * 31`, presenting `"a" * 31` gets 401. Stored `"a" * 32`, presenting `"a" * 32` gives `LIST`.
    - `test_list_bad_syntax_disables_tier_others_unaffected[non-ascii|space|trailing-newline]`: stored values `"x" * 32 + "é"`, `"x" * 16 + " " + "x" * 16`, `"x" * 32 + "\n"`. Presenting the stored value gets 401. `PUB` gives `PUBLIC`, `OWN` gives `OWNER`.
    - `test_list_unset_logged_once_per_container`: two calls, one `list_token_unset` record, one SSM call.
  - Isolation (`real_fetchers`): `test_list_fetch_failure_isolated` (`_FakeSSM` raises for the LIST name: the LIST token gets 401, `PUB` gives `PUBLIC`, `OWN` gives `OWNER`, one `list_token_fetch_failed` record); `test_list_fetch_retried_after_failure` (raise once, then return `LST`: the second call gives `LIST`, and the SSM fake counts 2 LIST calls); `test_list_param_env_missing_isolated` (`monkeypatch.delenv("LIST_TOKEN_PARAM")`: same as a fetch failure).
  - Owner guard (`real_fetchers`): `test_owner_unusable_disables_owner[placeholder|31-chars|non-ascii|trailing-newline]`. The stored owner value is the case value. Presenting that stored value gets 401; this is the test for the spec anti-goal "the placeholder works as the owner token". `PUB` gives `PUBLIC`, `LST` gives `LIST`, and there is one `owner_token_unset` record. `test_owner_fetch_failure_gives_500`: every token gets 500.
  - `test_no_token_value_in_logs`: run the collision, guard, isolation and owner-guard cases under `caplog`. No `record.getMessage()`, `record.args` or `record.exc_text` contains `OWN`, `LST`, `PUB` or a guard test value.
- [ ] **Step 2:** Run `uv run --no-sync pytest src/tests/test_list_tier.py -v`. Expected: FAIL (no `_get_list_token`, no `Tier.LIST` result).
- [ ] **Step 3:** Implement the Interfaces above and the two test-file stub changes.
- [ ] **Step 4:** Run `uv run --no-sync pytest src/tests/test_list_tier.py src/tests/test_lambda_handlers.py src/tests/test_match_index_contract.py -v`. Expected: PASS.

### Task 3: Handlers use the policy table

**Files:**
- Modify: `terraform/modules/functions/src/list_matches.py`, `get_artifact.py`, `list_players.py`, `get_player.py` (import `policy_for`, not `Tier`; update the docstrings to name the three tiers)
- Test: `src/tests/test_list_tier.py`

**Interfaces:**
- Consumes: Task 1 `policy_for`; Task 2 `validate_token`.
- Produces: the §5.2 endpoint behaviour.
  - `list_matches`: filter to public entries when `not policy_for(tier).lists_private_matches`.
  - `get_artifact`: after the three path-parameter checks, when `not policy.serves_artifacts`, log `list_tier_artifact_denied` (info, `extra={"handler": "get_artifact"}`) and return 404 `{"error": "Match not found"}`. The private gate becomes `visibility == "private" and not policy.reads_private_content`.
  - `list_players`, `get_player`: read the private index only when `policy_for(tier).reads_private_content`.

A `s3` fixture in `test_list_tier.py` puts a `MagicMock` on `shared._s3_client` (with `monkeypatch.setattr`) that serves invented data: `providers.json` = `["synthp"]`; `synthp/matches.json` with `SYN-PUB-001` (public, date `2099-01-10`, `updated_at` `2099-01-10T00:00:00Z`) and `SYN-PRIV-001` (private, date `2099-02-10`, `updated_at` `2099-02-10T00:00:00Z`), each with `artifacts {"tracking": "tracking.jsonl"}`; `synthp/players.json` with `SYN-PL-PUB` and `SYN-PL-BOTH`; `synthp/_private/players.json` with `SYN-PL-PRIV` and `SYN-PL-BOTH` (a different record). Any other key raises `NoSuchKey`, set on the mock as `_PlayersWiring` in `test_lambda_handlers.py` does. `generate_presigned_url` returns `"https://s3.example.com/presigned"`.

- [ ] **Step 1: Write the failing tests** (fixtures `stubbed` and `s3`):
  - `test_list_providers_same_for_every_tier[public|list|owner]`: 200 and the same body.
  - `test_list_matches_list_equals_owner[none|updatedSince|dateFrom|dateTo]`: query values `updatedSince=2099-01-15T00:00:00Z`, `dateFrom=2099-01-15`, `dateTo=2099-03-01`. Each value keeps `SYN-PRIV-001` in the OWNER result, so each case fails if LIST is filtered like PUBLIC (LTT-PLAN-03). The LIST `result["body"]` string equals the OWNER string.
  - `test_list_matches_public_has_no_private`.
  - Review Focus 3, `test_list_matches_unknown_provider_same_for_list_and_owner`: provider `nosuch`. LIST and OWNER both get 404 `{"error": "Provider not found"}`.
  - `test_get_artifact_list_gets_uniform_404_without_s3[public|private|unknown-match|unknown-artifact|unknown-provider]`: 404, body `{"error": "Match not found"}`, `get_object` and `generate_presigned_url` not called, and one `list_tier_artifact_denied` record with `record.handler == "get_artifact"`.
  - `test_get_artifact_list_invalid_param_gets_400`: `id="_bad"`. Expected: 400.
  - `test_get_artifact_public_and_owner_unchanged`: PUBLIC gets 302 on `SYN-PUB-001` and 404 on `SYN-PRIV-001`. OWNER gets 302 on both, with the keys `synthp/SYN-PUB-001/tracking.jsonl` and `synthp/_private/SYN-PRIV-001/tracking.jsonl`.
  - `test_list_players_list_equals_public_and_no_private_read`: no `get_object` call with a key that ends in `/_private/players.json`.
  - `test_get_player_list_behaves_like_public[SYN-PL-PRIV→404|SYN-PL-PUB→public|SYN-PL-BOTH→public record]`, and no private-index read.
  - `test_health_needs_no_token`: 200 with `{"headers": {}}`.
  - `test_handlers_never_compare_tier`: parse the six handler sources with `ast`. No `ast.Name` or `ast.Attribute` with the id `Tier`. Find the name bound by `... = validate_token(event)`. No `ast.Compare` node has an `ast.Name` operand with that id.
- [ ] **Step 2:** Run `uv run --no-sync pytest src/tests/test_list_tier.py -v`. Expected: FAIL (the LIST `/matches` body lacks the private entry, the LIST artifact gets 302, and the AST guard finds `Tier`).
- [ ] **Step 3:** Implement the Interfaces above.
- [ ] **Step 4:** Run `uv run --no-sync pytest src/tests/test_list_tier.py src/tests/test_lambda_handlers.py src/tests/test_match_index_contract.py -v`. Expected: PASS.

### Task 4: Terraform — the LIST parameter and its wiring

**Files:**
- Modify: `terraform/environments/dev/main.tf` (new `aws_ssm_parameter.api_token_list`; two new `module "functions"` arguments), `terraform/environments/dev/outputs.tf` (two outputs), `terraform/environments/dev/variables.tf` (`last_rotation` description: both tokens)
- Modify: `terraform/modules/functions/variables.tf` (two variables; `last_rotation` description), `terraform/modules/functions/main.tf` (the `ssm:GetParameter` `Resource` becomes `[var.owner_token_param_arn, var.list_token_param_arn]`; `LIST_TOKEN_PARAM = var.list_token_param_name` in the five authed Lambdas)
- Test: `src/tests/test_list_tier.py`

**Interfaces:**
- Consumes: Task 2 `_PLACEHOLDER_TOKEN`, `_usable_token`.
- Produces: the §5.5 infrastructure. The new parameter copies the shape of `api_token_owner` (`main.tf:27-37`), with the description `List-only-tier bearer token for the mock provider API`. Outputs: `list_token_param_arn` and `list_token_param_name`, with descriptions that mirror the owner outputs.

- [ ] **Step 1: Write the failing tests:**
  - `test_terraform_placeholders_are_rejected_by_guard`: read `terraform/environments/dev/main.tf` as text. For each of `api_token_owner` and `api_token_list`, extract the `value = "..."` of its `aws_ssm_parameter` block. Each equals `shared._PLACEHOLDER_TOKEN`, and `shared._usable_token(value) is False`.
  - `test_five_authed_lambdas_set_both_token_params`: split `terraform/modules/functions/main.tf` into `resource "aws_lambda_function"` blocks. Exactly 5 blocks contain `OWNER_TOKEN_PARAM`. Each of them contains `LIST_TOKEN_PARAM = var.list_token_param_name`. The `health` block contains neither.
  - `test_lambda_role_may_read_list_param`: the statement with `ssm:GetParameter` names `var.list_token_param_arn`.
- [ ] **Step 2:** Run `uv run --no-sync pytest src/tests/test_list_tier.py -k terraform -v` and `-k lambda_role`. Expected: FAIL.
- [ ] **Step 3:** Make the Terraform changes.
- [ ] **Step 4:** Run the same tests (expected: PASS), then `terraform fmt -check -recursive terraform` (expected: no output, exit 0), then `terraform -chdir=terraform/environments/dev init -backend=false -input=false` and `terraform -chdir=terraform/environments/dev validate` (expected: `Success! The configuration is valid.`). Do not run `plan` or `apply` here. CI posts the plan on the PR (Task 8).

### Task 5: Live verify script

**Files:**
- Modify: `scripts/_verify_http.py` (move `RateLimiter` and `with_retry` here from `verify_match_index_fields.py`, unchanged), `scripts/verify_match_index_fields.py` (import them from `_verify_http`; no other change)
- Create: `scripts/verify_list_token_tier.py`
- Modify: `pyproject.toml` (`"scripts/verify_list_token_tier.py" = ["S310"]` with the usual rationale comment)
- Test: `src/tests/test_verify_list_token_tier.py` (uses the `load_script` fixture); `src/tests/test_verify_match_index_fields.py` (existing; must stay green)

**Interfaces:**
- Consumes: `_verify_http.NoFollow`, `RateLimiter`, `with_retry`.
- Produces (in `verify_list_token_tier.py`):
  - Constants: `PUBLIC_TOKEN = "test-token-pining-for-the-data"`, `PLACEHOLDER = "placeholder-set-via-cli-after-first-apply"`, `LIST_PARAM = "/pining-for-the-data/api_token_list"`, `FILTERS = (("updatedSince", "2026-10-01T00:00:00Z"), ("dateFrom", "2022-01-01"), ("dateTo", "2023-01-01"))`.
  - `@dataclass(frozen=True) class Response: status: int; body: bytes; location: str | None`
  - `Fetch = Callable[[str, str], Response]` (path, token).
  - `make_fetch(api: str, *, limiter: RateLimiter | None = None, opener=None) -> Fetch`: no redirect follow (`NoFollow`); `with_retry` retries 429 and 5xx only; any other `HTTPError` (302, 401, 404) becomes a `Response`, without a retry.
  - Check functions, each `-> list[str]` (problem lines; empty means pass; a line names the provider and the check, never an id, a name or a token): `check_placeholder(fetch)`, `check_public_has_no_private(fetch, providers)`, `check_list_equals_owner(fetch, providers, list_token, owner_token)`, `check_artifacts(fetch, providers, list_token, owner_token)`, `check_players(fetch, providers, list_token)`, `check_player_records(fetch, providers, list_token, owner_token)`, `check_providers(fetch, list_token)`.
  - `sample_entries(matches: list[dict]) -> tuple[dict | None, dict | None]`: the first public and the first private entry in index order of the OWNER listing. Check 4 and the snapshot use the same sample.
  - `take_snapshot(fetch, providers, owner_token) -> dict`: `{"version": 1, "bodies": {"<tier> <path>": "<sha256>"}, "controls": {"<tier> artifact <provider>": <status>}}`. Keys hold tiers, paths without ids, and provider names only.
  - `compare_snapshot(current: dict, saved: dict) -> list[str]`: reports differing values and missing or extra keys by key name.
  - `run(args: argparse.Namespace, fetch: Fetch, owner_token: str, list_token: str | None) -> int`: `--snapshot PATH` writes the snapshot and returns. `--before-token` runs checks 1, 2 and 8. Otherwise it runs checks 1–7, and 8 when `--compare` is given. It prints one `check N <name>: PASS|FAIL` line per check, the problem lines, and a final `PASS` or `FAIL`. It returns 0 or 1.
  - `main() -> int`: arguments `--api` (required), `--owner-token` (default `$PINING_FOR_THE_DATA_TOKEN`), `--list-token-from-ssm`, `--snapshot PATH`, `--compare PATH`, `--before-token`. The LIST token comes from `PINING_LIST_TOKEN` or, with `--list-token-from-ssm`, from `boto3.client("ssm").get_parameter(Name=LIST_PARAM, WithDecryption=True)`. A missing or empty token that the mode needs gives `ap.error(...)` (exit 2) before any request.

- [ ] **Step 1: Write the failing tests** (invented data; a dict-backed fake `Fetch`; private ids such as `SYN-PRIV-001`):
  - `test_placeholder_check_passes_on_401_and_fails_on_200`.
  - `test_public_private_entry_fails`.
  - `test_list_equals_owner_passes`; `test_list_differs_with_one_filter_fails`; `test_no_private_entry_anywhere_fails`.
  - `test_artifacts_404_with_controls_pass`; `test_list_artifact_302_fails`; `test_control_not_302_fails`; `test_provider_without_private_entries_skipped_and_reported`.
  - `test_players_list_differs_fails`; `test_private_player_200_fails`; `test_public_player_mismatch_fails`; `test_provider_without_private_players_skipped_and_reported`.
  - `test_providers_mismatch_fails`.
  - `test_snapshot_round_trip_passes`; `test_snapshot_hash_differs_fails`; `test_snapshot_file_has_only_hashes_and_statuses` (no token string, no `SYN-` id).
  - Review Focus 4, `test_compare_snapshot_key_mismatch_reported`: the saved snapshot lacks one provider's keys and has one extra key. The problems name both keys, and no `KeyError` is raised.
  - `test_404_not_retried`: `make_fetch` with a fake opener that raises `HTTPError(404)` gives `Response(404, ...)`, and the opener was called once.
  - `test_list_token_from_ssm_reads_named_parameter`: a stubbed boto3 client records `Name == LIST_PARAM` and `WithDecryption is True`.
  - `test_before_token_runs_only_checks_1_2_8`.
  - Review Focus 5, `test_empty_list_token_rejected`: `PINING_LIST_TOKEN=""` in full mode. Expected: `SystemExit(2)`, the message "LIST token required", and no fetch call.
  - `test_output_never_contains_tokens_or_ids`: a full `run` with failures in every check. `capsys` output contains none of the token strings and no `SYN-` id.
- [ ] **Step 2:** Run `uv run --no-sync pytest src/tests/test_verify_list_token_tier.py -v`. Expected: FAIL (no module).
- [ ] **Step 3:** Move `RateLimiter` and `with_retry`, then implement the Interfaces above.
- [ ] **Step 4:** Run `uv run --no-sync pytest src/tests/test_verify_list_token_tier.py src/tests/test_verify_match_index_fields.py -v`. Expected: PASS.

### Task 6: Documentation, ADR 0014, version

**Files:**
- Modify: `docs/api-reference.md`, `AGENTS.md`, `README.md`, `ARCHITECTURE.md`, `SECURITY.md`, `terraform/docs/setup.md`, `docs/decisions/0001-owner-token-in-ssm-parameter-store.md`, `docs/decisions/README.md`, `docs/c4/architecture.dsl`, `docs/c4/architecture.html`, `CHANGELOG.md`, `pyproject.toml`, `uv.lock`
- Create: `docs/decisions/0014-list-only-token-tier.md`
- Test: `src/tests/test_agents_md_budget.py` (existing gate)

- [ ] **Step 1:** `api-reference.md`, as §8 states. The Authentication table gets a `LIST` row (source: SSM `/pining-for-the-data/api_token_list`; use: the lakehouse v2 Ops Space). Describe the byte comparison, the §5.3 table, the unset guard for both tokens, and the rotation procedure of spec §6 steps 7 and 8, including `gh variable set LAST_ROTATION` and `MSYS_NO_PATHCONV=1`. Each endpoint section gets one LIST sentence. "Tier-blind" names all three tiers.
- [ ] **Step 2:** ADR 0014 in the README format: context (lakehouse §29.6), decision (policy table, LIST semantics D3/D4), the unset guard on both tokens (D6), the byte comparison (D8), the isolation rule, consequences (R4 metadata exposure, the kill switch), alternatives (owner token in the Space; a cached list written by the Job, lakehouse §29.6 options i and ii). Add the README index row. ADR 0001 gets the line `**See also:** ADR 0014 — the unset guard now applies to this token.`
- [ ] **Step 3:** `AGENTS.md`: retitle the section "three-tier auth". Add one LIST bullet (SSM name, list-only, 404 on every artifact, PUBLIC on player endpoints). Change the `validate_token` bullet to name `PUBLIC`, `LIST` and `OWNER`, and the rotation bullet to the GitHub variable plus local apply. Keep all three anchor texts verbatim: "bearer token stored in SSM Parameter Store SecureString", "Tier mismatch returns uniform `404`", and "bump `LAST_ROTATION` env var on all 6 Lambdas" (the rotation bullet, `api-rotation`). Keep every symbol of those entries, including `last_rotation` and `401 retry`. Do not edit `agents_md_invariants.json`. Example rotation bullet: "Rotation: after `aws ssm put-parameter --overwrite`, set the GitHub variable `LAST_ROTATION` and bump `LAST_ROTATION` env var on all 6 Lambdas via `terraform apply -var=last_rotation=<the same value>`."
- [ ] **Step 4:** `README.md` (the "Two-tier auth" paragraph), `ARCHITECTURE.md` (status line, ASCII label, §3.5 with a `Tier.LIST` bullet and the guard, the SSM resource list), `SECURITY.md` (the LIST tier in the scope, the guard), `terraform/docs/setup.md` (the owner step uses the spec §6 step 7 command form with the owner parameter name; a new optional "Set Up the LIST Token" step; rotation with the GitHub variable; line 202 "two-tier auth contract" becomes "three-tier").
- [ ] **Step 5:** C4: in `architecture.dsl`, change "two-tier auth" to "three-tier auth" in the workspace description (line 1) and the `pining` system description (line 8), update the `validate_token` description (three tiers, byte comparison, guard), the `ssmParam` description (two SecureStrings), add a `_get_list_token` component next to `ownerTokenFetcher`, change the operator relationship to "Sets owner and LIST tokens (out-of-band)", and add the lakehouse Space relationship "Lists matches (LIST bearer token)". Render `architecture.html` as the c4 rules file says.
- [ ] **Step 6:** `CHANGELOG.md` `[0.10.0] - <date of Task 8>`: the LIST tier, the guard on both tokens, the byte comparison (a non-ASCII token now gets 401), the verify script, the rotation doc fix, the test count. Add the compare link. Set `version = "0.10.0"` in `pyproject.toml` and run `uv lock`. Expected: only the project version line changes in `uv.lock`.
- [ ] **Step 7:** Run `uv run --no-sync pytest src/tests/test_agents_md_budget.py -v`. Expected: PASS. Run `git grep -n "two-tier\|Two-tier"` outside `docs/superpowers/`, `CHANGELOG.md` and `src/tests/fixtures/`. Expected: no hit.

### Task 7: Quality gate, final review, implementation reviews

- [ ] **Step 1: Shift left.** Run `uv run --no-sync ruff check src scripts terraform/modules/functions/src`, `uv run --no-sync ruff format --check src scripts terraform/modules/functions/src`, `uv run --no-sync pyright`, `uv run --no-sync pytest`, `uv run --no-sync pip-audit`, and `uvx detect-secrets==1.5.0 scan` on each new or changed file by name. Expected: ruff clean (baseline at `7479d32`: `terraform/modules/functions/src` passes `ruff check`, 7 files formatted; CI does not lint that directory, so this gate does); `pyright` 0 errors and no warning other than the 3 known `huggingface_hub` warnings (cycle A baseline); pytest all PASS, with the count recorded; pip-audit no finding, run in an environment synced like CI (`uv sync --frozen --extra dev`; a stale venv can miss a package); detect-secrets: a scan with `--baseline .secrets.baseline` adds no entry (a scan without the baseline lists only the entries the baseline already holds). Report any failure in full.
- [ ] **Step 2:** Run the `final-review` skill. Fix what it finds, then run Step 1 again.
- [ ] **Step 3: Gate — owner.** Ask the owner to launch the A/B implementation reviews of the branch. Fix or answer every finding, and run Step 1 again after the fixes. Take any A/B disagreement to the owner.

### Task 8: The one commit, push and PR (owner gate)

- [ ] **Step 1: Gate — owner approval.** Show `git status`, `git diff --stat` and the full diff. The request names three actions: the commit (message below), the push of `feat/list-token-tier`, and `gh pr create`. Each happens only after an explicit yes that covers it.

```
feat: LIST list-only token tier and unset guard for SSM tokens

<body: the LIST tier semantics (D3, D4), the policy table, the guard on both tokens (D6), the byte comparison (D8), the Terraform parameter, the verify script, ADR 0014, 0.10.0>

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

- [ ] **Step 2:** Watch CI until it is green. Read the Terraform plan comment on the PR. Expected: 1 to add (`aws_ssm_parameter.api_token_list`); in-place updates to the Lambda role policy and the six Lambda functions (code hash; `LIST_TOKEN_PARAM` on five); nothing destroyed. Report the plan summary to the owner. Any other change stops the rollout.

### Task 9: Rollout (owner gates), verify, release

Follow spec §6 steps 5–11 in order.

- [ ] **Step 1:** Directly before the merge, run `uv run --no-sync python scripts/verify_list_token_tier.py --api "$API_URL" --snapshot "<scratchpad>/list-tier-snapshot.json"`. Expected: the file exists and holds hashes and status codes only.
- [ ] **Step 2: Gate — owner go for the admin merge.** After the merge, watch the `Terraform Apply` run until it is green.
- [ ] **Step 3:** Run the script with `--before-token --compare <snapshot>`. Expected: checks 1, 2 and 8 PASS, exit 0. A FAIL on check 8 for an OWNER key means the owner guard broke the owner tier: report it and propose the revert (spec §6 Rollback).
- [ ] **Step 4: Owner step.** Ask the owner to run the spec §6 step 7 commands. Then run `MSYS_NO_PATHCONV=1 aws ssm describe-parameters --parameter-filters Key=Name,Values=/pining-for-the-data/api_token_list` with `AWS_PROFILE=devops-agent`. Expected: the project KMS key and version 2. Never read or print the value.
- [ ] **Step 5: Gate — owner go for the rotation apply** (spec §6 step 8), from `main` at the merge commit. Expected: `gh variable get LAST_ROTATION` equals the new timestamp, and the apply plan changes only the `LAST_ROTATION` env var of the six Lambdas. If the plan shows any other change (for example `API_TOKEN`, when the local `terraform.tfvars` differs from the CI secret), answer `no` at the apply prompt and report it to the owner.
- [ ] **Step 6:** Run the script with `--list-token-from-ssm --compare <snapshot>` (full mode). Expected: checks 1–8 PASS, exit 0. Report the result in full.
- [ ] **Step 7: Gate — owner go for the tag and release.** Tag `v0.10.0` at this PR's merge commit, not at a later tip of `main` (B-PLAN-03), push the tag, and create the GitHub Release (release convention: CHANGELOG section text). Then tell the owner that the token can go to the lakehouse Space (D5).

---

## Self-review record

- Spec coverage: §5.1 → Task 1; §5.3, §5.4 → Task 2; §5.2, §5.6 → Task 3; §5.5 → Task 4; §5.7 → Task 5; §8 → Task 6; §7.3 → Task 7; §6 steps 4–11 → Tasks 8 and 9. Every §7.1 and §7.2 test is named in Tasks 1–5.
- Type consistency: `Tier`, `TierPolicy`, `policy_for`, `_usable_token`, `_PLACEHOLDER_TOKEN`, `_get_list_token` and `Response`/`Fetch` have the same names in every task that uses them.
- Proportion: no function bodies except the regex, the encoding and the policy values that the spec pins.

## Plan review round 1 — disposition

A (`2026-10-27-list-token-tier-plan-A.md`): REQUEST CHANGES, one SHOULD FIX. B (`2026-10-27-list-token-tier-plan-B.md`): APPROVE, four CONSIDER. A and B do not disagree. LTT-PLAN-02 and B-PLAN-04 are the same finding.

| Finding | Disposition |
|---------|-------------|
| LTT-PLAN-01 (SHOULD FIX) guard tests present `OWN`, not the stored value | Fixed. Task 2: every LIST and owner guard case presents the stored value and asserts 401 (or `LIST` for the 32-character case). The owner placeholder case is the test for the spec anti-goal. The log assertions stay. |
| LTT-PLAN-02 / B-PLAN-04 (CONSIDER) spec status still says "For owner approval" | Fixed. The spec status line records the owner's approval on 2026-10-27, with D1–D8 and the 13 calls. |
| LTT-PLAN-03 (CONSIDER) `dateTo=2099-01-15` drops the only private entry | Fixed. Task 3 uses `dateTo=2099-03-01`. Every filter case keeps `SYN-PRIV-001` in the OWNER result. |
| A's note: the "two-tier" grep also hits `architecture.dsl:1,8` and `setup.md:202` | Fixed. Task 6 Steps 4 and 5 name them. |
| B-PLAN-01 (CONSIDER) the `api-rotation` anchor is not named | Fixed. Task 6 Step 3 keeps all three anchors verbatim, forbids a fixture edit, and gives an example rotation bullet that holds the anchor. |
| B-PLAN-02 (CONSIDER) owner stubs stay `own-tok` (7 characters) | Fixed. Task 2 replaces them with an invented 40-character `b64token` value in both test files. |
| B-PLAN-03 (CONSIDER) the tag on the tip of `main` | Fixed. Task 9 Step 7 tags this PR's merge commit. |

## Plan review round 2 — disposition

A (`2026-10-27-list-token-tier-plan-r2-A.md`) and B (`2026-10-27-list-token-tier-plan-r2-B.md`): both APPROVE, every round-1 finding resolved, no new finding. A noted that the invented owner value has 40 characters, not 39. Fixed in Task 2 and in the B-PLAN-02 row.

## Implementation review round 1 — disposition

A (`2026-10-27-list-token-tier-impl-A.md`): APPROVE, no finding (17 of 17 core mutations caught). B (`2026-10-27-list-token-tier-impl-B.md`): REQUEST CHANGES, one SHOULD FIX and four CONSIDER (25 of 26 mutations caught). A and B do not contradict each other: B's findings are new evidence, not a dispute of A's.

| Finding | Disposition |
|---------|-------------|
| B-IMPL-01 (SHOULD FIX) the output-leak test never reached check 4's problem lines | Fixed. The test no longer empties the OWNER listing and asserts that checks 1–8 all FAIL, including the `LIST got 302` and `control got 404` lines. B's id-leak mutation made the new test fail (RED) and the real code passes (GREEN). |
| B-IMPL-02 (CONSIDER) pip-audit: `fsspec` 2026.2.0 PYSEC-2026-4217 | Pre-existing on `main`. `fsspec` comes only through `huggingface_hub` (the `publish` extra); CI syncs `--extra dev`, so CI's pip-audit does not see it, but the lock pinned an affected version. The owner chose to merge Dependabot PR #76 first (2026.6.0, `d7c2d0e`); this branch now starts from it. A CI-like run (`uv sync --frozen --extra dev`, then `pip-audit`) reports no known vulnerability. |
| B-IMPL-03 (CONSIDER) the snapshot recorded one control per entry, check 4 controls every key | Fixed. `take_snapshot` records a control for every artifact key, named `<tier> artifact <provider> #<index>` (a key can hold a match id). `test_snapshot_round_trip_passes` RED, then GREEN. |
| B-IMPL-04 (CONSIDER) `docs/api-reference.md` 401 rows said "either configured value" | Fixed. Both rows name the three tokens and a disabled one. |
| B-IMPL-05 (CONSIDER) the plan's detect-secrets expectation was imprecise | Fixed. Task 7 Step 1 states the `--baseline` form. |
