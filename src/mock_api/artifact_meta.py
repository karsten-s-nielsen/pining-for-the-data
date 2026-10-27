"""Content identity of artifact objects: streamed SHA-256 and size of the bytes as stored (ADR 0013).

The hash is always computed from the bytes themselves. S3 ETags and S3 checksums are never used:
a multipart ETag is not a content hash, and an object written without a checksum has none (spec §5.4).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO, NamedTuple

CHUNK_SIZE: int = 8 * 1024 * 1024


class ArtifactDigest(NamedTuple):
    sha256: str
    size: int

    def as_dict(self) -> dict[str, str | int]:
        """The ``artifact_meta`` value shape: ``{"sha256": ..., "size": ...}``."""
        return {"sha256": self.sha256, "size": self.size}


def hash_stream(stream: BinaryIO, chunk_size: int = CHUNK_SIZE) -> ArtifactDigest:
    """Hash a binary stream in fixed-size chunks, so a large object is never held in memory."""
    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(chunk_size):
        digest.update(chunk)
        size += len(chunk)
    return ArtifactDigest(digest.hexdigest(), size)


def hash_file(path: Path) -> ArtifactDigest:
    with path.open("rb") as fh:
        return hash_stream(fh)


def artifact_name(filename: str) -> str:
    """Artifact key of a staged file: strip ALL extensions, so ``tracking.jsonl.bz2`` -> ``tracking``."""
    return filename.split(".", 1)[0]


def staged_files(game_dir: Path) -> list[Path]:
    """The files ``upload_game`` uploads: regular files, sorted, names starting with ``.`` excluded."""
    return [p for p in sorted(game_dir.iterdir()) if p.is_file() and not p.name.startswith(".")]


def staged_artifact_meta(game_dir: Path) -> dict[str, dict[str, str | int]]:
    """``artifact_meta`` for a staging directory, keyed by artifact name."""
    return {artifact_name(p.name): hash_file(p).as_dict() for p in staged_files(game_dir)}
