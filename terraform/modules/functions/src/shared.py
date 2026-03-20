"""Shared utilities for Lambda handlers."""

from __future__ import annotations

import hmac
import json
import os
import re

import boto3
from botocore.config import Config

# Force SigV4 for presigned URLs (required for KMS-encrypted buckets)
_s3_config = Config(signature_version="s3v4")
_s3_client = None


def get_s3_client():
    """Get a shared S3 client configured for SigV4 signing."""
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", config=_s3_config)
    return _s3_client


def validate_token(event: dict) -> dict | None:
    """Validate bearer token from Authorization header.

    Returns None if valid, or an error response dict if invalid.
    """
    token = os.environ.get("API_TOKEN", "")
    if not token:
        return _error_response(500, "Server misconfiguration")
    headers = event.get("headers") or {}
    # API Gateway may or may not lowercase header names
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return _error_response(401, "Missing or malformed Authorization header")
    if not hmac.compare_digest(auth[7:], token):
        return _error_response(401, "Invalid token")
    return None


# Strict allowlist for path parameters — prevents path traversal
_SAFE_PARAM = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_path_param(value: str, name: str) -> dict | None:
    """Validate a path parameter against the safe character allowlist.

    Returns None if valid, or an error response dict if invalid.
    """
    if not value or not _SAFE_PARAM.match(value):
        return _error_response(400, f"Invalid {name}: must be alphanumeric, hyphens, or underscores")
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
