"""GET /v1/providers — list supported tracking data providers."""

from __future__ import annotations

import json
import os

from shared import get_s3_client, json_response, validate_token

BUCKET = os.environ.get("DATA_BUCKET", "")


def handler(event: dict, context: object) -> dict:
    auth_error = validate_token(event)
    if auth_error:
        return auth_error

    s3 = get_s3_client()
    try:
        obj = s3.get_object(Bucket=BUCKET, Key="providers.json")
        providers = json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        providers = {"providers": []}

    return json_response(200, providers)
