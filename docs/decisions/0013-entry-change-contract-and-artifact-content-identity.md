# 0013 — Entry Change Contract and Artifact Content Identity

## Status

Accepted

## Date

2026-10-26

## Context

Consumers of the mock provider API poll `{provider}/matches` and download artifacts. They need
two answers before they spend a download: did this entry change, and did this artifact's bytes
change?

The old contract could not answer either. `updated_at` was set by the upload tooling on every
write, including a byte-identical re-upload, so a re-run of a loader marked every entry as
changed. No entry carried a content hash, so a consumer had to download an artifact to learn
that it had not changed. That contract came from the private-data-tier design
(`docs/superpowers/specs/2026-05-02-private-data-tier.md:159` and `:641`), which deferred
per-artifact SHA-256 "until a consumer asks".

The lakehouse v2 ingestion design is that consumer. It also groups matches by competition and
season, and those values were not in the index. So the deferral's own condition is met.

Two constraints shape the answer:

- **Honesty (ADR 0010).** A value the source does not give stays absent. A value is never
  inferred, and a name is never copied into an id or an id into a name.
- **Reproducible staging.** A content hash is useful only if the same source gives the same
  bytes on every run. Three loaders gzipped with `gzip.open`, which writes the current time and
  the file name into the gzip header.

## Decision

1. **Competition and season fields.** Every match entry carries `competition_id`,
   `competition_name`, `season_id` and `season_name`, read from the match's own metadata
   artifact by one set of value rules: an integer id becomes its decimal string; a non-empty
   string is kept verbatim; `None` or `""` means absent; any other type (`float`, `bool`,
   `list`, `dict`) is refused loudly. Each field reads only its own source path. Gradient
   Sports gives a season value but no season id, so its entries carry `season_name` and no
   `season_id`.
2. **Artifact content identity.** Every match entry carries
   `artifact_meta: {<artifact key>: {"sha256": <hex>, "size": <bytes>}}`, computed from the bytes
   stored in S3. Its keys equal the `artifacts` keys. ETags and S3 checksums are never used.
3. **The change contract.** `updated_at` changes only when the record changes: any field, or the
   `sha256` of any artifact. A byte-identical re-upload keeps the stored `updated_at`. The same
   contract applies to player records (without the artifact part). Clients should compare each
   artifact's `artifact_meta.sha256` with their stored copy before they download it.
4. **Validation before any write.** `upload_game` builds and validates the entry, including the
   hashes, before its first S3 write, so a refused value leaves the objects and the index
   unchanged.
5. **Reproducible staging.** Both `_gzip_file` functions write a fixed gzip header (`mtime` 0, no
   file name). Every loader stages byte-identical files from the same source.
6. **Backfill.** A one-shot script sets the new fields and hashes on every live entry, in two
   passes: pass 1 reads, checks and hashes everything and writes nothing (any per-entry error
   stops the run); pass 2 writes each provider's index with `IfMatch`.

## Consequences

**Positive**

- A consumer can poll with `updatedSince` and skip unchanged entries, then skip unchanged
  artifacts by comparing `sha256`. A loader re-run no longer marks the whole provider as changed.
- Competition and season grouping needs no inference on the consumer side, and the absence of a
  value is meaningful.
- A wrong metadata artifact is caught: the backfill and the verify script check that the metadata
  match id equals the entry id.

**Negative**

- The first re-upload of a stored StatsBomb `.json.gz` artifact after this change stores the new
  fixed-header bytes, so its hash and `updated_at` change once.
- Every upload hashes its staged files, and the backfill and `--full-hash` verify read every stored
  byte (13.68 GB at the time of the backfill).
- The `matches.json` entries are larger (the SkillCorner index grows from about 0.5 MB to about
  0.9 MB, well under the 6 MB Lambda response limit).

**Reversal cost:** low for readers (all new fields are optional and additive; the schema `$id`
stays `matches:v1`). Restoring "refresh on every write" would break consumers that rely on the
new contract, so it would need a new ADR.

## Alternatives Considered

- **`updated_at` on every write (the old contract).** Rejected: it makes `updated_at` useless for
  incremental refresh after any re-run.
- **S3 ETags as content identity.** Rejected: a multipart upload's ETag is not a hash of the
  object bytes, and some stored objects are multipart.
- **S3 additional checksums.** Rejected: a multipart upload gives a composite SHA-256, not the hash
  of the object bytes, and objects written without a checksum have none.
- **Infer missing competition or season values** (for example derive a season id from a season
  name). Rejected by ADR 0010: an inferred value is presented as the provider's.

## See Also

- Spec: `docs/superpowers/specs/2026-10-26-match-competition-season-and-artifact-identity-design.md`
- ADR 0010 (faithful-feed mimicry; the honesty rule)
- `src/formats/competition_season.py` (value rules), `src/mock_api/artifact_meta.py` (hashing),
  `src/mock_api/index_records.py` (the change contract)
- `scripts/backfill_match_competition_and_artifact_meta.py`, `scripts/verify_match_index_fields.py`
- `docs/api-reference.md`, "List Matches" and "List Players"
