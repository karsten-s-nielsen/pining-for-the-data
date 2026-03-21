"""GET /v1/{provider}/matches — list available games for a provider."""

from __future__ import annotations

import json
import os

from shared import get_s3_client, json_response, logger, validate_path_param, validate_token

BUCKET = os.environ.get("DATA_BUCKET", "")


def handler(event: dict, context: object) -> dict:
    auth_error = validate_token(event)
    if auth_error:
        logger.warning("auth_failure", extra={"handler": "list_matches"})
        return auth_error

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

    return json_response(200, matches)
