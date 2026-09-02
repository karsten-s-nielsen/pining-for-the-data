# 0011 — Canonical Owner-Tier SkillCorner Format (Columnar Parquet/zstd)

## Status

Accepted

## Date

2026-09-01

## Context

Owner-tier SkillCorner data reached S3 in two divergent shapes: the restricted Real Madrid
matches as `tracking.json.gz` + snappy Parquet (`events`/`physical`/`freeze_frames`) +
`metadata.json`, and future raw-JSON-family deliveries (Champions League 25/26, Premier League
25/26) as JSON metadata + CSV events + JSON tracking + a single combined physical file. A match
is ~140 MB, ~97 % of which is uncompressed tracking JSON. Two goals: shrink that footprint, and
converge the divergent layouts on one canonical format.

Investigation established that the tracking layer dominates size and is highly compressible
(146 MB raw → ~4.6 MB as nested Parquet/zstd, smaller than JSON at any zstd level and columnar),
and that `freeze_frames` is a byte-identical, event-synchronised *slice* of continuous tracking
(positions verified equal) — regenerable from tracking + events, unlike StatsBomb 360 where
freeze frames are the only spatial data.

## Decision

The canonical **owner-tier** SkillCorner artifact set per match is:

- `metadata.json` — JSON (nested/hierarchical, small; unchanged),
- `tracking.parquet` — a **nested, fixed-schema** Parquet (zstd), one row per frame, lossless
  under a defined per-column-type round-trip equivalence,
- `events.parquet` — Parquet (zstd),
- `physical.parquet` — Parquet (zstd), optional.

`freeze_frames` is **no longer produced** for owner-tier SkillCorner. The artifact **role keys**
(`metadata`/`tracking`/`events`/`physical`) are unchanged; only the tracking wire format moves
from gzip-JSON to Parquet — permitted because ADR 0008 makes the wire format out-of-band of the
role key. Each `matches.json` entry carries an optional integer `format_version` (canonical = 2;
absent = legacy), so consumers dispatch across the mixed-format migration window without
byte-sniffing.

Scope: owner tier only. The **public** redistributed SkillCorner tier (legacy id-prefixed
CSV/JSONL) is left in its native format.

## Consequences

- **Footprint:** a match drops from ~140 MB to a few MB (tracking ~4.6 MB), stored columnar.
- **Amends the owner-tier format stance** recorded in ADR 0009 and the 2026-06-29 RM design spec
  ("format normalization: none — the tracking body is never parsed, staged as-is"): owner-tier
  tracking is now parsed and columnarised. The tier/provider decision of ADR 0009 is unchanged.
- **`freeze_frames` remains a valid ADR-0008 vocabulary key** — StatsBomb (ADR 0010) still emits
  it as its only spatial layer; only SkillCorner owner-tier stops emitting it. No amendment to
  the ADR 0008 vocabulary is required.
- **Faithful-archive stance retained** (ADR 0010 lineage): every tracking field is preserved,
  including `possession` and `image_corners_projection`, not reshaped to any consumer's narrower
  schema. Values are unchanged; the CSV→Parquet conform uses a SAFE cast (raises on any lossy
  conversion) against a pinned, committed reference schema.
- **Consumers** (e.g. luxury-lakehouse) retool their tracking reader from gzip-JSON to Parquet
  and schema-evolve events for newer-season columns. This is a Hyrum's-Law break by design,
  accepted with the consumer's owner.
- **Migration risk** is bounded by a stored-object safety gate: no owner-tier object is
  overwritten or deleted until its replacement has been re-fetched from S3 and verified.
- **Public tier untouched**, avoiding a Hyrum's-Law break on existing public consumers and
  keeping the MIT redistribution a "pure redistribution" rather than an "adaptation".

## Alternatives Considered

- **Recompress tracking JSON with zstd (keep JSON).** Rejected: only competitive at max zstd
  level (slow), stays a non-columnar blob, and Parquet+snappy already beats JSON+zstd-19.
- **Flat or two-table tracking layout.** Rejected: both are larger than nested at equal fidelity
  (4.88 / 4.81 MB vs 4.63 MB), and nested is the most source-faithful and preserves player-array
  order/structure natively.
- **Keep `freeze_frames`.** Rejected: it is a redundant slice of tracking; retaining it fights the
  "one canonical format" goal and costs storage for zero new information.
- **Converge the public tier too.** Rejected: breaks existing public consumers (Hyrum's Law) and
  turns a pure redistribution into an adaptation.

## See Also

- Spec: `docs/superpowers/specs/2026-09-01-skillcorner-canonical-parquet-format-design.md`
- ADR 0008 (role-aligned artifact-key vocabulary; wire format out-of-band of the role key)
- ADR 0009 (restricted tier under an existing public provider; format stance amended here)
- ADR 0010 (faithful-feed mimicry; the archive-fidelity lineage)
- ADR 0002 / 0005 (the `_private/` prefix + single-bucket tier isolation the migration operates within)
- Implementation: `src/formats/skillcorner_canonical.py`, `src/formats/skillcorner_raw.py`,
  `scripts/migrate_skillcorner_tracking_parquet.py`, `scripts/upload_skillcorner_raw.py`
