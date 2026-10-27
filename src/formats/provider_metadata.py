"""Read the match identity from a stored metadata artifact, for any provider and tier (spec §5.2).

The backfill and the live verify read the stored metadata object of an index entry and need the
same answer the upload path computed: the metadata match id (to prove the right artifact was read)
and the competition/season identity. Both go through the provider's own pure reader, so the
§5.3 value rules apply exactly once.

Pure functions only (no I/O): callers fetch the bytes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree as ET

from formats import gradientsports, idsse, skillcorner_bundle, skillcorner_opendata, statsbomb
from formats.competition_season import CompetitionSeason


@dataclass(frozen=True)
class MetadataIdentity:
    """What a metadata artifact says about its match."""

    match_id: str
    competition: CompetitionSeason


def metadata_artifact_key(provider: str, entry: Mapping[str, Any]) -> str:
    """The artifact key that holds an entry's metadata.

    ``"metadata"`` for every provider, except public ``skillcorner``, whose native id-prefixed keys
    (ADR 0011) put the metadata in the single key that ends in ``_match``.
    """
    artifacts = entry.get("artifacts") or {}
    if "metadata" in artifacts:
        return "metadata"
    if provider == "skillcorner":
        match_keys = [k for k in artifacts if k.endswith("_match")]
        if len(match_keys) != 1:
            raise ValueError(f"entry {entry.get('id')!r}: expected exactly one '_match' artifact key, got {match_keys}")
        return match_keys[0]
    raise ValueError(f"entry {entry.get('id')!r}: no 'metadata' artifact key")


def _json(body: bytes) -> Any:
    return json.loads(body.decode("utf-8"))


def read_metadata_identity(provider: str, visibility: str, body: bytes) -> MetadataIdentity:
    """Parse a stored metadata artifact with the provider's reader. Raises on an unknown provider."""
    info: Any
    if provider == "gradientsports":
        info = gradientsports.match_info(_json(body))
    elif provider == "idsse":
        info = idsse.read_match_information_bytes(body)
    elif provider == "skillcorner" and visibility == "public":
        info = skillcorner_opendata.match_info(_json(body))
    elif provider == "skillcorner":
        info = skillcorner_bundle.match_info(_json(body))
    elif provider == "statsbomb":
        info = statsbomb.match_info(_json(body))
    else:
        raise ValueError(f"no metadata reader for provider {provider!r}")
    return MetadataIdentity(match_id=info.match_id, competition=info.competition)


def raw_id_values(provider: str, visibility: str, body: bytes) -> dict[str, object]:
    """The raw id values of a stored metadata artifact, with their source types (spec §5.3, §5.9).

    The backfill reports the type of every id it reads, so a ``float`` id (a pandas upcast) shows
    up in the dry-run before any write. ``visibility`` is accepted for parity with
    ``read_metadata_identity``; both SkillCorner tiers use the same V3 paths.
    """
    if provider == "gradientsports":
        obj = _json(body)
        meta = (obj[0] if obj else {}) if isinstance(obj, list) else obj
        return {"match_id": meta.get("id"), "competition_id": (meta.get("competition") or {}).get("id")}
    if provider == "idsse":
        # S314: the bytes are our own bucket's copy of the trusted matchinformation artifact.
        general = ET.fromstring(body).find("./MatchInformation/General")  # noqa: S314
        if general is None:
            raise ValueError("no MatchInformation/General element")
        return {
            "match_id": general.get("MatchId"),
            "competition_id": general.get("CompetitionId"),
            "season_id": general.get("SeasonId"),
        }
    if provider == "skillcorner":
        meta = _json(body)
        edition = meta.get("competition_edition") or {}
        return {
            "match_id": meta.get("id"),
            "competition_id": (edition.get("competition") or {}).get("id"),
            "season_id": (edition.get("season") or {}).get("id"),
        }
    if provider == "statsbomb":
        meta = _json(body)
        return {
            "match_id": meta.get("match_id"),
            "competition_id": (meta.get("competition") or {}).get("competition_id"),
            "season_id": (meta.get("season") or {}).get("season_id"),
        }
    raise ValueError(f"no metadata reader for provider {provider!r}")
