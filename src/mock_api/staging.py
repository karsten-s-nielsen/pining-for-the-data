"""Stage role-aligned artifacts into a directory for upload.

Extracted from scripts/upload_statsbomb_club.py so both StatsBomb source families
(commercial club-drop and open-data tournaments) stage through one path. Parameterized
by artifact specs so each family supplies its own source filenames.
"""

from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path


def _gzip_file(src: Path, dest: Path) -> None:
    """Stream-gzip src -> dest in 1 MiB chunks (never loads the body into memory)."""
    with src.open("rb") as f_in, gzip.open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out, length=1 << 20)


def stage_artifacts(
    source_root: Path,
    staging_dir: Path,
    artifact_specs: tuple[tuple[str, str, str], ...],
    metadata: dict,
    metadata_filename: str = "metadata.json",
) -> None:
    """Stage each artifact; gzip the .gz specs, copy the rest; write metadata as JSON.

    Compression rule: staged names ending in .gz are stream-gzipped (the multi-megabyte
    events/freeze-frame bodies), the rest copied plain (kilobyte roster/metadata).
    """
    for _role, source_name, staged_name in artifact_specs:
        src = source_root / source_name
        dest = staging_dir / staged_name
        if staged_name.endswith(".gz"):
            _gzip_file(src, dest)
        else:
            shutil.copyfile(src, dest)
    (staging_dir / metadata_filename).write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
