"""GET /v1/{provider}/matches — list available games for a provider."""

from __future__ import annotations

import json
import os

from shared import get_s3_client, json_response, validate_path_param, validate_token

BUCKET = os.environ.get("DATA_BUCKET", "")


def handler(event: dict, context: object) -> dict:
    auth_error = validate_token(event)
    if auth_error:
        return auth_error

    provider = event.get("pathParameters", {}).get("provider", "")
    param_error = validate_path_param(provider, "provider")
    if param_error:
        return param_error

    s3 = get_s3_client()
    key = f"{provider}/matches.json"
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        matches = json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        return json_response(404, {"error": f"Provider '{provider}' not found"})

    return json_response(200, matches)
