"""GET /v1/{provider}/matches/{id}/{artifact} — serve a tracking artifact."""

from __future__ import annotations

import json
import os

from shared import (
    Tier,
    get_s3_client,
    json_response,
    logger,
    redirect_response,
    validate_path_param,
    validate_token,
)

BUCKET = os.environ.get("DATA_BUCKET", "")
PRESIGNED_EXPIRY = int(os.environ.get("PRESIGNED_EXPIRY", "3600"))


def handler(event: dict, context: object) -> dict:
    """Resolve an artifact by name and return a presigned S3 URL via 302 redirect.

    Reads ``{provider}/matches.json`` once to determine the match's visibility
    and the artifact's filename, enforces tier check (404 on mismatch — uniform
    with not-found to avoid existence leaks), then generates the presigned URL
    directly. No ``list_objects_v2`` and no ``head_object`` — the index is the
    source of truth for the file's existence (the upload tool wrote both
    atomically). Spec §4.3.
    """
    tier = validate_token(event)
    if isinstance(tier, dict):
        logger.warning("auth_failure", extra={"handler": "get_artifact"})
        return tier

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

    # Look up the match in matches.json to determine visibility AND filename.
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=f"{provider}/matches.json")
        matches_data = json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        return json_response(404, {"error": "Match not found"})
    except Exception:
        logger.exception("s3_error", extra={"handler": "get_artifact", "stage": "matches_lookup"})
        return json_response(500, {"error": "Internal server error"})

    match = next((m for m in matches_data.get("matches", []) if m.get("id") == match_id), None)
    if match is None:
        return json_response(404, {"error": "Match not found"})

    visibility = match.get("visibility", "public")
    if visibility == "private" and tier != Tier.OWNER:
        # Uniform 404 with not-found — no existence leak.
        return json_response(404, {"error": "Match not found"})

    # Spec §4.3: artifacts is an object {name: filename}; keys form the whitelist,
    # values resolve the file. Legacy entries (uploaded before Task 8) have an
    # array of names instead — fall back to per-request S3 list for those, until
    # they're re-uploaded into the object form. This keeps existing public data
    # serving without forcing a one-shot migration.
    artifacts = match.get("artifacts")
    prefix_root = f"{provider}/_private/{match_id}" if visibility == "private" else f"{provider}/{match_id}"

    if isinstance(artifacts, dict):
        filename = artifacts.get(artifact)
        if filename is None:
            return json_response(404, {"error": "Artifact not found"})
        key = f"{prefix_root}/{filename}"
    elif isinstance(artifacts, list):
        # Legacy array form: name must be in the list, then S3-list to find the file
        if artifact not in artifacts:
            return json_response(404, {"error": "Artifact not found"})
        try:
            response = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"{prefix_root}/{artifact}", MaxKeys=5)
            matching = [
                obj["Key"]
                for obj in response.get("Contents", [])
                if obj["Key"].rsplit("/", 1)[-1].rsplit(".", 1)[0] == artifact
            ]
            if not matching:
                return json_response(404, {"error": "Artifact not found"})
            key = matching[0]
        except Exception:
            logger.exception("s3_error", extra={"handler": "get_artifact", "stage": "legacy_list"})
            return json_response(500, {"error": "Internal server error"})
    else:
        return json_response(404, {"error": "Artifact not found"})

    try:
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET, "Key": key},
            ExpiresIn=PRESIGNED_EXPIRY,
        )
    except Exception:
        logger.exception("s3_error", extra={"handler": "get_artifact", "stage": "presign"})
        return json_response(500, {"error": "Internal server error"})

    return redirect_response(url)
