"""Shared utilities for Lambda handlers."""

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
from pydantic import BaseModel, ConfigDict, Field, model_validator

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


# ----- Canonical Pydantic models (spec §6.6) -----


class _SourceMeta(BaseModel):
    """Provenance metadata for an upstream data source."""

    name: str
    url: str = ""
    licence: str = ""


class MatchEntry(BaseModel):
    """Canonical shape of a single entry in `{provider}/matches.json`. Spec §4.1."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "$id": "urn:pining-for-the-data:schema:matches:v1",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    id: str = Field(..., pattern=_PATH_PARAM_RE, max_length=128)
    artifacts: dict[str, str] = Field(
        ...,
        description=(
            "Map of artifact-name to exact filename. "
            "Keys form the API whitelist; each key MUST match the path-param regex."
        ),
    )
    visibility: str = Field(..., pattern=r"^(public|private)$")
    updated_at: str = Field(..., description="ISO 8601 UTC timestamp")
    date: str | None = None
    home: str | None = None
    away: str | None = None
    provenance: str | None = None
    source: _SourceMeta | None = None

    @model_validator(mode="after")
    def _validate_artifact_keys(self) -> MatchEntry:
        # Every artifact name must satisfy the same regex as path params,
        # so the upload tool cannot land entries the API will refuse to serve.
        # Spec §5.2.
        regex = re.compile(_PATH_PARAM_RE)
        for name in self.artifacts.keys():
            if not regex.match(name) or len(name) > 128:
                raise ValueError(
                    f"artifact name {name!r} does not match the path-param regex {_PATH_PARAM_RE} (max 128 chars)"
                )
        return self


class PlayerRecord(BaseModel):
    """Canonical shape of a single entry in `{provider}/players.json`. Spec §6.3."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "$id": "urn:pining-for-the-data:schema:players:v1",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    id: str = Field(..., pattern=_PATH_PARAM_RE, max_length=128)
    visibility: str = Field(..., pattern=r"^(public|private)$")
    updated_at: str = Field(..., description="ISO 8601 UTC timestamp")
    firstName: str | None = None
    lastName: str | None = None
    nickname: str | None = None
    dob: str | None = None
    height: float | None = None
    position: str | None = None
    positionGroupType: str | None = None
    nationality: str | None = None
    source: _SourceMeta | None = None

    @model_validator(mode="after")
    def _require_a_name(self) -> PlayerRecord:
        if not (self.nickname or (self.firstName and self.lastName)):
            raise ValueError("PlayerRecord requires either nickname OR (firstName AND lastName)")
        return self
