"""SkillCorner Open Data (github.com/SkillCorner/opendata) — pure discovery + role mapping.

The opendata match layout is the legacy id-prefixed PUBLIC artifact set the mock API already
serves for the original A-League drop: per match a SkillCorner V3 ``{id}_match.json`` metadata
file plus three bodies redistributed AS-IS — extrapolated tracking JSONL, dynamic-events CSV and
phases-of-play CSV. The metadata schema is the V3 shape, so
``formats.skillcorner_bundle.players_from_meta`` is reused unchanged by the adapter to derive
players.

Pure functions only (no I/O): ``scripts/upload_skillcorner_opendata.py`` injects the HTTP/S3
boundary. This is the public-tier peer of ``formats.skillcorner_raw`` (owner tier). It does NOT
reuse ``skillcorner_bundle.match_info`` because that converts the kickoff to Europe/Madrid local
time — correct for the owner-tier Real Madrid data but wrong for A-League (Australia/NZ) and, more
importantly, a mismatch with the UTC calendar date the original 10 public entries already carry.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass

# (role, filename-suffix) for the four opendata files per match. The staged filename keeps the
# id-prefixed opendata basename so ``upload_game`` derives the exact legacy artifact keys
# ({id}_match, {id}_tracking_extrapolated, {id}_dynamic_events, {id}_phases_of_play) already used
# by the original 10 public matches (ADR 0008: the legacy skillcorner provider uses id-prefixed
# keys; the wire format is out-of-band of the role key).
OPENDATA_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("match", "_match.json"),
    ("tracking_extrapolated", "_tracking_extrapolated.jsonl"),
    ("dynamic_events", "_dynamic_events.csv"),
    ("phases_of_play", "_phases_of_play.csv"),
)


@dataclass(frozen=True)
class OpenDataMatchInfo:
    """Index metadata derived from one opendata ``{id}_match.json``."""

    match_id: str
    date: str  # UTC calendar date (date_time[:10]) — matches the existing public entries
    home: str
    away: str


def opendata_files(match_id: str) -> dict[str, str]:
    """Map each role to its source path relative to the opendata ``data/`` root."""
    return {role: f"matches/{match_id}/{match_id}{suffix}" for role, suffix in OPENDATA_SUFFIXES}


def _sorted_ids(ids: Iterable[str]) -> list[str]:
    """Sort match ids numerically when all-numeric (SkillCorner ids), else lexicographically."""
    out = list(ids)
    return sorted(out, key=int) if all(s.isdigit() for s in out) else sorted(out)


def discover_match_ids(index: list[dict]) -> list[str]:
    """Sorted string match ids from the opendata ``data/matches.json`` index."""
    return _sorted_ids(str(m["id"]) for m in index)


def select_new_matches(all_ids: list[str], live_ids: set[str]) -> list[str]:
    """Sorted opendata ids not already present in the live index (idempotent skip)."""
    return _sorted_ids(set(all_ids) - set(live_ids))


def _team_label(team: dict | None) -> str | None:
    if not team:
        return None
    return team.get("short_name") or team.get("name")


def match_info(meta: dict) -> OpenDataMatchInfo:
    """Derive index metadata from an opendata ``{id}_match.json``. Raises on missing fields.

    ``date`` is the UTC calendar date (the ``date_time`` prefix), reproducing the convention of
    the original 10 public entries exactly — see the module docstring for why no timezone
    conversion is applied.
    """
    match_id = meta.get("id")
    home = _team_label(meta.get("home_team"))
    away = _team_label(meta.get("away_team"))
    date_time = meta.get("date_time")
    if match_id is None or not home or not away or not date_time:
        raise ValueError("missing required meta fields (need id, date_time, home_team, away_team)")
    if "T" not in date_time or len(date_time) < 10:
        raise ValueError(f"unexpected date_time format: {date_time!r}")
    return OpenDataMatchInfo(match_id=str(match_id), date=date_time[:10], home=home, away=away)


# --- Git-LFS resolution helpers -------------------------------------------------------------------
# The opendata repo LFS-tracks every ``*.jsonl`` (``.gitattributes: *.jsonl filter=lfs``), so
# raw.githubusercontent serves a 133-byte pointer, not the blob. These pure helpers let the loader
# and the backfill detect a pointer, read its content-addressed identity, and prove a resolved blob
# is byte-identical to upstream (the redistribution contract).

_LFS_MAGIC = b"version https://git-lfs.github.com/spec/v1"
_LFS_OID_RE = re.compile(r"^oid sha256:([0-9a-f]{64})$", re.MULTILINE)
_LFS_SIZE_RE = re.compile(r"^size (\d+)$", re.MULTILINE)


def is_lfs_pointer(body: bytes) -> bool:
    """True if ``body`` is a Git-LFS pointer file rather than the real blob.

    raw.githubusercontent.com serves this 133-byte text for any LFS-tracked path; the opendata
    repo LFS-tracks every ``*.jsonl`` (``.gitattributes: *.jsonl filter=lfs``).
    """
    return body.startswith(_LFS_MAGIC)


def parse_lfs_pointer(body: bytes) -> tuple[str, int]:
    """Extract ``(oid_sha256_hex, size)`` from a Git-LFS pointer body.

    Raises ValueError if either the ``oid sha256:<hex>`` or ``size <int>`` line is absent/malformed.
    """
    text = body.decode("utf-8", errors="replace")
    oid_m = _LFS_OID_RE.search(text)
    size_m = _LFS_SIZE_RE.search(text)
    if not oid_m or not size_m:
        raise ValueError("malformed LFS pointer (need 'oid sha256:<64-hex>' and 'size <int>')")
    return oid_m.group(1), int(size_m.group(1))


def verify_blob(body: bytes, oid: str, size: int) -> None:
    """Assert a resolved blob matches the pointer's content-addressed identity.

    The pointer's ``oid`` IS the sha256 of the real content, so this proves byte-parity with
    upstream — the redistribution contract. Raises ValueError on size or sha256 mismatch
    (truncation, corruption, LFS-bandwidth error page, or wrong object).
    """
    if len(body) != size:
        raise ValueError(f"LFS blob size mismatch: got {len(body)}, expected {size}")
    digest = hashlib.sha256(body).hexdigest()
    if digest != oid:
        raise ValueError(f"LFS blob sha256 mismatch: got {digest}, expected {oid}")


def public_opendata_ids(matches: list[dict]) -> list[str]:
    """Sorted ids of public entries carrying a ``*_tracking_extrapolated`` artifact.

    Used by the backfill to select the live A-League matches whose tracking object may be an
    unresolved LFS pointer. ``_sorted_ids`` keeps numeric SkillCorner ids in numeric order.
    """
    ids = [
        str(m["id"])
        for m in matches
        if m.get("visibility") == "public" and any("tracking_extrapolated" in k for k in (m.get("artifacts") or {}))
    ]
    return _sorted_ids(ids)
