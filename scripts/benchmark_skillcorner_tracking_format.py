"""Reproduce the tracking-format footprint figures from the canonical-format spec (§2.3/§2.5).

Reports the size of one match's tracking layer in each candidate encoding — raw JSON (the
uncompressed body as delivered), gzip (the legacy owner-tier form), Parquet+snappy, and
Parquet+zstd (the canonical form) — plus the ratio versus raw. Run it against an operator-local
tracking file (`.json` or `.json.gz`; no data is committed):

    python scripts/benchmark_skillcorner_tracking_format.py <path/to/tracking.json[.gz]>

`tracking_format_sizes` is a pure function (unit-tested on synthetic frames). See
docs/superpowers/specs/2026-09-01-skillcorner-canonical-parquet-format-design.md §2.5.
"""

from __future__ import annotations

import gzip
import io
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from formats.skillcorner_canonical import TRACKING_SCHEMA, tracking_to_parquet  # noqa: E402


def tracking_format_sizes(tracking_bytes: bytes) -> dict[str, int]:
    """Byte size of one match's tracking in each encoding.

    ``tracking_bytes`` is the file content (gzip is transparently decompressed). ``raw_json`` is
    the uncompressed body exactly as delivered (so the figure matches the on-disk file), while
    ``gzip`` compresses that same body — reproducing the spec's raw-vs-gzip-vs-Parquet comparison.
    """
    body = gzip.decompress(tracking_bytes) if tracking_bytes[:2] == b"\x1f\x8b" else tracking_bytes
    frames = json.loads(body)
    snappy = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(frames, schema=TRACKING_SCHEMA), snappy, compression="snappy")
    return {
        "raw_json": len(body),
        "gzip": len(gzip.compress(body)),
        "parquet_snappy": len(snappy.getvalue()),
        "parquet_zstd": len(tracking_to_parquet(frames)),
    }


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/benchmark_skillcorner_tracking_format.py <tracking.json[.gz]>")
    sizes = tracking_format_sizes(Path(sys.argv[1]).read_bytes())
    raw = sizes["raw_json"]
    print(f"{'encoding':22} {'size':>12}   {'vs raw':>8}")
    for name, size in sizes.items():
        print(f"{name:22} {size / 1e6:>9.2f} MB   {raw / size:>7.1f}x")


if __name__ == "__main__":
    main()
