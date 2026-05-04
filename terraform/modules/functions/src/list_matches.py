"""GET /v1/{provider}/matches — list available games for a provider, filtered by tier."""

from __future__ import annotations

import json
import os

from shared import Tier, get_s3_client, json_response, logger, validate_path_param, validate_token

BUCKET = os.environ.get("DATA_BUCKET", "")


def handler(event: dict, context: object) -> dict:
    """Return the matches index for a provider, filtered by caller's tier.

    Spec §4.2: PUBLIC tier sees only entries with visibility == "public" (or
    missing, treated as public). OWNER tier sees all entries. Empty filtered
    list returns 200 with `{"matches": []}` — not 404 — so the public tier
    cannot probe for the existence of any private matches.
    """
    tier = validate_token(event)
    if isinstance(tier, dict):
        logger.warning("auth_failure", extra={"handler": "list_matches"})
        return tier

    provider = (event.get("pathParameters") or {}).get("provider", "")
    param_error = validate_path_param(provider, "provider")
    if param_error:
        logger.warning("validation_failure", extra={"handler": "list_matches", "param": "provider"})
        return param_error

    s3 = get_s3_client()
    key = f"{provider}/matches.json"
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        matches = json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        return json_response(404, {"error": "Provider not found"})
    except Exception:
        logger.exception("s3_error", extra={"handler": "list_matches"})
        return json_response(500, {"error": "Internal server error"})

    if tier != Tier.OWNER:
        # Filter to public entries (missing `visibility` field defaults to public).
        filtered = [m for m in matches.get("matches", []) if m.get("visibility", "public") == "public"]
        matches = {**matches, "matches": filtered}

    return json_response(200, matches)
