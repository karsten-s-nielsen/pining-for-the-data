# ADR 0006 — Canonical Pydantic Models Live Outside the Lambda Source Directory

**Status:** Accepted
**Date:** 2026-05-03
**Context:** Private data tier deploy; Lambda runtime hardening (no spec section — emerged at deploy time)

## Context

The first deploy of the private-data-tier Lambda code returned `500` on every endpoint. The root cause: `terraform/modules/functions/src/shared.py` imported pydantic at module level. The AWS Lambda Python 3.12 runtime does not bundle pydantic, so the import failed at cold-start, propagating to a 500 from every handler that imported `shared`.

The canonical `MatchEntry` and `PlayerRecord` models had been placed in `shared.py` for proximity to the Lambda handlers, on the assumption "shared utilities and schemas live together." The deploy made the cost of that proximity concrete: bundling pydantic into every Lambda zip (or a Lambda Layer) for models the handlers don't actually instantiate.

## Decision

Move the canonical Pydantic models out of the Lambda source dir into a new top-level package: **`src/canonical/models.py`**. Keep `terraform/modules/functions/src/shared.py` for runtime utilities only (Tier enum, `validate_token`, `validate_path_param`, response builders, S3 client, SSM owner-token fetcher).

Lambda handlers consume already-validated dict payloads from S3 (the upload CLIs validate before any S3 write); they never instantiate `MatchEntry` or `PlayerRecord` themselves. The Lambda zip therefore stays dependency-free — no pydantic, no third-party packages beyond what AWS Lambda ships natively (boto3, botocore).

The upload CLIs (`pining-upload`, `pining-upload-players`), the schema regeneration script, and the test suite import the models via the standard package layout: `from canonical.models import MatchEntry, PlayerRecord`. The package is registered in `pyproject.toml`'s wheel build target.

## Consequences

**Positive**
- Lambda zip stays small and dependency-free. Cold-start is fast; deploy footprint is minimal.
- No Lambda Layer to maintain (no second build artifact, no version coordination across Lambdas).
- Clear separation of concerns: `terraform/modules/functions/src/` = "Lambda runtime code"; `src/canonical/` = "schema source of truth, consumed by writers and tools".
- Sets the pattern for future schemas: any new canonical model goes into `src/canonical/`, NOT `terraform/modules/functions/src/`.
- Pydantic stays a dev-time + write-time dependency only, never enters the Lambda runtime.

**Negative**
- Two import paths coexist in the codebase (Lambda code vs. application code).
- A reader new to the project may initially expect models to live next to handlers; the docstring at the top of `models.py` explains why they don't.
- If a future requirement DOES need Pydantic in the Lambda runtime (e.g., dynamic schema validation on read), this decision is reversible but requires either a Lambda Layer or vendoring pydantic into the handler zip.

**Reversal cost**: low. If pydantic ends up needed at runtime, add a Lambda Layer (one Terraform resource, one `pip install --target` step in the build) and either move or re-import the models. The directory structure decision doesn't lock anything in beyond the import paths.

## Alternatives Considered

- **Lambda Layer for pydantic**: rejected — adds a build artifact and Terraform resource for a dependency the handlers don't need. Justifiable only if the handlers themselves want Pydantic for runtime validation, which they don't (validation is the upload-tool's job).
- **Pre-installed via `pip install --target` into the Lambda src dir**: rejected — pollutes the Lambda src dir with vendored dependencies, complicates the `archive_file` build, and is fragile to platform differences in pip's Windows vs. Linux output.
- **Try/except the pydantic import in `shared.py`**: considered briefly as a quick patch — rejected because it leaves the architectural ambiguity unresolved (models defined in a runtime-shared file, conditionally usable). The cleaner fix is to put the models where they belong.
- **Public Lambda Layer for pydantic (community-maintained)**: rejected — supply-chain dependency on a third party for a dependency the handlers don't even want.

## See Also

- `docs/superpowers/specs/2026-05-02-private-data-tier.md` §6.6 — schema lifecycle (updated 2026-05-03 to reflect the model location)
- `src/canonical/models.py` — the models themselves, with a docstring explaining the placement
- `terraform/modules/functions/src/shared.py` — Lambda runtime utilities (no pydantic import)
- `scripts/regenerate_schemas.py` — generates `schemas/*.json` from the canonical models
- `pyproject.toml` — wheel package registration for `canonical/`
