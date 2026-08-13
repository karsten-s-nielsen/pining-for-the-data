"""HTTP helpers shared by the post-load verification scripts.

Extracted from verify_gradient_load.py, verify_idsse_load.py,
verify_skillcorner_realmadrid_load.py and verify_statsbomb_load.py, which each
carried a copy. Only the genuinely identical pieces live here — each script keeps
its own presigned-fetch logic, because those differ in real ways (raise-vs-return
on a non-302, whether the body is returned to the caller).

scripts/ is not a package, so consumers insert their own directory on sys.path
before importing this module (the precedent set by scripts/upload_statsbomb_club.py).

The MODULE is private (leading underscore) — it is an implementation detail of
scripts/. Its members are not: they are imported by name across module boundaries,
so they carry public names.
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any


def parse_content_range_total(header: str) -> int:
    """Return the total size from a `Content-Range: bytes a-b/total` header, or -1."""
    m = re.search(r"/(\d+)\s*$", header or "")
    return int(m.group(1)) if m else -1


def get_json_with_status(api: str, path: str, token: str) -> tuple[Any, int]:
    """GET `{api}{path}` with a bearer token; return `(decoded body, HTTP status)`.

    This is the single place that builds the request and decodes the body. `get_json`
    is the thin accessor for the common case where the status carries no information
    (a non-2xx raises HTTPError before either function returns).
    """
    req = urllib.request.Request(f"{api}{path}", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8")), resp.status


def get_json(api: str, path: str, token: str) -> dict:
    """GET `{api}{path}` with a bearer token and decode the JSON body."""
    payload, _ = get_json_with_status(api, path, token)
    return payload


class NoFollow(urllib.request.HTTPRedirectHandler):
    """Do not follow the API's 302.

    urllib copies every header except Content-Length/Content-Type onto the
    redirected request, so Authorization would reach the presigned S3 URL — which
    already carries query-string auth. S3 rejects requests with two auth mechanisms.

    Returning None from redirect_request is what makes http_error_302 fall through
    to the default handler and raise HTTPError(code=302); callers catch that to read
    the Location header, then fetch the presigned URL with a clean, header-free
    request. Do not "simplify" this into an override that swallows the redirect —
    the raised HTTPError is the mechanism.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
