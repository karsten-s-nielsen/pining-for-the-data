"""GET /v1/{provider}/matches/{id}/{artifact} — serve a tracking artifact."""

from __future__ import annotations

import os

from shared import get_s3_client, json_response, logger, redirect_response, validate_path_param, validate_token

BUCKET = os.environ.get("DATA_BUCKET", "")
PRESIGNED_EXPIRY = int(os.environ.get("PRESIGNED_EXPIRY", "3600"))


def handler(event: dict, context: object) -> dict:
    auth_error = validate_token(event)
    if auth_error:
        logger.warning("auth_failure", extra={"handler": "get_artifact"})
        return auth_error

    params = event.get("pathParameters") or {}
    provider = params.get("provider", "")
    match_id = params.get("id", "")
    artifact = params.get("artifact", "")

    for name, value in [("provider", provider), ("id", match_id), ("artifact", artifact)]:
        param_error = validate_path_param(value, name)
        if param_error:
            logger.warning("validation_failure", extra={"handler": "get_artifact", "param": name})
            return param_error

    s3 = get_s3_client()
    try:
        # Scan for artifact file by prefix
        prefix = f"{provider}/{match_id}/{artifact}"
        response = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix, MaxKeys=5)

        contents = response.get("Contents", [])
        # Filter to files that match artifact name exactly (not just prefix substring)
        matching = [
            obj["Key"]
            for obj in contents
            if obj["Key"].rsplit("/", 1)[-1].rsplit(".", 1)[0] == artifact
        ]

        if not matching:
            return json_response(404, {"error": "Artifact not found"})

        key = matching[0]
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET, "Key": key},
            ExpiresIn=PRESIGNED_EXPIRY,
        )
    except Exception:
        logger.exception("s3_error", extra={"handler": "get_artifact"})
        return json_response(500, {"error": "Internal server error"})

    return redirect_response(url)
