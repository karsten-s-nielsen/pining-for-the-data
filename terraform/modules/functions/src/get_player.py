"""GET /v1/{provider}/players/{id} — fetch a single player record."""

from __future__ import annotations

import os

from shared import (
    Tier,
    get_s3_client,
    json_response,
    logger,
    provider_known,
    read_player_index,
    validate_path_param,
    validate_token,
)

BUCKET = os.environ.get("DATA_BUCKET", "")


def handler(event: dict, context: object) -> dict:
    """Return a single player record. 404 if provider unknown, player not found,
    or found-but-private-and-public-tier.

    Spec §6.4: gates on providers.json membership.
    Spec §6.3.1: on cross-tier ID collision, owner-tier sees the private record.
    """
    tier = validate_token(event)
    if isinstance(tier, dict):
        logger.warning("auth_failure", extra={"handler": "get_player"})
        return tier

    params = event.get("pathParameters") or {}
    provider = params.get("provider", "")
    player_id = params.get("id", "")

    for name, value in [("provider", provider), ("id", player_id)]:
        param_error = validate_path_param(value, name)
        if param_error:
            logger.warning("validation_failure", extra={"handler": "get_player", "param": name})
            return param_error

    s3 = get_s3_client()
    if not provider_known(s3, BUCKET, provider):
        return json_response(404, {"error": "Provider not found"})

    # Owner tier: try PRIVATE index first so a cross-tier ID collision returns
    # the private record (spec §6.3.1: private wins).
    if tier == Tier.OWNER:
        private_players = read_player_index(s3, BUCKET, f"{provider}/_private/players.json")
        found = next((p for p in private_players if p.get("id") == player_id), None)
        if found is not None:
            return json_response(200, found)

    # Fall through to public index for both tiers.
    public_players = read_player_index(s3, BUCKET, f"{provider}/players.json")
    found = next((p for p in public_players if p.get("id") == player_id), None)
    if found is not None:
        return json_response(200, found)

    return json_response(404, {"error": "Player not found"})
