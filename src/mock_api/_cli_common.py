"""Shared utilities for the upload CLIs (pining-upload, pining-upload-players)."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

# Same rule as the API-side validator: no leading underscore (reserved namespace).
SAFE_PARAM = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


def validate_param(value: str, name: str) -> None:
    """Raise ValueError if param is empty, too long, or contains unsafe characters.

    Leading underscore is rejected — reserved for internal namespace markers (`_private`).
    """
    if not value or len(value) > 128 or not SAFE_PARAM.match(value):
        raise ValueError(
            f"Invalid {name}: must be 1-128 characters, start with alphanumeric, "
            f"and contain only alphanumeric, hyphens, or underscores"
        )


def utc_now_iso() -> str:
    """Current UTC time, ISO 8601 with trailing Z (no microseconds)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def handle_cli_errors(parser, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run func with friendly error messages for common AWS/validation failures."""
    try:
        return func(*args, **kwargs)
    except ValueError as e:
        parser.error(str(e))
    except ImportError as e:
        if "boto3" in str(e):
            parser.error("boto3 is required. Install with: uv sync --extra aws")
        raise
    except Exception as e:
        name = type(e).__name__
        if "NoCredentialsError" in name or "CredentialRetrievalError" in name:
            parser.error("AWS credentials not configured. Run `aws configure` or set AWS_PROFILE.")
        if "NoSuchBucket" in name:
            parser.error("Bucket not found. Check `terraform output bucket_name` for the correct name.")
        raise
