# 0015 — SkillCorner Open Data: commit pin, `source.revision`, and the conditional re-sync

## Status

Accepted

## Date

2026-10-27

## Context

The public SkillCorner Open Data loader (`scripts/upload_skillcorner_opendata.py`) fetched the redistributed artifacts from the upstream `master` branch, so the bytes it served floated to whatever the upstream repository held at fetch time. The loader also skips every already-live match id, so a file that changed upstream after its first ingest never reached the store. Ten of the twenty live public entries therefore stored older `dynamic_events` and `phases_of_play` than upstream, and no entry recorded which upstream commit produced it. The public store was neither reproducible nor current.

## Decision

- **Pin the redistribution to a named upstream commit.** The loader fetches from a single `UPSTREAM_COMMIT` (`4340d274572876239c154c90bc507a9b3250a656`, 2026-09-14) on the raw host, the Git-LFS media host, and the index fetch. The pin is a module constant; moving it is a deliberate one-line change plus a re-run, never a floating ref.

- **Record the commit in `source.revision`.** `_SourceMeta` (shared by `MatchEntry` and `PlayerRecord`) gains an optional `revision` field, a 40-hex git SHA-1. `upload_game` writes it through the existing `source` gate. The `provenance` string stays `"redistributed"`, so every consumer that reads `provenance` is unaffected (Hyrum's law). Both JSON Schemas are regenerated.

- **Re-sync drifted entries with conditional, upsert-only writes.** `scripts/resync_skillcorner_opendata.py` stamps `source.revision` on every public opendata entry and re-uploads only a `dynamic_events` or `phases_of_play` object whose sha256 differs from the pin. An unexpected `match`/`tracking_extrapolated` drift, an entry with no `source`, or an upstream `404` is reported and that one match is stopped — never a silent re-upload, never a delete. Each artifact PUT is conditional on its ETag (`If-Match`); the index is written per match with the ETag re-read each iteration, so a `412` aborts only that match. Every other entry field is carried verbatim: only `artifact_meta`, `source.revision` and `updated_at` may change.

- **Prove the result from the stored bytes.** `scripts/verify_skillcorner_opendata_resync.py` downloads and hashes every stored artifact of every public entry and asserts its sha256 and size equal both the pinned upstream file and the entry's `artifact_meta`. The `tracking_extrapolated` pin identity is the upstream Git-LFS pointer `oid` (no upstream blob download), but its stored bytes are hashed in full — the `--full-hash` bar (ADR 0013).

## Consequences

- The public store is reproducible: every entry names the commit it came from, and the loader no longer floats.
- A future upstream change is adopted by bumping `UPSTREAM_COMMIT` and re-running the re-sync under the owner's go, not automatically. A vanished upstream file is reported and kept, never deleted.
- Stamping `source.revision` on all twenty entries bumps each `updated_at` once (a real metadata change, ADR 0013).
- The re-sync avoids re-downloading the ~90 MB tracking blobs by using the pointer `oid` for the drift check; the one-time verify still hashes the stored tracking bytes.

## See Also

- ADR 0013 — the `updated_at` change contract and `artifact_meta` content identity.
- ADR 0008 — the id-prefixed artifact-key vocabulary the opendata matches use.
- `docs/superpowers/specs/2026-10-27-skillcorner-opendata-resync-design.md`.
