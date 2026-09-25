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

REPO_ROOT = Path(__file__).resolve().parents[2]  # src/tests/ -> repo root
AGENTS = REPO_ROOT / "AGENTS.md"
SHIM = REPO_ROOT / "CLAUDE.md"
CONTEXT_DIR = REPO_ROOT / "docs" / "context"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SNAPSHOT = FIXTURES / "claude_md_at_44930cf.md"
INVENTORY = FIXTURES / "agents_md_invariants.json"

# --- byte budgets (bytes on disk, never len(text)) ---
LANDED_BYTES = 4930  # measured AGENTS.md size on disk at migration (Task 3 Step 5)
TARGET = 5120  # cut-verification bound: LANDED_BYTES must be <= this
CEILING = int(LANDED_BYTES * 1.15)  # future-bloat brake
CONTEXT_FILE_FLOOR = 800
CONTEXT_TOTAL_FLOOR = 3072
PER_BULLET_CHAR_CAP = 600
POINTER_BULLET_THRESHOLD = 250  # long bullets must delegate to ADR/docs (PINING-PLAN-01):
#   250 not lakehouse's 120 — a self-contained class-1 bullet that names a src/ path
#   (e.g. `schemas/` ~215 chars, cites src/canonical/models.py, no docs/ADR pointer) would
#   false-fail at 120. The hard LANDED<=5120 TARGET is the real anti-bloat backstop; this is
#   the anti-prose-creep belt-and-braces.

# --- anti-shrink pin: dual-source with the fixture's own "count" ---
INVARIANT_COUNT = 18  # == inventory["count"] == len(entries); a fixture-only shrink fails against this

# pointer forms accepted on a long AGENTS.md bullet (PINING-SPEC-01):
#   ADR 0011 / ADR-0010 ; docs/decisions/... ; docs/context/... ; any docs/*.md
POINTER_RE = re.compile(r"ADR[- ]?\d{3,4}|docs/decisions/|docs/context/|docs/[\w./-]+\.md")
# a symbols[] entry that is ONLY a bare ADR ref (either form) is rejected:
BARE_ADR_RE = re.compile(r"^ADR[- ]?\d{3,4}$")

STOPWORDS = {
    "the",
    "a",
    "an",
    "of",
    "to",
    "in",
    "on",
    "and",
    "or",
    "is",
    "are",
    "be",
    "for",
    "with",
    "that",
    "this",
    "it",
    "as",
    "at",
    "by",
    "so",
}


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")  # utf-8 explicit: 120 non-ASCII bytes


def _bullets(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.lstrip().startswith("- ")]


def test_shim_integrity():
    lines = [ln for ln in _read(SHIM).splitlines() if ln.strip() and not ln.lstrip().startswith("<!--")]
    assert lines == ["@AGENTS.md"], f"CLAUDE.md must be a pure @AGENTS.md shim; got {lines}"


def test_agents_md_byte_budget():
    # cut-verification (PINING-SPEC-02): landed over TARGET => class-2 not fully moved.
    assert LANDED_BYTES <= TARGET, f"LANDED {LANDED_BYTES} B over TARGET {TARGET} B"
    size = AGENTS.stat().st_size
    assert size <= CEILING, f"AGENTS.md {size} B exceeds CEILING {CEILING} B (LANDED x1.15)"


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
    assert data["count"] == len(entries) == INVARIANT_COUNT, (
        f"count pin mismatch: json={data['count']} entries={len(entries)} const={INVARIANT_COUNT}"
    )
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
    every distinctive symbol survives in AGENTS.md + context files."""
    data = json.loads(_read(INVENTORY))
    agents_text = _read(AGENTS)
    context_text = "\n".join(_read(f) for f in sorted(CONTEXT_DIR.glob("*.md")))
    haystack = agents_text + "\n" + context_text
    for e in data["entries"]:
        assert e["class1_anchor"] in agents_text, (
            f"class1_anchor missing from AGENTS.md: {e['id']} :: {e['class1_anchor']!r}"
        )
        for sym in e["symbols"]:
            assert sym in haystack, f"symbol dropped (not in AGENTS.md + context): {e['id']} :: {sym!r}"


def test_inventory_grounded_in_snapshot():
    """The inventory is authored from the real pre-change file, not fabricated."""
    data = json.loads(_read(INVENTORY))
    snap = _read(SNAPSHOT)
    for e in data["entries"]:
        grounded = (e["class1_anchor"] in snap) or any(s in snap for s in e["symbols"])
        assert grounded, f"invariant not grounded in snapshot: {e['id']}"
