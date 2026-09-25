# AGENTS.md Restructure — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this plan is a single coupled change landing in **one commit**; execute inline with `superpowers:executing-plans` (batch with checkpoints). Subagent-driven execution is **not** used here (the part-deux precedent, and the single-commit surface, make it needless). Steps use checkbox (`- [ ]`) syntax. **No per-task commits** — see Global Constraints.

**Goal:** Rename the always-loaded project instruction file `CLAUDE.md` → cross-tool `AGENTS.md` behind a one-line `@import` shim, move the class-2 (why/history) content to an on-demand `docs/context/` store, and add a CI anti-bloat gate whose migration-safety oracle proves no class-1 invariant was dropped.

**Architecture:** `AGENTS.md` holds terse class-1 invariants + pointers (always loaded). `CLAUDE.md` becomes `<!-- … -->\n@AGENTS.md` (empirically verified to auto-load `AGENTS.md` at Claude Code 2.1.280). Two single-purpose context files under `docs/context/` hold the moved class-2. A pytest module in `src/tests/` reads a committed pre-change snapshot (never `git show` — CI is shallow) and a committed invariant inventory to enforce byte budgets, shim purity, and per-invariant survival.

**Tech Stack:** Python 3.12, pytest, `uv`, ruff, pyright, GitHub Actions (`python-ci.yml`), Markdown.

**Spec:** `docs/superpowers/specs/2026-09-25-agents-md-restructure-design.md` (r2 APPROVED). The plan argues from it; executors read both.

## Global Constraints

- **Single commit for the whole cycle.** No per-step / per-task / micro-commits. Every task below ends at a *tested deliverable*, not a commit. The one commit happens only in Task 7, after `final-review` and a full green local gate. (This overrides any "commit often" convention.)
- **`commit → push → PR → merge` are four separate owner-gated actions.** None is implied by approval of a prior one. Stop and show the diff/file-list at each gate; wait for an explicit "commit" / "push" / "open the PR" / "merge".
- **One feature branch off `main`** (never a worktree). Branch: `chore/agents-md-restructure`.
- **All file reads in test/oracle code use `encoding="utf-8"` explicitly** (the file has 120 non-ASCII bytes; a bare `open()` on Windows defaults to cp1252 and mojibakes em-dashes/arrows → false FAIL locally).
- **Byte budgets assert `Path.stat().st_size`** (bytes on disk), never `len(text)`. The per-bullet cap is a **char** cap on decoded text — deliberately distinct.
- **Committed-snapshot oracle:** read the pre-change file from `src/tests/fixtures/claude_md_at_44930cf.md`, never `git show <sha>:CLAUDE.md` (CI checkout is shallow — no `fetch-depth` — so the parent blob is absent and `git show` exits 128).
- **Zero information dropped.** Class-2 is *cut and pasted verbatim* into context files, not paraphrased. Any drop requires recorded owner approval first.
- **No behaviour change to shipped code.** The reference sweep touches zero code (no functional `open("CLAUDE.md")` exists).
- **No version bump** — `CLAUDE.md`/`AGENTS.md` are not packaged (re-verify in Task 5).
- **Local gate == CI check set exactly** (Task 6): `ruff check src/`, `ruff format --check src/`, `pyright src/`, `pytest --cov=src --cov-report=term-missing`, `pip-audit`, `detect-secrets scan --baseline`.
- **Scope:** the shared global `~/.claude/CLAUDE.md` is **not** touched.

---

## File structure

| File | Responsibility |
|---|---|
| `AGENTS.md` (create) | Class-1 always-loaded instructions (terse; target ≤ 5120 B). |
| `CLAUDE.md` (rewrite → shim) | `<!-- … -->` comments + `@AGENTS.md`, nothing else. |
| `docs/context/formats-and-tiers.md` (create) | Class-2: format families, tier/provenance model, ADR 0010/0011/0012 why. |
| `docs/context/scripts-and-migrations.md` (create) | Class-2: ops-script inventory + completed-migration audit trail. |
| `src/tests/fixtures/claude_md_at_44930cf.md` (create) | Verbatim pre-change snapshot of `CLAUDE.md` @ `44930cf`. |
| `src/tests/fixtures/agents_md_invariants.json` (create) | The class-1 invariant inventory (18 entries). |
| `src/tests/test_agents_md_budget.py` (create) | The anti-bloat gate + migration-safety oracle. |

---

## Task 1: Committed snapshot fixture + invariant inventory

**Files:**
- Create: `src/tests/fixtures/claude_md_at_44930cf.md`
- Create: `src/tests/fixtures/agents_md_invariants.json`

**Interfaces:**
- Produces: the snapshot file (oracle baseline) and the inventory JSON consumed by `test_agents_md_budget.py` (Task 2). Inventory shape: `{"count": 18, "entries": [{"id","class1_anchor","symbols":[...],"home"}]}`.

- [ ] **Step 1: Copy the pre-change file verbatim into the fixture**

`44930cf` is the current `main` HEAD, so the working-tree `CLAUDE.md` *is* the pre-change content. Copy it byte-for-byte:

```bash
cp CLAUDE.md src/tests/fixtures/claude_md_at_44930cf.md
```

Verify identical:

```bash
diff CLAUDE.md src/tests/fixtures/claude_md_at_44930cf.md && echo IDENTICAL
python -c "import pathlib; print(pathlib.Path('src/tests/fixtures/claude_md_at_44930cf.md').stat().st_size)"   # expect 9024
```

- [ ] **Step 2: Author the invariant inventory**

Create `src/tests/fixtures/agents_md_invariants.json` with **exactly 18 entries**. Each `class1_anchor` is a phrase that MUST appear verbatim in `AGENTS.md` (Task 3); each `symbols` entry is a distinctive token that MUST survive in `AGENTS.md ∪ docs/context/*.md`. `home` is `agents` (invariant lives fully in AGENTS.md) or `context:<file>` (anchor in AGENTS.md, detail-symbols in that context file).

```json
{
  "count": 18,
  "entries": [
    {"id": "deidentify-two-layer", "class1_anchor": "Two-layer mapping: stable synthetic identity (Layer 1) → per-game jersey mapping (Layer 2)", "symbols": ["src/deidentify/"], "home": "agents"},
    {"id": "deid-reserved-names", "class1_anchor": "4 featured names: Fezzik Took, Tormund Tully, Westley Montoya, T'Challa Stark", "symbols": ["Wakanda FC", "Asgard Athletic"], "home": "agents"},
    {"id": "formats-owner-canonical-parquet", "class1_anchor": "canonical columnar Parquet/zstd", "symbols": ["skillcorner_canonical.py", "tracking.parquet", "format_version", "skillcorner_raw.py"], "home": "context:formats-and-tiers"},
    {"id": "formats-public-native-v3", "class1_anchor": "public A-League tier stays in its native V3 shape", "symbols": ["skillcorner_opendata.py", "SkillCorner Open Data"], "home": "context:formats-and-tiers"},
    {"id": "statsbomb-two-families-provenance", "class1_anchor": "both StatsBomb source families are owner-tier only", "symbols": ["statsbomb_open.py", "provenance", "redistributed", "original"], "home": "context:formats-and-tiers"},
    {"id": "statsbomb-faithful-feed", "class1_anchor": "faithful-feed gzip-JSON", "symbols": ["de-pivot"], "home": "context:formats-and-tiers"},
    {"id": "schemas-pydantic-free-runtime", "class1_anchor": "models kept out of the Lambda zip so the runtime stays pydantic-free", "symbols": ["src/canonical/models.py", "MatchEntry", "PlayerRecord"], "home": "agents"},
    {"id": "scripts-load-verify-pairs", "class1_anchor": "per-provider load + post-load verify pairs", "symbols": ["upload_gradient_wc2022.py", "verify_gradient_load.py", "upload_idsse_bundesliga.py", "verify_idsse_load.py", "upload_statsbomb_club.py", "upload_statsbomb_open.py", "verify_statsbomb_load.py"], "home": "context:scripts-and-migrations"},
    {"id": "scripts-canonical-ingest-adapters", "class1_anchor": "two source-family adapters sharing one verify", "symbols": ["upload_skillcorner_raw.py", "upload_skillcorner_parquet.py", "partition_ingestible", "verify_skillcorner_canonical_load.py", "upload_skillcorner_realmadrid.py", "verify_skillcorner_realmadrid_load.py"], "home": "context:scripts-and-migrations"},
    {"id": "scripts-opendata-public", "class1_anchor": "Public-tier SkillCorner Open Data", "symbols": ["upload_skillcorner_opendata.py", "verify_skillcorner_opendata_load.py", "players_from_meta", "derive_players"], "home": "context:scripts-and-migrations"},
    {"id": "scripts-completed-migrations-audit", "class1_anchor": "Completed one-shot migrations", "symbols": ["backfill_skillcorner_artifacts.py", "migrate_skillcorner_tracking_parquet.py", "migrate_pff_to_gradientsports.py", "migrate_gradientsports_slug.py", "backfill_skillcorner_opendata_lfs.py", "idsse_figshare_manifest.json", "_verify_http.py", "regenerate_schemas.py"], "home": "context:scripts-and-migrations"},
    {"id": "conventions-toolchain", "class1_anchor": "Ruff for linting/formatting (line-length 120)", "symbols": ["Python 3.12+", "Pyright", "hatch"], "home": "agents"},
    {"id": "conventions-no-tracking-data", "class1_anchor": "No tracking data files in the repo", "symbols": ["too large for git"], "home": "agents"},
    {"id": "conventions-licensing", "class1_anchor": "MIT license (code + redistributed SkillCorner data)", "symbols": ["CC-BY-4.0"], "home": "agents"},
    {"id": "cli-entry-points", "class1_anchor": "upload game artifacts to S3 and update provider indexes", "symbols": ["pining-generate-roster", "pining-ingest", "pining-publish", "pining-upload", "pining-upload-players", "--visibility"], "home": "agents"},
    {"id": "api-two-tier-ssm", "class1_anchor": "bearer token stored in SSM Parameter Store SecureString", "symbols": ["/pining-for-the-data/api_token_owner", "api_token", "terraform.tfvars"], "home": "agents"},
    {"id": "api-tier-enum-404-failclosed", "class1_anchor": "Tier mismatch returns uniform `404`", "symbols": ["validate_token", "Tier", "PUBLIC", "OWNER", "shared.py"], "home": "agents"},
    {"id": "api-rotation", "class1_anchor": "bump `LAST_ROTATION` env var on all 6 Lambdas", "symbols": ["last_rotation", "401 retry"], "home": "agents"}
  ]
}
```

- [ ] **Step 3: Ground every anchor/symbol in the snapshot**

Each entry must be traceable to the pre-change file (proves the inventory is not fabricated). Run:

```bash
python - <<'PY'
import json, pathlib
snap = pathlib.Path("src/tests/fixtures/claude_md_at_44930cf.md").read_text(encoding="utf-8")
inv = json.loads(pathlib.Path("src/tests/fixtures/agents_md_invariants.json").read_text(encoding="utf-8"))
for e in inv["entries"]:
    grounded = (e["class1_anchor"] in snap) or any(s in snap for s in e["symbols"])
    print(("OK  " if grounded else "MISS"), e["id"])
PY
```

Every line must print `OK`. If any prints `MISS`, the anchor/symbol string is mistyped relative to the source — fix the string to match the snapshot exactly (do not invent). Also confirm `count == 18 == len(entries)` and all ids unique.

> Note: `final-review` and `no-tracking-data` etc. anchors are chosen to appear verbatim in the snapshot; `formats-owner-canonical-parquet` uses the symbol `skillcorner_canonical.py` (present in snapshot L9) for grounding since its short anchor `canonical columnar Parquet/zstd` is also present verbatim.

---

## Task 2: The gate test module (written RED)

**Files:**
- Create: `src/tests/test_agents_md_budget.py`

**Interfaces:**
- Consumes: the two fixtures from Task 1.
- Produces: 6 tests. `LANDED_BYTES` is a placeholder value here and is pinned to the measured `AGENTS.md` size in Task 3 Step 5.

- [ ] **Step 1: Write the full test module**

```python
"""Anti-bloat gate + migration-safety oracle for AGENTS.md.

Guards that the always-loaded instruction file (AGENTS.md) stays terse
(class-1), that CLAUDE.md stays a pure @AGENTS.md import shim, and that no
class-1 invariant was dropped when class-2 content moved to docs/context/.
Spec: docs/superpowers/specs/2026-09-25-agents-md-restructure-design.md
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]          # src/tests/ -> repo root
AGENTS = REPO_ROOT / "AGENTS.md"
SHIM = REPO_ROOT / "CLAUDE.md"
CONTEXT_DIR = REPO_ROOT / "docs" / "context"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SNAPSHOT = FIXTURES / "claude_md_at_44930cf.md"
INVENTORY = FIXTURES / "agents_md_invariants.json"

# --- byte budgets (bytes on disk, never len(text)) ---
LANDED_BYTES = 4600      # measured AGENTS.md size; PINNED in Task 3 Step 5
TARGET = 5120            # cut-verification bound: LANDED_BYTES must be <= this
CEILING = int(LANDED_BYTES * 1.15)   # future-bloat brake
CONTEXT_FILE_FLOOR = 800
CONTEXT_TOTAL_FLOOR = 3072
PER_BULLET_CHAR_CAP = 600
POINTER_BULLET_THRESHOLD = 250       # long bullets must delegate to ADR/docs (PINING-PLAN-01):
                                     #   250 not lakehouse's 120 — a self-contained class-1 bullet that
                                     #   names a src/ path (e.g. `schemas/` ~215 chars, cites src/canonical/models.py,
                                     #   no docs/ADR pointer) would false-fail at 120. The hard LANDED<=5120 TARGET
                                     #   is the real anti-bloat backstop; this is the anti-prose-creep belt-and-braces.

# --- anti-shrink pin: dual-source with the fixture's own "count" ---
INVARIANT_COUNT = 18                 # == inventory["count"] == len(entries); a fixture-only shrink fails against this

# pointer forms accepted on a long AGENTS.md bullet (PINING-SPEC-01):
#   ADR 0011 / ADR-0010 ; docs/decisions/... ; docs/context/... ; any docs/*.md
POINTER_RE = re.compile(r"ADR[- ]?\d{3,4}|docs/decisions/|docs/context/|docs/[\w./-]+\.md")
# a symbols[] entry that is ONLY a bare ADR ref (either form) is rejected:
BARE_ADR_RE = re.compile(r"^ADR[- ]?\d{3,4}$")

STOPWORDS = {"the", "a", "an", "of", "to", "in", "on", "and", "or", "is", "are",
             "be", "for", "with", "that", "this", "it", "as", "at", "by", "so"}


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")     # utf-8 explicit: 120 non-ASCII bytes


def _bullets(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.lstrip().startswith("- ")]


def test_shim_integrity():
    lines = [ln for ln in _read(SHIM).splitlines()
             if ln.strip() and not ln.lstrip().startswith("<!--")]
    assert lines == ["@AGENTS.md"], f"CLAUDE.md must be a pure @AGENTS.md shim; got {lines}"


def test_agents_md_byte_budget():
    # cut-verification (PINING-SPEC-02): landed over TARGET => class-2 not fully moved.
    assert LANDED_BYTES <= TARGET, f"LANDED {LANDED_BYTES} B over TARGET {TARGET} B"
    size = AGENTS.stat().st_size
    assert size <= CEILING, f"AGENTS.md {size} B exceeds CEILING {CEILING} B (LANDED×1.15)"


def test_context_conservation_floor():
    files = sorted(CONTEXT_DIR.glob("*.md"))
    assert files, "no docs/context/*.md present"
    total = 0
    for f in files:
        s = f.stat().st_size
        assert s >= CONTEXT_FILE_FLOOR, f"{f.name} {s} B below floor {CONTEXT_FILE_FLOOR} B (stub?)"
        total += s
    assert total >= CONTEXT_TOTAL_FLOOR, f"context total {total} B below {CONTEXT_TOTAL_FLOOR} B"


def test_per_bullet_char_cap():
    bullets = _bullets(_read(AGENTS))
    for b in bullets:
        assert len(b) <= PER_BULLET_CHAR_CAP, f"bullet > {PER_BULLET_CHAR_CAP} chars: {b[:80]}"
    # A long bullet must delegate detail to an ADR / docs pointer rather than
    # inline class-2 prose. Short self-contained bullets (CLI, conventions) exempt.
    for b in bullets:
        if len(b) >= POINTER_BULLET_THRESHOLD:
            assert POINTER_RE.search(b), f"long bullet lacks ADR/docs pointer: {b[:100]}"


def test_inventory_schema_valid():
    data = json.loads(_read(INVENTORY))
    entries = data["entries"]
    ids = [e["id"] for e in entries]
    assert data["count"] == len(entries) == INVARIANT_COUNT, \
        f"count pin mismatch: json={data['count']} entries={len(entries)} const={INVARIANT_COUNT}"
    assert len(set(ids)) == len(ids), "duplicate invariant ids"
    for e in entries:
        anchor = e["class1_anchor"]
        assert len(anchor) >= 12, f"anchor too short: {anchor!r}"
        tokens = [t for t in re.findall(r"[\w'/.+-]+", anchor.lower()) if t not in STOPWORDS]
        assert len(tokens) >= 2, f"anchor <2 non-stopword tokens: {anchor!r}"
        for sym in e["symbols"]:
            assert not BARE_ADR_RE.match(sym), f"bare ADR symbol rejected: {sym!r}"
        assert e["home"] == "agents" or e["home"].startswith("context:"), e["home"]


def test_invariant_completeness():
    """Migration-safety oracle: every class-1 anchor survives in AGENTS.md and
    every distinctive symbol survives in AGENTS.md ∪ context files."""
    data = json.loads(_read(INVENTORY))
    agents_text = _read(AGENTS)
    context_text = "\n".join(_read(f) for f in sorted(CONTEXT_DIR.glob("*.md")))
    haystack = agents_text + "\n" + context_text
    for e in data["entries"]:
        assert e["class1_anchor"] in agents_text, \
            f"class1_anchor missing from AGENTS.md: {e['id']} :: {e['class1_anchor']!r}"
        for sym in e["symbols"]:
            assert sym in haystack, \
                f"symbol dropped (not in AGENTS.md ∪ context): {e['id']} :: {sym!r}"


def test_inventory_grounded_in_snapshot():
    """The inventory is authored from the real pre-change file, not fabricated."""
    data = json.loads(_read(INVENTORY))
    snap = _read(SNAPSHOT)
    for e in data["entries"]:
        grounded = (e["class1_anchor"] in snap) or any(s in snap for s in e["symbols"])
        assert grounded, f"invariant not grounded in snapshot: {e['id']}"
```

(`INVARIANT_COUNT = 18` is defined in the budgets block above — it is the dual-source anti-shrink pin: deleting an inventory entry *and* decrementing `count` still fails against this hardcoded constant.)

- [ ] **Step 2: Run the module RED**

```bash
uv run pytest src/tests/test_agents_md_budget.py -v
```

Expected: FAILs — `AGENTS.md` and `docs/context/` do not exist yet, and `CLAUDE.md` is still the full file (not a shim). `test_inventory_schema_valid` and `test_inventory_grounded_in_snapshot` should already PASS (fixtures exist from Task 1). Record which fail; they are the RED half of the red→green proof (Task 6).

---

## Task 3: `AGENTS.md` class-1 rewrite + `CLAUDE.md` shim

**Files:**
- Create: `AGENTS.md`
- Rewrite: `CLAUDE.md` (→ shim)
- Modify: `src/tests/test_agents_md_budget.py` (pin `LANDED_BYTES`)

**Interfaces:**
- Produces: `AGENTS.md` containing every `class1_anchor` (Task 1) verbatim; `CLAUDE.md` shim.

- [ ] **Step 1: Write `AGENTS.md` (fresh, terse class-1)**

Structure (each `class1_anchor` from the inventory MUST appear verbatim; each Architecture format/scripts bullet MUST carry a `docs/context/…` pointer; keep every bullet ≤ 600 chars):

- **Header:** title `# pining-for-the-data`, the one-line purpose + `Companion repo to luxury-lakehouse.`
- **`## Architecture`** — terse `path — purpose` bullets:
  - `src/deidentify/` — keep purpose line.
  - `src/formats/` — terse: provider readers/writers; **owner-tier SkillCorner is the canonical columnar Parquet/zstd set (ADR 0011); the public A-League tier stays in its native V3 shape (ADR 0011); both StatsBomb source families are owner-tier only, faithful-feed gzip-JSON, provenance distinguishing `original` (ADR 0010) from `redistributed` (ADR 0012). See docs/context/formats-and-tiers.md.** (Anchors: `canonical columnar Parquet/zstd`, `public A-League tier stays in its native V3 shape`, `both StatsBomb source families are owner-tier only`, `faithful-feed gzip-JSON`.)
  - `src/publish/`, `src/mock_api/`, `src/tests/` — one-line each.
  - `schemas/` — include anchor `models kept out of the Lambda zip so the runtime stays pydantic-free`.
  - `src/canonical/` — `MatchEntry`, `PlayerRecord`.
  - `scripts/` — terse: one-shot ops scripts — **per-provider load + post-load verify pairs; canonical SkillCorner ingest with two source-family adapters sharing one verify; Public-tier SkillCorner Open Data; Completed one-shot migrations retained as the audit trail. See docs/context/scripts-and-migrations.md.** (Anchors: `per-provider load + post-load verify pairs`, `two source-family adapters sharing one verify`, `Public-tier SkillCorner Open Data`, `Completed one-shot migrations`.)
  - `terraform/…`, `docs/decisions/`, `docs/superpowers/{specs,plans}/` — one-line each.
- **`## Conventions`** — keep verbatim: anchors `Ruff for linting/formatting (line-length 120)`, `No tracking data files in the repo`, `MIT license (code + redistributed SkillCorner data)`; keep `Python 3.12+`, `Pyright`, `hatch`, `CC-BY-4.0`, `too large for git`.
- **`## De-identification (for future private data)`** — keep inline; anchors `Two-layer mapping: stable synthetic identity (Layer 1) → per-game jersey mapping (Layer 2)`, `4 featured names: Fezzik Took, Tormund Tully, Westley Montoya, T'Challa Stark`; keep `Wakanda FC`, `Asgard Athletic`.
- **`## CLI Entry Points`** — keep all five; anchor `upload game artifacts to S3 and update provider indexes`, keep `--visibility`.
- **`## Mock Provider API: two-tier auth`** — keep invariants; anchors `bearer token stored in SSM Parameter Store SecureString`, `Tier mismatch returns uniform \`404\``, `bump \`LAST_ROTATION\` env var on all 6 Lambdas`; keep `/pining-for-the-data/api_token_owner`, `api_token`, `terraform.tfvars`, `validate_token`, `Tier`, `PUBLIC`, `OWNER`, `shared.py`, `last_rotation`, `401 retry`, and the `docs/superpowers/specs/2026-05-02-private-data-tier.md` pointer. Move the ADR-provenance narration (current L67–70) to `formats-and-tiers.md`.
- **`## Pre-commit quality gate`** — keep the imperative: `Always run the \`final-review\` skill before the final commit`.

- [ ] **Step 2: Write the `CLAUDE.md` shim**

Replace the entire file with exactly:

```bash
cat > CLAUDE.md <<'EOF'
<!-- Canonical project instructions live in AGENTS.md (cross-tool convention). -->
<!-- Claude Code auto-loads CLAUDE.md; this shim @-imports AGENTS.md so both resolve to one source. -->
@AGENTS.md
EOF
```

- [ ] **Step 3: Verify every anchor is present in AGENTS.md**

```bash
python - <<'PY'
import json, pathlib
inv = json.loads(pathlib.Path("src/tests/fixtures/agents_md_invariants.json").read_text(encoding="utf-8"))
agents = pathlib.Path("AGENTS.md").read_text(encoding="utf-8")
missing = [e["id"] for e in inv["entries"] if e["class1_anchor"] not in agents]
print("MISSING ANCHORS:", missing or "none")
PY
```

Must print `none`. If any anchor is missing, edit `AGENTS.md` to include the exact phrase (do not edit the inventory to dodge it).

- [ ] **Step 4: Verify per-bullet cap + long-bullet pointers hold**

```bash
uv run pytest src/tests/test_agents_md_budget.py::test_per_bullet_char_cap -v
```

Expected: PASS. If a long Architecture bullet fails the pointer check, ensure it contains `docs/context/…`. If a bullet exceeds 600 chars, split the detail into the context file.

- [ ] **Step 5: Measure and pin `LANDED_BYTES`**

```bash
python -c "import pathlib; print(pathlib.Path('AGENTS.md').stat().st_size)"
```

Set `LANDED_BYTES` in `test_agents_md_budget.py` to that exact number. **It must be ≤ 5120** (TARGET). If it exceeds 5120, the class-2 move is incomplete — move more prose to the context files and re-measure until `LANDED_BYTES ≤ 5120`. Then:

```bash
uv run pytest src/tests/test_agents_md_budget.py::test_agents_md_byte_budget src/tests/test_agents_md_budget.py::test_shim_integrity -v
```

Expected: PASS.

---

## Task 4: The two context files (verbatim class-2 moves)

**Files:**
- Create: `docs/context/formats-and-tiers.md`
- Create: `docs/context/scripts-and-migrations.md`

**Interfaces:**
- Produces: the two context files; together they must contain every `context:*`-homed symbol from the inventory.

- [ ] **Step 1: Write `docs/context/formats-and-tiers.md`**

Open with a back-pointer line, then paste **verbatim** (cut, not paraphrase) from the snapshot: the full `src/formats/` rationale (snapshot L9 — the ADR 0010/0011/0012 provenance/tier prose, the `skillcorner_canonical.py` transform note, the public-V3 `skillcorner_opendata.py` peer-of-`skillcorner_raw.py` detail, the four-artifact set) and the Mock-API provider-provenance narration (snapshot L67–70). Add `##` headings for readability but do not alter the substance. Header:

```markdown
# Formats & tiers — rationale

> Class-2 context for the `## Architecture` / `## Mock Provider API` sections of [AGENTS.md](../../AGENTS.md). The enforceable invariants live there; this file holds the why.
```

- [ ] **Step 2: Write `docs/context/scripts-and-migrations.md`**

Same pattern: back-pointer header, then paste **verbatim** the `scripts/` block detail (snapshot L15–21) — the load+verify pairs enumerated, the canonical-ingest adapters + `partition_ingestible` defect-skip + retained legacy RM loader, the public opendata loader detail, the shared helpers, and the completed-migration audit-trail bullet with all `backfill_*`/`migrate_*` scripts + `idsse_figshare_manifest.json`.

```markdown
# Scripts & migrations — inventory and audit trail

> Class-2 context for the `scripts/` bullet of [AGENTS.md](../../AGENTS.md). AGENTS.md keeps the terse role summary; this file is the full inventory + the completed-migration audit trail.
```

- [ ] **Step 3: Verify conservation + completeness**

```bash
uv run pytest src/tests/test_agents_md_budget.py::test_context_conservation_floor src/tests/test_agents_md_budget.py::test_invariant_completeness -v
```

Expected: PASS. If `test_invariant_completeness` reports a dropped symbol, that token was neither kept in `AGENTS.md` nor pasted into a context file — restore it verbatim to the correct context file. Do **not** delete an inventory entry to pass.

---

## Task 5: Reference sweep + packaging re-verify

**Files:** none created; verification + (only if a live ref is found) targeted edits.

- [ ] **Step 1: Re-run the tracked-ref sweep and bucket every hit**

```bash
git grep -l "CLAUDE\.md"
```

Expected: the 12 known files (`CHANGELOG.md` + `docs/superpowers/{specs,plans}/*.md`), all **HISTORICAL** (records of past cycles). Leave them unchanged. Confirm no source/code file appears:

```bash
git grep -nE "open\([^)]*CLAUDE\.md|Path\([^)]*CLAUDE\.md|[\"']CLAUDE\.md[\"']" -- 'src/**' 'scripts/**' 'terraform/**' || echo "no functional CLAUDE.md open — expected"
```

Expected: `no functional CLAUDE.md open`. If any **LIVE** rule-text ref (a doc pointing readers to `CLAUDE.md` as the current rulebook) is found, repoint it to `AGENTS.md` and record it here; none is expected.

- [ ] **Step 2: Re-verify not-packaged (no version bump)**

```bash
grep -nE "CLAUDE|AGENTS" pyproject.toml || echo "not in pyproject"
grep -rnE "CLAUDE\.md|AGENTS\.md" --include=*.py scripts/ || echo "no packaging script references"
```

Expected: `not in pyproject` and no build-include reference → confirm **no version bump, no CHANGELOG release entry**.

---

## Task 6: Red→green proof + full local CI gate

**Files:** none.

- [ ] **Step 1: Confirm the red→green transition is recorded**

Task 2 Step 2 captured RED (pre-restructure). Now the full module must be green:

```bash
uv run pytest src/tests/test_agents_md_budget.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 2: Run the exact CI check set locally (must all be green)**

```bash
uv run ruff check src/
uv run ruff format --check src/
uv run pyright src/
uv run pytest --cov=src --cov-report=term-missing
uv run pip-audit
uvx detect-secrets==1.5.0 scan --baseline .secrets.baseline
```

Expected: ruff/pyright/pytest green. Known-noise caveats (triage, do not mask): `pip-audit` may red on "own package not on PyPI" (pre-existing, version-keyed); `detect-secrets` may report Windows/Linux baseline path/hash diffs (do **not** regenerate the baseline to force green). If either fails, confirm the failure is pre-existing and unrelated to this docs change before proceeding.

---

## Task 7: `final-review`, then the single commit → push → PR → merge (four owner gates)

**Files:** none created.

- [ ] **Step 1: Run the `final-review` skill**

Invoke the `final-review` skill over the full change set (repo rule). Address anything it flags (drift, stale references, C4 diagram if applicable). Re-run Task 6 Step 2 if any file changed.

- [ ] **Step 2: Create the feature branch and stage the change**

```bash
git checkout -b chore/agents-md-restructure
git add AGENTS.md CLAUDE.md docs/context/formats-and-tiers.md docs/context/scripts-and-migrations.md \
        src/tests/test_agents_md_budget.py src/tests/fixtures/claude_md_at_44930cf.md \
        src/tests/fixtures/agents_md_invariants.json \
        docs/superpowers/specs/2026-09-25-agents-md-restructure-design.md \
        docs/superpowers/plans/2026-09-25-agents-md-restructure.md
git status
git diff --cached --stat
```

Show the staged file list + diffstat to the owner. **The spec and plan commit here, with the finished code — not earlier.**

- [ ] **Step 3: Commit — OWNER GATE 1**

Stop. Show the diff. Commit **only** on the owner's explicit "commit":

```bash
git commit -m "chore: restructure CLAUDE.md into AGENTS.md + context store with anti-bloat gate

Rename CLAUDE.md -> AGENTS.md (cross-tool convention) behind an @import shim;
move class-2 rationale to docs/context/{formats-and-tiers,scripts-and-migrations}.md;
add src/tests/test_agents_md_budget.py: byte budget, shim integrity, and a
migration-safety oracle (committed snapshot, 18-invariant inventory, dual-source
count pin) proving no class-1 invariant was dropped. No behaviour change; not packaged.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 4: Push — OWNER GATE 2** (separate; not implied by the commit approval)

```bash
git push -u origin chore/agents-md-restructure
```

- [ ] **Step 5: Open the PR — OWNER GATE 3** (separate)

```bash
gh pr create --title "chore: AGENTS.md restructure + anti-bloat gate" --body "$(cat <<'EOF'
Rename CLAUDE.md → AGENTS.md behind an @import shim; move class-2 to docs/context/;
add the anti-bloat + migration-safety gate (src/tests/test_agents_md_budget.py).
No behaviour change to shipped code; docs/infra only; not packaged (no version bump).

Spec: docs/superpowers/specs/2026-09-25-agents-md-restructure-design.md
Plan: docs/superpowers/plans/2026-09-25-agents-md-restructure.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Merge — OWNER GATE 4** (separate; after CI green)

Wait for CI green on the PR, then on the owner's explicit "merge":

```bash
gh pr merge --squash --admin
```

---

## Self-review (plan vs spec)

- **Spec coverage:** §1 summary → Tasks 3/4; §2 measured state → Task 1 grounding + Task 5 re-verify; §4.1 shim → Task 3 Step 2 + `test_shim_integrity`; §4.2 two context files → Task 4; §4.3 AGENTS.md content → Task 3 Step 1; §5 inventory (schema, `class1_anchor` distinctiveness, ADR-reject, `INVARIANT_COUNT` dual pin) → Task 1 + `test_inventory_schema_valid`; §6 gate (utf-8, `stat().st_size`, snapshot oracle, char cap + PINING-SPEC-01 `POINTER_RE`, completeness, PINING-SPEC-02 hard TARGET) → Task 2; §7 sweep (3 buckets, zero repoint) → Task 5; §8 scope/verbatim/zero-drop → Global Constraints + Task 4 Step 3; §9 no version bump → Task 5 Step 2; §10 local=CI → Task 6 Step 2; §11 four-gate commit discipline → Task 7. No spec requirement is unimplemented.
- **Placeholder scan:** `LANDED_BYTES` is the one deliberately-provisional value; Task 3 Step 5 pins it to a measured number with an explicit ≤ 5120 gate — not a vague TODO. All test code, the inventory JSON, and the shim are concrete.
- **Type/name consistency:** `INVARIANT_COUNT = 18 == count == len(entries)`; `POINTER_RE`/`BARE_ADR_RE` match spec §5/§6.2; fixture paths (`claude_md_at_44930cf.md`, `agents_md_invariants.json`) identical across Tasks 1/2; `test_*` names stable across Tasks 2/3/4/6.
