"""Canonical Pydantic models for the mock provider API's index entries.

These models are the schema source of truth for `matches.json` and
`players.json`. They are NOT bundled into the Lambda zip — they're consumed by
the upload CLIs (which validate before any S3 write) and by the schema
regeneration script. JSON Schemas in `schemas/` are generated from these
models via `scripts/regenerate_schemas.py` and drift-tested in `test_schemas.py`.

Spec §6.6.

Why this lives outside `terraform/modules/functions/src/`: the Lambda runtime
(Python 3.12 on AWS Lambda) does not include pydantic, and adding it via a
Lambda layer is more operational surface than the handlers need — they
consume already-validated dict payloads from S3, never instantiate the models
themselves. Keeping the models out of the Lambda source dir means the Lambda
zip stays dependency-free and small.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Same regex used by the API path-param validator in
# terraform/modules/functions/src/shared.py — kept here as a separate constant
# rather than imported from shared.py because the canonical models must not
# depend on the Lambda runtime module.
_PATH_PARAM_RE = r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"


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
        for name in self.artifacts:
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
