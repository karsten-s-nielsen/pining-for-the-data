# Design — `AGENTS.md` restructure for pining-for-the-data

- **Date:** 2026-09-25
- **Author session:** pining-for-the-data
- **Independent reviewer:** `karstenskyt__silly-kicks_part-deux` (spec / plan / impl)
- **Pattern origin:** the validated lakehouse `AGENTS.md` restructure (spec + plan APPROVED in two review rounds — reports at `D:\Development\_reviews\2026-09-25-lakehouse-agents-md-restructure-{spec,plan}[-r2].md`). This document is the pining adaptation; the two hard-won part-deux lessons and every gate below are carried verbatim in intent, re-derived for pining's measured reality.
- **r2 (2026-09-25):** resolved spec-review findings — **PINING-SPEC-01** (SHOULD-FIX): `POINTER_RE` in §6.2 check 4 and the symbols-reject in §5 now accept pining's ADR forms in **both** `ADR 0011` (space) and `ADR-0010` (hyphen) styles via `ADR[- ]?\d{3,4}`, plus `docs/decisions/` and `docs/context/` paths; **PINING-SPEC-02** (CONSIDER): §6.2 check 2 adds a cut-verification gate making `TARGET` a hard fail when `LANDED > 5120 B`.

---

## 1. Summary

Partition the always-loaded project instruction file into:

1. **Class-1** — current, terse, enforceable invariants and pointers — which stays always-loaded.
2. **Class-2** — the *why*, the history, and the measurements behind those invariants — which moves to an on-demand context store under `docs/context/`.

Rename the always-loaded file from `CLAUDE.md` to the cross-tool **`AGENTS.md`**, and leave a one-line `CLAUDE.md` shim that `@`-imports it so Claude Code keeps auto-loading the instructions. Add a CI anti-bloat gate whose migration-safety oracle mechanically proves that no invariant was dropped in the move.

The durable value is the **rename** (cross-tool `AGENTS.md` consistency across pining, luxury-lakehouse, and silly-kicks) and the **gate** (keeps the file lean forever). The context tree is secondary and is sized by the measured split — not forced to a fixed shape.

## 2. Current state (measured at `44930cf`, this repo's `main` HEAD)

| Fact | Value | How measured |
|---|---|---|
| `CLAUDE.md` size | **9024 bytes on disk / 8944 bytes of content¹ / 80 lines** | `wc -c`, `wc -l`, Python `len(read().encode())` |
| Non-ASCII bytes | **120** (UTF-8 em-dashes `—`, arrows `→`, etc.) | `sum(1 for b in open(...,'rb').read() if b>127)` |
| Nested instruction files | **none** — single top-level file only | no other `CLAUDE.md` in tree |
| ADR home | **`docs/decisions/`** (not `docs/superpowers/adrs/`) | tree layout |
| Tracked files referencing `CLAUDE.md` | **12** — all historical (`CHANGELOG.md` + `docs/superpowers/{specs,plans}/*.md`) | `git grep -l "CLAUDE\.md"` |
| Functional `open()`/`Path()` of `CLAUDE.md` in code | **0** | `git grep` over `src/ scripts/ terraform/` |
| Packaged? | **No** — no `CLAUDE.md` in `pyproject.toml`; no `bump` script | `grep -n CLAUDE pyproject.toml`; `ls scripts/ | grep bump` |
| Test config | `testpaths = ["src/tests"]`, `addopts = "--tb=short -q"`, no benchmarks | `pyproject.toml:98-99` |
| Fixtures dir | `src/tests/fixtures/` **exists** | `ls` |
| CI checkout | `actions/checkout@v7.0.1`, **no `fetch-depth`** → shallow clone | `.github/workflows/python-ci.yml:24` |
| `@import` mechanism | **empirically verified** at Claude Code **2.1.280** (this running version) — a `CLAUDE.md` shim of `<!-- … -->\n@AGENTS.md` caused a headless `claude -p` to read `AGENTS.md` and return its sentinel `IMPORT_OK_7F3A9QZ` | canary run 2026-09-25, `claude --version` |

¹ 9024 with the trailing newline / on-disk; 8944 measured as the decoded content string. Byte budgets in §7 assert `Path.stat().st_size` (on-disk), so **9024** is the baseline `LANDED_ORIGINAL`.

### 2.1 Measured class-1 / class-2 split

Per-section byte measurement (`stat`-equivalent, UTF-8):

| Section | Bytes | Class | Disposition |
|---|---:|---|---|
| Title + tagline (L1–4) | 139 | 1 | keep (rewritten header) |
| **`## Architecture` (whole)** | **5580** | **mixed** | skeleton stays class-1; rationale moves |
| — simple dir bullets (deidentify/publish/mock_api/tests/schemas/canonical + terraform/docs) | ~1300 | 1 | keep as terse `dir → purpose (ADR ref)` |
| — `src/formats/` bullet (L9) | 1361 | 2 | move (format-family / tier / provenance *why*, ADR 0010/0011/0012) |
| — `scripts/` block (L15–21) | 2920 | 2 | move (script inventory detail + completed-migration audit trail) |
| `## Conventions` (L31–39) | 377 | 1 | keep verbatim-ish |
| `## De-identification` (L41–50) | 636 | 1 | **keep inline** (live reserved-names policy — owner decision) |
| `## CLI Entry Points` (L52–58) | 439 | 1 | keep |
| `## Mock Provider API` (L60–76) | 1451 | mixed | invariants stay class-1; provider-provenance ADR *why* (L67–70, ~350 B) moves |
| `## Pre-commit quality gate` (L78–80) | 309 | 1 | keep (imperative; trim the justification tail) |

**Total class-2 to move ≈ 4.6 KB, ~93% of it in `## Architecture`.** The rest of the file is already terse class-1. This measurement — not a target shape — sizes the context tree below.

## 3. Goals / non-goals

**Goals**
- `AGENTS.md` is the single always-loaded, terse, enforceable class-1 file.
- `CLAUDE.md` is a shim that `@`-imports `AGENTS.md`; Claude Code behaviour is unchanged (verified §2).
- Every class-2 fact is preserved verbatim in `docs/context/`; **zero information dropped** without recorded owner approval.
- A CI gate keeps `AGENTS.md` under a byte ceiling and mechanically proves invariant survival on every future change.

**Non-goals**
- No change to the shared global `~/.claude/CLAUDE.md` (out of scope, explicitly left).
- No behaviour change to shipped code. (The reference sweep touches zero code — §2 shows no functional `open()`. Comment/doc pointer repoints, where any exist, are behaviour-preserving; none are expected here.)
- No new provider, no data change, no infra change.

## 4. Target layout

```
AGENTS.md                              # class-1, always-loaded (target ≤ 5 KB)
CLAUDE.md                              # shim → @AGENTS.md
docs/context/formats-and-tiers.md      # class-2: format families, tier + provenance model, ADR 0010/0011/0012 why
docs/context/scripts-and-migrations.md # class-2: ops-script inventory + completed-migration audit trail
src/tests/fixtures/claude_md_at_44930cf.md   # committed pre-change snapshot (the oracle's baseline)
src/tests/test_agents_md_budget.py     # the anti-bloat gate + migration-safety oracle
src/tests/fixtures/agents_md_invariants.json # the invariant inventory
```

### 4.1 The shim (`CLAUDE.md`)

Exact content — two HTML-comment lines then the import, nothing else:

```
<!-- Canonical project instructions live in AGENTS.md (cross-tool convention). -->
<!-- Claude Code auto-loads CLAUDE.md; this shim @-imports AGENTS.md so both resolve to one source. -->
@AGENTS.md
```

**HTML comments (`<!-- … -->`), never `#`** — a `#` line in a Markdown file is an H1 heading, not a comment. The `@AGENTS.md` line is the sole functional line. This is the exact shape the empirical canary (§2) validated.

### 4.2 Context store: two single-purpose files (gold-standard)

Rationale (owner asked for the best-practice pattern, since it may be copied to other repos): a context store groups by **recall domain** — one purpose per file — so a reader pulls exactly one focused file. The measured class-2 is two distinct domains:

- **`docs/context/formats-and-tiers.md`** — provider format families, the public/owner tier model, `provenance` semantics, and the *why* behind ADR 0010 (StatsBomb faithful-feed), ADR 0011 (SkillCorner canonical Parquet; public tier stays native V3), ADR 0012 (open-data tournaments as `redistributed`). Absorbs the moved `src/formats/` prose and the Mock-API provider-provenance tail.
- **`docs/context/scripts-and-migrations.md`** — the ops-script inventory (per-provider load+verify pairs, canonical-ingest source-family adapters, shared helpers) and the completed-migration audit trail (`backfill_*`, `migrate_*`, the two provider-slug renames), including the "idempotent / safe to re-run / retained as audit trail" notes.

Two files is a genuine split of real mass (~1.7 KB / ~2.9 KB), not a forced tree. A single catch-all is the anti-pattern that rots as it grows.

Each context file opens with a one-line back-pointer to `AGENTS.md` and is discoverable from the `AGENTS.md` bullet that summarises it (`… — see docs/context/<file>.md`).

### 4.3 `AGENTS.md` class-1 content

A fresh, terse rewrite (not a copy) preserving every enforceable invariant and every load-bearing pointer:

- Header (title + one-line purpose + companion-repo note).
- `## Architecture` — one terse bullet per directory: `path — purpose (ADR ref; see docs/context/…)`. The enforceable invariants stay here, e.g.:
  - *owner-tier SkillCorner is canonical Parquet/zstd — ADR 0011*
  - *public A-League tier stays native V3, kept out of the canonical Parquet set — ADR 0011*
  - *both StatsBomb source families are owner-tier only; `provenance` distinguishes `original` (360 club, ADR 0010) from `redistributed` (open tournaments, ADR 0012)*
- `## Conventions` — kept.
- `## De-identification` — kept inline (reserved-names policy).
- `## CLI Entry Points` — kept.
- `## Mock Provider API: two-tier auth` — the invariants (public vs owner tier, SSM SecureString path `/pining-for-the-data/api_token_owner`, `validate_token` → `Tier` enum, uniform `404` not `403`, duplicate-token → `PUBLIC` fail-closed, the rotation command, the `docs/superpowers/specs/2026-05-02-private-data-tier.md` pointer) stay class-1; the ADR-provenance narration moves to `formats-and-tiers.md`.
- `## Pre-commit quality gate` — the `final-review`-before-final-commit imperative stays.

## 5. The invariant inventory (`agents_md_invariants.json`)

A committed JSON fixture enumerating every class-1 invariant that must survive the migration. Schema per entry:

```json
{
  "id": "kebab-case-stable-id",
  "class1_anchor": "a ≥12-char, ≥2-non-stopword-token phrase from the imperative that MUST appear in AGENTS.md",
  "symbols": ["distinctive tokens — fn/const/path/ADR names — that must survive in AGENTS.md ∪ context files"],
  "home": "agents" | "context:<file>"
}
```

Rules (validator-enforced, §7):
- `class1_anchor` is a distinctive phrase (≥ 12 chars **and** ≥ 2 non-stopword tokens) — rejects vacuous anchors like `"the rule"` or `"see ADR"` that could match `AGENTS.md` boilerplate by accident.
- `symbols` must be **distinctive** — a bare ADR reference in **either** pining form (matched by `^ADR[- ]?\d{3,4}$`, covering both the space form `ADR 0011` and the hyphen form `ADR-0010` — both occur, e.g. `CLAUDE.md` L9 carries `ADR 0011` and "the purest `ADR-0010` case") is **rejected** (ADR numbers recur across the file; matching on them alone would pass vacuously). Each symbol is a function/const/path name or a distinctive multi-word phrase.
- The fixture carries a top-level `"count"`, and the test carries a module-level `INVARIANT_COUNT` constant; **both** are set to the enumerated N, and the test asserts `count == len(entries) == INVARIANT_COUNT`. This dual-source pin is what makes a silent shrink (delete an entry *and* decrement `count`) fail. The concrete inventory and N are produced in the plan/impl (estimated N ≈ 16–20).

## 6. The CI anti-bloat gate + migration-safety oracle

New test module `src/tests/test_agents_md_budget.py`, collected by the existing `testpaths = ["src/tests"]`. **Every file read uses `encoding="utf-8"` explicitly** (the file has 120 non-ASCII bytes; a bare `open()` on the Windows dev box defaults to `cp1252` and mojibakes em-dashes/arrows — a false FAIL on exactly the machine §11's local gate runs on; ubuntu CI would hide it).

### 6.1 Committed-snapshot oracle — never `git show`

The oracle reads a **committed fixture** `src/tests/fixtures/claude_md_at_44930cf.md` (a verbatim copy of `CLAUDE.md` at `44930cf`, the pre-change content), via `open(path, encoding="utf-8")`. It must **never** shell out to `git show <sha>:CLAUDE.md` — pining's CI checks out shallow (no `fetch-depth`, §2), so the parent blob is absent and `git show` exits 128. This is the load-bearing part-deux/lakehouse lesson.

### 6.2 Checks

1. **`test_shim_integrity`** — read `CLAUDE.md`; keep non-blank lines whose `lstrip()` does **not** start with `<!--`; assert the survivors are exactly `["@AGENTS.md"]`. (Strips `<!--` HTML comments, matching §4.1 — not `#`.)
2. **`test_agents_md_byte_budget`** — `Path("AGENTS.md").stat().st_size` (bytes on disk, never `len(text)`):
   - hard **CEILING** = `LANDED × 1.15`, where `LANDED` is the pinned as-shipped `AGENTS.md` byte size (set at impl once the rewrite exists) — the operative anti-bloat brake against *future* growth.
   - **TARGET** = 5120 B — the cut-verification bound (see next bullet): at migration time `LANDED` **must** be ≤ TARGET or the gate hard-FAILs; thereafter TARGET is the visible warning line that sits below `CEILING`, so a later justified drift shows up before it reaches the ceiling.
   - **cut-verification gate (PINING-SPEC-02):** because `CEILING` keys off `LANDED`, it only brakes future bloat — it does not by itself prove *this* migration cut the ~4.6 KB of class-2. Two things close that: (a) the conservation floor (check 3) + completeness (check 6) prove the class-2 landed in the context files; and (b) at impl, `LANDED` **must** fall at ≈ 4–5 KB (from 9024 B). If `LANDED > TARGET` (5120 B), that is a signal the move is incomplete (class-2 prose remains inline) — impl treats it as a **hard FAIL**, not a soft report, and investigates before pinning `CEILING`. So `TARGET` is soft only while `LANDED ≤ TARGET`; a landed size above it fails the gate.
3. **`test_context_conservation_floor`** — each `docs/context/*.md` `stat().st_size ≥ 800 B` (non-stub) **and** their combined size `≥ 3072 B` (proves the ~4.6 KB class-2 actually landed there rather than being deleted). Anti-hollowing companion to the completeness check.
4. **`test_per_bullet_char_cap`** — split `AGENTS.md` into bullets; assert each bullet's **decoded-`str` length ≤ 600 chars** (a char cap on UTF-8-decoded text — deliberately distinct from the file's byte budget), and that every *substantive* bullet carries a pointer matched by **`POINTER_RE`**. `POINTER_RE` must accept **all** pining pointer forms:
   - an ADR reference in either form — `ADR[- ]?\d{3,4}` (covers `ADR 0011` and `ADR-0010`);
   - a `docs/decisions/…` path (the ADR home);
   - a `docs/context/…` path (the context store);
   - any other `docs/…\.md` path (e.g. the `docs/superpowers/specs/2026-05-02-private-data-tier.md` pointer kept in the Mock-API section).

   A too-narrow regex (e.g. the inherited `ADR-\d+`, hyphen-only) would false-FAIL a kept-inline class-1 bullet that points only to `(ADR 0011)` in the space form — this is the exact PINING-SPEC-01 gap. Keeps class-2 prose from creeping back into a bullet while accepting every legitimate pointer form.
5. **`test_inventory_schema_valid`** — load `agents_md_invariants.json`; assert `count == len(entries) == INVARIANT_COUNT`; every `class1_anchor` satisfies the ≥12-char / ≥2-non-stopword-token rule; no `symbols` entry is a bare ADR number; all ids unique.
6. **`test_invariant_completeness`** (the migration-safety oracle) — for each inventory entry: the `class1_anchor` substring appears in the UTF-8-decoded `AGENTS.md`; every `symbol` appears in `AGENTS.md ∪ docs/context/*.md`. Reads the committed snapshot only as documentation of the source-of-truth set (the inventory is authored from it). A dropped invariant fails this test.

All substring matching runs on UTF-8-decoded text so a non-ASCII anchor matches.

## 7. Reference sweep (three buckets)

`git grep -l "CLAUDE\.md"` (tracked only) → **12 files**, all in `CHANGELOG.md` + `docs/superpowers/{specs,plans}/`:

- **LIVE rule-text refs** (a reader would follow to find current rules) → repoint to `AGENTS.md`. **Expected: none** — no source file `open()`s it (§2), and no doc points to it as the live rulebook.
- **HISTORICAL** (past specs, plans, changelog entries — a record of what was true then) → **leave unchanged.** All 12 fall here.
- **Global** `~/.claude/CLAUDE.md` and third-party skill mentions → out of scope, left.

Net: **repoint zero.** Impl re-runs the grep, re-confirms every hit is historical, and confirms zero functional `open()`s. Any surprise LIVE ref found at impl is repointed and recorded.

## 8. Scope & safety

- Verbatim moves — class-2 is **cut** from `CLAUDE.md` and pasted into the context files, not paraphrased.
- **Zero drops by default.** If any class-2 fact is judged redundant and dropped rather than moved, it is recorded in the inventory with its reason and requires explicit owner approval first (an approved drop, not a silent one).
- The class-1 rewrite is *fresh* (terse), so `AGENTS.md` is smaller than the class-1 slice of the original; conservation of the *moved* material is guaranteed by the completeness check (§6.2, check 6) + the context floor (§6.2, check 3), not by total byte parity.
- Global instruction file untouched. No behaviour change to shipped code.

## 9. Versioning / packaging

`CLAUDE.md` is **not packaged** (absent from `pyproject.toml`; no `bump` script — §2). This is a docs/infra change: **no version bump, no `CHANGELOG.md` release entry required.** Impl re-verifies by confirming `AGENTS.md`/`CLAUDE.md` appear in no build include list (`pyproject.toml` `[tool.hatch.build*]`) before concluding no bump.

## 10. Acceptance criteria — local gate mirrors CI exactly

The local gate runs pining's **actual** `python-ci.yml` check set (not a narrower subset), all green, before the work is declared complete:

1. `uv run ruff check src/`
2. `uv run ruff format --check src/`
3. `uv run pyright src/`
4. `uv run pytest --cov=src --cov-report=term-missing` (collects the new `src/tests/test_agents_md_budget.py` via `testpaths`)
5. `uv run pip-audit` — known-stale caveat: pip-audit can red on "own package not on PyPI" keyed on project version; that is pre-existing and unrelated to this change.
6. `uvx detect-secrets==1.5.0 scan --baseline .secrets.baseline` — cross-platform baseline caveat: Windows/Linux path/hash differences are a known pining gotcha; do not regenerate the baseline to force green.

Plus a **red→green proof**: the gate test is written and shown RED (against the un-migrated tree) before the restructure, and green after.

## 11. Workflow & commit discipline

- One **feature branch** off `main` (never a worktree); commits accumulate there; PR opens from it.
- A **single commit** for the whole cycle — the restructure + the gate + the context files + the (zero) sweep together, as one fully-tested coherent state. **No per-step / micro-commits.**
- **`final-review` skill runs before the commit** (repo rule).
- `commit → push → PR → merge` are **four separate owner-gated actions.** None is implied by approval of a prior one. The session stops and shows the diff / file list at the commit gate and waits for an explicit "commit"; likewise for push, PR, and merge.
- The spec and plan documents commit **with** the finished code in that single commit (not earlier).

## 12. Review checkpoints (independent)

`karstenskyt__silly-kicks_part-deux` reviews at spec, plan, and impl; reports land in `D:\Development\_reviews\` as `2026-09-2X-pining-agents-md-restructure-{spec,plan,impl}.md`. This session delivers each artifact, stops, and hands off.

## 13. Risks / could-not-verify

- **Exact invariant set / N** — enumerated in the plan/impl from the committed snapshot; the spec fixes only the schema and the dual-source count pin.
- **`pip-audit` / `detect-secrets`** local failures may be pre-existing environmental noise (§10 caveats); triage before attributing to this change.
- **`@import`** verified at 2.1.280 (this version); a future Claude Code that changed import semantics would need re-verification, but the shim + `AGENTS.md` also satisfies the cross-tool `AGENTS.md` convention independent of Claude Code's importer.
