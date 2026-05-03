"""GET /v1/providers — list supported tracking data providers."""

from __future__ import annotations

import json
import os

from shared import get_s3_client, json_response, logger, validate_token

BUCKET = os.environ.get("DATA_BUCKET", "")


def handler(event: dict, context: object) -> dict:
    """Return the provider index by reading ``providers.json`` from S3.

    Tier-blind: returns the same list to both PUBLIC and OWNER tiers.
    Existence of a provider is not the secret; the per-match visibility
    flag is the only enforcement boundary. Spec §4.2.
    """
    tier = validate_token(event)
    if isinstance(tier, dict):
        logger.warning("auth_failure", extra={"handler": "list_providers"})
        return tier

    s3 = get_s3_client()
    try:
        obj = s3.get_object(Bucket=BUCKET, Key="providers.json")
        providers = json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        providers = {"providers": []}
    except Exception:
        logger.exception("s3_error", extra={"handler": "list_providers"})
        return json_response(500, {"error": "Internal server error"})

    return json_response(200, providers)
