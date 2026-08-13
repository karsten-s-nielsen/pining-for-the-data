# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.4.0] - 2026-08-12

### Added
- Restricted commercial **StatsBomb 360** data ingested as a new **owner-tier** provider (`statsbomb`, `visibility="private"`, `provenance="original"`) — served only to the owner bearer token; public consumers get a uniform `404` with no existence leak. Not redistributable. No Lambda or Terraform change (the API is already tier- and provider-generic).
- `src/formats/statsbomb.py` — pure, stdlib-only reader for the club file-drop delivery. Detects and de-pivots the `statsbombpy` pandas column-orient dumps (raw feed arrays flow through the same entry point unchanged), joins the entitlement row on statsbombpy's rendered `"{country} - {competition}"` string plus season name, resolves home/away team ids from the event stream by **exact** name equality (delivery order is never a fallback — an inverted fixture is indistinguishable from a correct one downstream), derives gender per team from the lineups, and re-nests the flattened match row into StatsBomb's real match object shape with dropped fields emitted as `null`.
- Fail-loud pre-flight (`assert_delivery_coherent`) that refuses an incoherent delivery **before a byte is staged**: zero freeze frames, zero lineup entries, **per-team squads that are empty or absent**, freeze frames referencing unknown event ids, fewer than two periods, no `Half End` event, a distinct-team count other than two, or lineups and events disagreeing on which two teams played. There is deliberately no `--force` flag — a blanket override would disable every check at once.
- The empty-per-team-squad guard closes a gap the two neighbouring checks bracketed without covering: a delivery whose team blocks are present but whose per-team `lineup` arrays are all empty satisfies the zero-lineups guard (the outer array is non-empty) *and* the lineups-vs-events team cross-check (the `team_id`s are still there). `players_from_lineups` then returns `[]`, the catalogue upload is skipped by its `if players:` guard, and the match is indexed with an empty roster, no player catalogue and **no error** — the exact failure the zero-lineups guard exists to prevent.
- Role-aligned artifact keys `events` / `freeze_frames` / `roster` / `metadata` (ADR 0008). The multi-megabyte `events` and `freeze_frames` bodies are stream-gzipped before upload; `roster` is staged verbatim (the proprietary per-player rating block survives intact) and `metadata` is synthesised by the reader.
- `scripts/upload_statsbomb_club.py` — owner-tier worked adapter: pre-flight, stage the four artifacts into a fresh temp dir, call `pining-upload` at the private tier, then derive and upload the player catalogue. Every fallible step (competition join, team resolution, index-field derivation, `PlayerRecord` validation) runs before the first write, so a bad delivery cannot leave an indexed match without a catalogue. Source root read from `$STATSBOMB_RESTRICTED_DIR`.
- `scripts/verify_statsbomb_load.py` — owner+public post-load verification: owner sees the restricted matches, artifacts and players; public gets a uniform `404`; every entry carries a date (a dateless entry is invisible to `dateFrom`/`dateTo`); the served artifact key set is asserted against the role vocabulary; large bodies validated via a `Range: bytes=0-0` GET. Identifiers sampled from the live response (no licensed ids committed). Every check records into `failures` and lets the run continue — including the metadata JSON decode and every nested dereference in the feed-shape check, so a malformed artifact cannot abort the loop and take the remaining artifacts' public-token `404` leak checks down with it — and a missing metadata body is reported rather than silently skipping the feed-shape assertions. The feed-shape check resolves each section's type before testing membership: the obvious spelling (`"competition_id" not in md.get("competition", {})`) raises `TypeError` on a scalar section *and* silently degrades to a **substring match** on a string one, so `"competition": "competition_id"` would have been reported as correctly nested.
- ADR 0010 — **faithful-feed mimicry**: a provider-native substructure served by this API represents the *provider's* format, not the format of the pipeline that happened to deliver our copy. Structure may be reconstructed; values may never be invented (a dropped field is `null`, never a plausible-looking synthesised id). Inference from *inside* the delivery is permitted and may be made to raise on contradiction; inference from *outside* it is forbidden. Scope is explicitly the content of provider-native structures — the artifact envelope stays ADR 0008's.

### Changed
- `scripts/_verify_http.py` — shared HTTP helpers (`parse_content_range_total`, `get_json`, `get_json_with_status`, `NoFollow`) extracted from all **four** post-load verify scripts, which now import them instead of each carrying a copy. `verify_gradient_load.py` needs the HTTP status alongside the body, so `get_json_with_status` is the one implementation that builds the request and decodes the body, and `get_json` is a thin accessor over it that drops the status — a superset accommodated rather than two request paths free to drift apart. The presigned-fetch functions are deliberately **left per-script**: IDSSE's raises on a failed fetch, whereas the owner-tier scripts must treat a public-token `404` as a passing post-condition, so a single shared implementation would have to be parameterised into something less clear than the ones it replaces.
- Test count: 210 → 333 (+102 new unit tests for the provider, +7 for the shared verify-HTTP helpers, +14 for the StatsBomb verify script's metadata feed-shape check; fully invented fixtures only — no licensed id, and no real person's attributes, are committed).
- Documentation: README, ARCHITECTURE.md, CLAUDE.md, `docs/api-reference.md` and the ADR index updated for the new provider; C4 architecture diagram regenerated (now including the restricted-SkillCorner orchestrator + verify scripts).
- `docs/api-reference.md` now states the absent-vs-null contract on player records: `pining-upload-players` serialises with `model_dump(exclude_none=True)`, so an unset optional field is **omitted** from the served JSON rather than null-valued, and consumers must test for absence (`"firstName" in record`) — testing for a null value raises `KeyError` against any provider that leaves a field unset.
- ADR 0008 amended with an **Amendments** section recording the `freeze_frames` / `physical` vocabulary extension that ADR 0009 introduced in v0.3.0 but never wrote back to the vocabulary ADR itself, so the canonical list is now complete where a consumer actually looks it up; plus cross-references to ADR 0009 and ADR 0010 (which governs artifact *content*, whereas 0008 governs the *envelope*).
- `docs/decisions/README.md`'s ADR immutability rule widened in the same change, so the governance doc and the amended artifact do not contradict each other. A decision *change* is still a new ADR and never an edit; what is now permitted is a narrow, three-condition exception — **additive only**, **non-revisionary** (original decision text left intact), and **attributed** (naming the ADR that introduced the extension) — for recording a later ADR's extension of a vocabulary or contract the original defines. The rationale is discoverability: a vocabulary whose canonical list is scattered across the ADRs that happened to extend it is a contract nobody can look up.
- `_NoFollow` renamed to `NoFollow` in `scripts/_verify_http.py`. It is imported by name across module boundaries by four scripts, so a leading underscore advertised a privacy the export does not have. The *module* keeps its `_verify_http` name — a private module with public members is the accurate shape.
- `CLAUDE.md`'s `scripts/` inventory regrouped by role and completed — it previously read as exhaustive while omitting the three completed migration scripts and the figshare manifest.
- C4 Level 2 split from one `include *` container view into a runtime view plus per-concern and per-provider slices. The combined view had reached 31 containers / **41 element nodes** — far past the ~15-per-view readability guideline, and it degraded further with every provider added, since each brings an orchestrator *and* a verify script. This is a **views-only** change: the model is untouched (same containers, same relationships, same deployment instances). `Containers` now carries just the runtime request path (consumers → API Gateway → Lambdas → S3/SSM/KMS, 13 nodes); `Containers_Toolchain` (14) holds the shared CLIs, canonical models, schemas and the HuggingFace publishing path; `Containers_{GradientSports,IDSSE,SkillCornerRestricted,StatsBomb}` (8-9 each) hold one ingestion path apiece, so a new provider now costs one new slice rather than two more boxes on a wall chart; `Containers_Platform` (8) holds the storage, encryption, audit and observability substrate. Every container still renders in at least one view.
- C4 view keys `GetArtifactComponents` and `PrivateArtifactDownload` renamed to `Component_lambdaArtifact` and `Dynamic_PrivateArtifactDownload`. Neither matched the `Component_<containerId>` / `Dynamic_<flowId>` convention, so each claimed its own top-level tab group — advertising two architectural levels that do not exist. They now nest under the canonical **Components** and **Dynamic** groups. No file deep-links a C4 tab anchor, so no inbound reference breaks.
- `scripts/verify_gradient_load.py` — removed a dead `import urllib.parse`. Ruff cannot flag it: the statement binds the name `urllib`, which the module genuinely uses (for `urllib.request` and `urllib.error`), so the unused submodule import is invisible to F401.

## [0.3.0] - 2026-06-29

### Added
- Restricted SkillCorner Real Madrid tracking data ingested as **owner-tier** content under the existing `skillcorner` provider (`visibility="private"`, `provenance="original"`) — served only to the owner bearer token; public consumers see only the redistributed open data. No new provider slug and no Lambda/Terraform change (the API is already tier/provider-generic).
- `src/formats/skillcorner_bundle.py` — pure, stdlib-only reader for the SkillCorner multi-artifact bundle (Soccermatics-course distribution). Parses only the small `meta/*.json` for index metadata (`date` derived in `Europe/Madrid` local time) and derives the owner-tier player catalogue from the self-contained `meta.players` lists; the large tracking/events/freeze/physical bodies are never parsed.
- Role-aligned artifact keys `tracking` / `events` / `freeze_frames` / `metadata` / `physical` (ADR 0008); `freeze_frames` and `physical` registered in the shared role vocabulary. `tracking` JSON is gzip-compressed (streamed) before upload; the reproducible `velocities` artifact is excluded.
- `scripts/upload_skillcorner_realmadrid.py` — owner-tier worked adapter: stages each match's artifacts into a fresh temp dir, calls `pining-upload` at the private tier, then derives + uploads the player catalogue, **skipping (and reporting) any player id already present in the public tier** so a cross-tier collision never aborts the load. Source root read from `$SKILLCORNER_RESTRICTED_DIR`.
- `scripts/verify_skillcorner_realmadrid_load.py` — owner+public post-load verification: owner sees the restricted matches/artifacts/players, public gets a uniform `404` (no existence leak), large `tracking` validated via a `Range: bytes=0-0` GET. Identifiers sampled from the live response (no licensed ids committed).
- ADR 0009 — restricted data under an existing public provider (tier dimension over a new slug; `meta`-as-index-source over the mismatched `matches.parquet`; cross-tier player-collision skip-and-report).

### Changed
- Test count: 187 → 210 (+24 new unit tests; synthetic fixtures only).
- Documentation: README (owner-tier note), CLAUDE.md (new reader + scripts), and the ADR index updated.

### Security
- Bumped transitive `pip-audit` tooling deps to clear newly-disclosed advisories: `msgpack` 1.1.2 → 1.2.1 (GHSA-6v7p-g79w-8964, via `cachecontrol`) and `pip` 26.1.1 → 26.1.2 (PYSEC-2026-196, via `pip_api`).

## [0.2.0] - 2026-05-29

### Removed
- `get_artifact` legacy array-form fallback (and its 2 regression tests). All deployed `matches.json` entries are now in canonical object form, so the dead code path is gone.

### Migrated
- 10 SkillCorner matches in `skillcorner/matches.json` migrated from legacy array-form `artifacts: ["..."]` to canonical object-form `artifacts: {name: filename}`, `visibility: "public"` and `updated_at` added, `source.license` (American) renamed to `source.licence` (British, per spec §8.2.1). One-shot script `scripts/backfill_skillcorner_artifacts.py` is idempotent and uses S3 `IfMatch=<etag>` for optimistic concurrency control.

### Added
- IDSSE/Sportec open Bundesliga as a new **public** provider (`idsse`): 7 matches of raw DFL XML (matchinformation / events / positions at 25 fps), redistributed as-is under CC-BY 4.0 with DFL/Sportec authorization, served through the existing provider API. New `src/formats/idsse.py` reader parses only the ~12 KB matchinformation XML for index metadata (`date` derived in `Europe/Berlin` local time); positions/events are served byte-for-byte. `scripts/upload_idsse_bundesliga.py` fetches the version-pinned figshare release (`/versions/1`), verifies it against a committed md5 manifest (`scripts/idsse_figshare_manifest.json`, regenerable via `--write-manifest`), and bulk-loads via `pining-upload`. `scripts/verify_idsse_load.py` runs size-aware post-load checks (full GET for `metadata`, `Range: bytes=0-0` GET for the large `events`/`tracking`). Role-aligned artifact keys `metadata` / `events` / `tracking` (ADR 0008).
- ADR 0008 — role-aligned artifact-key vocabulary across providers (`metadata`/`events`/`tracking`; SkillCorner's id-prefixed keys documented as the legacy exception).
- Two-tier auth on the mock provider API: PUBLIC tier (existing `api_token`) and OWNER tier (SSM Parameter Store SecureString). `validate_token` returns a `Tier` enum; tier mismatch returns uniform `404` to avoid existence leaks (no 403). Duplicate-token misconfiguration classifies as PUBLIC (fail closed).
- Match-level visibility flag (`public` / `private`) with reserved `_private/` S3 prefix for tier separation (defense in depth alongside the application-layer tier check).
- New `/v1/{provider}/players` and `/v1/{provider}/players/{id}` reference resource endpoints, with provider-gated 404 and private-wins precedence on cross-tier ID collision.
- Canonical Pydantic models (`MatchEntry`, `PlayerRecord`) in `src/canonical/models.py` as the schema source of truth; published JSON Schemas in `schemas/` (URN `$id`, Draft 2020-12 `$schema`); drift-tested in CI via `scripts/regenerate_schemas.py`. Models live outside the Lambda source dir so the Lambda zip stays dependency-free (no pydantic at runtime).
- `pining-upload --visibility public|private` flag with cross-tier mixing rejection.
- `pining-upload-players` CLI accepting canonical JSON only (CSV explicitly rejected with reference to `scripts/upload_gradient_wc2022.py` adapter).
- `updated_at` ISO 8601 UTC timestamp on every match and player entry, refreshed on every write (drives consumer incremental refresh).
- Object-form `artifacts: {name: filename}` in `matches.json` (replaces array form). `get_artifact` resolves filenames via dict lookup with no per-request `list_objects_v2`; the keys form the API's allowlist.
- CloudTrail data events on the data bucket (`terraform/modules/audit/`), landing in a separate audit bucket with 365-day retention and SSE-KMS. Only `/providers.json` reads are excluded from the trail; `/matches.json` and `/players.json` reads stay logged.
- `LAST_ROTATION` env var on all 6 Lambdas — bumped via `terraform apply -var=last_rotation=...` to invalidate the warm-container `_get_owner_token` cache during a rotation.
- `scripts/upload_gradient_wc2022.py` — orchestrator for bulk-loading Gradient Sports FIFA World Cup 2022 as private-tier data (no licence gate; single-owner private-tier load is data movement within the operator's own systems).
- `scripts/verify_gradient_load.py` — automated post-load verification (counts, visibility leak checks, content-agnostic spot-check sampling).
- ADRs 0001-0006 covering owner-token storage, `_private/` convention, resource-noun endpoints, CloudTrail audit, single-bucket multi-tier prefix isolation, and canonical-models-outside-Lambda placement.
- British `--source-licence` is the canonical CLI flag spelling on both upload CLIs; American `--source-license` accepted as a quiet alias.

### Changed
- Documentation: README, ARCHITECTURE.md, CLAUDE.md, and docs/api-reference.md updated to reflect the new two-tier auth, `/players` resource, audit logging, and infrastructure additions.
- Test count: 64 → 187.
- C4 architecture diagram regenerated to include the new Lambdas, audit module, SSM Parameter Store, KMS, the `canonical/` package, and the IDSSE provider (orchestrator + verify script + external source).

### Fixed (deploy-time hardening)
- `get_artifact` accepts both legacy array-form `artifacts: [...]` and current object-form `artifacts: {name: filename}`. Legacy entries (uploaded pre-Task-8) fall back to per-request S3 list; object-form entries skip listing entirely. Two regression tests cover the legacy path so it doesn't bit-rot.
- `reserved_concurrent_executions` removed from all 5 pre-existing Lambdas (was = 5); the health Lambda was created without it. Account limit is 10 ConcurrentExecutions with 10-min unreserved required; reserving any concurrency on this account fails the API call. Existing 3 Lambdas had no reservation in production despite TF claiming = 5 (silent drift); new declaration brings TF state into alignment with reality.
- CloudTrail `advanced_event_selector` field selectors merged: CloudTrail rejects two `field_selector` blocks for the same field. Combined `starts_with` + `not_ends_with` on `resources.ARN` into a single block.
- `verify_gradient_load.py` corrected to FIFA WC 2022 actual counts: 64 matches (not 67 — standard tournament size) and 829 unique player IDs (Gradient Sports CSV has ~2321 (player, team) rows that dedupe by `id`). The script's `_follow_redirect` now strips the `Authorization: Bearer` header on the 302 follow to S3 — S3 rejects presigned URLs with a stray bearer as conflicting auth.
- Docs (CHANGELOG, CLAUDE.md, ARCHITECTURE.md, api-reference, spec §6.6) updated to reference `src/canonical/models.py` as the canonical model location.

### Operational milestones (this branch)
- Dev stack deployed end-to-end: SSM owner token set, audit module + CloudTrail provisioned, all 6 Lambdas live (5 data-tier with both tokens + unauthenticated health).
- Gradient Sports FIFA WC 2022 successfully bulk-loaded into the private tier: 64 matches (256 artifact files) + 829 unique player records. `scripts/verify_gradient_load.py` post-conditions all pass: counts correct, zero visibility leaks, 20/20 artifact downloads via presigned URLs, 5/5 player spot-checks, public-tier 404 on private artifacts.
- IDSSE Bundesliga bulk-loaded into the **public** tier on dev: 7 matches (21 artifacts, ~2.63 GB). `scripts/verify_idsse_load.py` post-conditions all pass: 7 matches, `/providers` includes `idsse`, `dateFrom`/`dateTo` filter parity, and size-aware artifact checks (`metadata` 200 + body; `events`/`tracking` 206 + positive `Content-Range` total without downloading the 418 MB positions XML).

## [0.1.0] - 2026-03-20

### Added
- SkillCorner V3 format reader and writer (`pining-ingest` CLI)
- Automated HuggingFace Hub publishing (`pining-publish` CLI)
- De-identification engine with synthetic roster generation (`pining-generate-roster` CLI)
- Mock provider REST API on AWS (S3 + API Gateway + Lambda)
- Upload CLI for mock API data management (`pining-upload` CLI)
- Terraform modules for full infrastructure deployment
- 10 A-League Men matches redistributed in SkillCorner V3 format
- ARCHITECTURE.md with C4 diagrams
- CI pipeline (ruff, pyright, pytest) via GitHub Actions

[Unreleased]: https://github.com/karsten-s-nielsen/pining-for-the-data/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/karsten-s-nielsen/pining-for-the-data/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/karsten-s-nielsen/pining-for-the-data/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/karsten-s-nielsen/pining-for-the-data/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/karsten-s-nielsen/pining-for-the-data/releases/tag/v0.1.0
