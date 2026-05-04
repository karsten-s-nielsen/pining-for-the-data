"""GET /v1/{provider}/players — list players for a provider, filtered by tier."""

from __future__ import annotations

import json
import os

from shared import (
    Tier,
    get_s3_client,
    json_response,
    logger,
    validate_path_param,
    validate_token,
)

BUCKET = os.environ.get("DATA_BUCKET", "")


def handler(event: dict, context: object) -> dict:
    """Return the player catalogue for a provider, merged across visible tiers.

    Gates on providers.json membership for unknown-provider 404 (spec §6.4).
    Owner-tier merge applies private-wins precedence on cross-tier ID
    collision (spec §6.3.1).
    """
    tier = validate_token(event)
    if isinstance(tier, dict):
        logger.warning("auth_failure", extra={"handler": "list_players"})
        return tier

    provider = (event.get("pathParameters") or {}).get("provider", "")
    param_error = validate_path_param(provider, "provider")
    if param_error:
        logger.warning("validation_failure", extra={"handler": "list_players", "param": "provider"})
        return param_error

    s3 = get_s3_client()
    if not _provider_known(s3, provider):
        return json_response(404, {"error": "Provider not found"})

    public_players = _read_index(s3, f"{provider}/players.json")
    private_players = _read_index(s3, f"{provider}/_private/players.json") if tier == Tier.OWNER else []

    # Private-wins precedence on cross-tier ID collision (spec §6.3.1).
    by_id: dict[str, dict] = {}
    for pub in public_players:
        pid = pub.get("id")
        if isinstance(pid, str):
            by_id[pid] = pub
    for priv in private_players:
        pid = priv.get("id")
        if isinstance(pid, str):
            by_id[pid] = priv  # overwrite any same-id public entry

    return json_response(200, {"provider": provider, "players": list(by_id.values())})


def _provider_known(s3, provider: str) -> bool:
    """Check that `provider` appears in providers.json. Returns True if so."""
    try:
        obj = s3.get_object(Bucket=BUCKET, Key="providers.json")
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return provider in (data.get("providers") or [])
    except s3.exceptions.NoSuchKey:
        return False
    except Exception:
        logger.exception("s3_error", extra={"handler": "list_players", "key": "providers.json"})
        return False


def _read_index(s3, key: str) -> list[dict]:
    """Read a players index from S3. Returns [] if the index doesn't exist."""
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return data.get("players", [])
    except s3.exceptions.NoSuchKey:
        return []
    except Exception:
        logger.exception("s3_error", extra={"handler": "list_players", "key": key})
        return []
