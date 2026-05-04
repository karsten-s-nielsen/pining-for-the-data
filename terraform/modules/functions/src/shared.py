"""Shared utilities for Lambda handlers.

The Pydantic canonical models (`MatchEntry`, `PlayerRecord`) live in
`src/canonical/models.py` (outside this directory) so they are NOT bundled
into the Lambda zip — Lambda handlers consume already-validated dict payloads
from S3 and never instantiate the models. Keeping the models out of this
module means the Lambda runtime stays dependency-free (no pydantic).
"""

from __future__ import annotations

import functools
import hmac
import json
import logging
import os
import re
from enum import StrEnum

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)

# Force SigV4 for presigned URLs (required for KMS-encrypted buckets)
_s3_config = Config(signature_version="s3v4")
_s3_client = None
_ssm_client = None


def get_s3_client():
    """Get a shared S3 client configured for SigV4 signing."""
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", config=_s3_config)
    return _s3_client


def _get_ssm_client():
    """Get a shared SSM client. Lazy-initialised."""
    global _ssm_client
    if _ssm_client is None:
        _ssm_client = boto3.client("ssm")
    return _ssm_client


@functools.cache
def _get_owner_token() -> str:
    """Fetch the owner-tier token from SSM Parameter Store.

    Cached for the lifetime of the warm Lambda container. Rotation requires
    bumping the LAST_ROTATION env var via terraform apply (spec §3.5).
    """
    param_name = os.environ["OWNER_TOKEN_PARAM"]
    response = _get_ssm_client().get_parameter(Name=param_name, WithDecryption=True)
    return response["Parameter"]["Value"]


class Tier(StrEnum):
    PUBLIC = "public"
    OWNER = "owner"


def validate_token(event: dict) -> Tier | dict:
    """Validate bearer token from Authorization header.

    Returns ``Tier.PUBLIC`` or ``Tier.OWNER`` on success, or an error response
    dict on failure. If both tokens are the same string (operator
    misconfiguration), classifies as ``PUBLIC`` — fail closed. Spec §3.2.
    """
    public_token = os.environ.get("API_TOKEN", "")
    if not public_token:
        return _error_response(500, "Server misconfiguration")

    headers = event.get("headers") or {}
    # API Gateway may or may not lowercase header names
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return _error_response(401, "Missing or malformed Authorization header")

    presented = auth[7:]

    try:
        owner_token = _get_owner_token()
    except Exception:
        logger.exception("owner_token_fetch_failed")
        return _error_response(500, "Server misconfiguration")

    # Compare against PUBLIC first so a duplicate-token misconfiguration
    # classifies as PUBLIC (fail closed; spec §3.2). The more restrictive
    # failure mode: break the owner consumer visibly rather than silently
    # leak private content to public-token holders.
    if hmac.compare_digest(presented, public_token):
        return Tier.PUBLIC
    if hmac.compare_digest(presented, owner_token):
        return Tier.OWNER
    return _error_response(401, "Invalid token")


# Strict allowlist for path parameters: alphanumeric, hyphen, underscore — but
# no leading underscore. Leading `_` is reserved for tier and namespace markers
# (e.g., `_private`). Spec §5.2.
_PATH_PARAM_RE = r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"
_SAFE_PARAM = re.compile(_PATH_PARAM_RE)


def validate_path_param(value: str, name: str) -> dict | None:
    """Validate a path parameter against the safe character allowlist.

    Rejects empty, too long, or values starting with ``_`` (reserved for
    internal namespace markers like ``_private``).

    Returns None if valid, or an error response dict if invalid.
    """
    if not value or len(value) > 128 or not _SAFE_PARAM.match(value):
        return _error_response(
            400,
            f"Invalid {name}: must start with alphanumeric; use only alphanumeric, hyphens, or underscores",
        )
    return None


def json_response(status_code: int, body: dict) -> dict:
    """Build API Gateway proxy response."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }


def redirect_response(url: str) -> dict:
    """Build 302 redirect response."""
    return {
        "statusCode": 302,
        "headers": {
            "Location": url,
            "Access-Control-Allow-Origin": "*",
        },
        "body": "",
    }


def _error_response(status_code: int, message: str) -> dict:
    return json_response(status_code, {"error": message})
