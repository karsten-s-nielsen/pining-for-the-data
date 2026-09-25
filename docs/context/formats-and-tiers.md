# Formats & tiers — rationale

> Class-2 context for the `## Architecture` (`src/formats/`) and `## Mock Provider API` sections of [AGENTS.md](../../AGENTS.md). The enforceable invariants live there; this file holds the why.

## `src/formats/` — provider format families

Provider format readers/writers (SkillCorner V3 JSON/JSONL, SkillCorner multi-artifact bundle + raw-JSON family for restricted owner-tier data, IDSSE/Sportec DFL XML, StatsBomb commercial 360 club bundle + open-data tournaments family (`statsbomb_open.py`) for restricted owner-tier data, Respo.Vision JSON future). Owner-tier SkillCorner is stored as the canonical columnar Parquet/zstd set (nested `tracking.parquet`, `events`/`physical` Parquet, freeze dropped, per-match `format_version` marker — ADR 0011; pure transforms in `skillcorner_canonical.py`). The two StatsBomb source families share one faithful-feed gzip-JSON shape (ADR 0010) and one artifact vocabulary; the open family (`statsbomb_open.py`) is the purest ADR-0010 case (the real published feed — no de-pivot/join/resolution) and is served `provenance="redistributed"` (ADR 0012). The **public** A-League tier stays in its native V3 shape (ADR 0011 keeps the public tier out of the canonical Parquet set): `skillcorner_opendata.py` is the pure discovery/role-mapping for the SkillCorner Open Data repo (`github.com/SkillCorner/opendata`, MIT) — the public-tier peer of `skillcorner_raw.py` — redistributing the legacy id-prefixed four-artifact set (`{id}_match.json`, `{id}_tracking_extrapolated.jsonl`, `{id}_dynamic_events.csv`, `{id}_phases_of_play.csv`) as-is.

## Mock Provider API — owner-tier providers & provenance

Owner-tier providers: `gradientsports` (Gradient Sports), `skillcorner` private tier (restricted Real Madrid), `statsbomb` (commercial 360 club delivery, `provenance="original"` — ADR 0010; plus open-data tournaments, `provenance="redistributed"` — ADR 0012). Both StatsBomb source families are owner-tier only; `provenance` distinguishes them.
