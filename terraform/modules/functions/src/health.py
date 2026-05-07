"""GET /v1/health -- unauthenticated health check."""

from __future__ import annotations

from shared import json_response


def handler(event: dict, context: object) -> dict:
    """Return 200 OK. No auth required. Used for synthetic monitoring."""
    return json_response(200, {"status": "ok"})
