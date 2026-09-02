"""Raw-JSON-family SkillCorner reader (Champions League 25/26, Premier League 25/26).

Pure discovery + role-mapping for the raw layout. The metadata schema is identical to the
parquet family (``formats.skillcorner_bundle.match_info`` / ``players_from_meta`` are reused).
The ``dynamic_events/*.json`` files are actually CSV (mislabelled ``.json``) — content is
handled downstream by ``formats.skillcorner_canonical.events_csv_to_parquet``; ``physical`` is
a single combined file, not per-match, so it is not part of the per-match role map.

See docs/superpowers/specs/2026-09-01-skillcorner-canonical-parquet-format-design.md §6.2.
"""

from __future__ import annotations

# role -> (source subdir, extension) for the raw-JSON family
RAW_ROLE_LAYOUT: dict[str, tuple[str, str]] = {
    "metadata": ("matches", ".json"),
    "tracking": ("tracking", ".json"),
    "events": ("dynamic_events", ".json"),  # actually CSV; content-sniffed downstream
}


def discover_manifest_matches(manifest_csv: bytes) -> list[str]:
    """Match ids from ``available_dynamic_event_match_ids.csv``, skipping the header row."""
    lines = manifest_csv.decode("utf-8").splitlines()
    return [line.strip() for line in lines[1:] if line.strip()]


def raw_role_files(match_id: str) -> dict[str, str]:
    """Map each per-match role to its source-relative path in the raw layout."""
    return {role: f"{sub}/{match_id}{ext}" for role, (sub, ext) in RAW_ROLE_LAYOUT.items()}
