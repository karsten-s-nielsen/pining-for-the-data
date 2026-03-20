"""Cross-format conversion: Respo.Vision 3D → Metrica-equivalent 2D.

STATUS: Scaffolding only — conversion logic is trivial but depends on
the actual Respo.Vision schema from respovision.py.

## The conversion

Metrica format: one XY position per player per frame (centroid only).
Respo.Vision format: 50+ XYZ keypoints per player per frame.

Projection: for each player in each frame:
    1. Take all keypoints (or a subset, e.g., torso keypoints only)
    2. Optionally filter by confidence threshold
    3. Average X values → player_x, average Y values → player_y
    4. Drop Z axis entirely

This produces a Metrica-compatible DataFrame that can be written
through the existing formats.metrica.write_parquet() pipeline.

## Why this matters

The open dataset serves both formats from the same games:
- Respo.Vision format: full 3D pose (researchers who want keypoints)
- Metrica-equivalent: 2D centroids (compatible with existing tools like
  mplsoccer, kloppy, and luxury-lakehouse's metrica.py adapter)

## GPU note

The projection is element-wise averaging — NumPy handles 135K frames x 22
players in <1 second. GPU (cupy/PyTorch) is not needed for this operation.

GPU becomes relevant if we add:
- Spatial analytics on keypoint data (joint angles, body orientation)
- Pose similarity search across frames
- Real-time processing of streaming tracking data

None of these are planned for the initial release.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


def respovision_to_metrica(respo_df: pd.DataFrame) -> pd.DataFrame:
    """Convert Respo.Vision 3D tracking data to Metrica-equivalent 2D format.

    Takes a DataFrame of Respo.Vision data (however it's structured after
    read_respovision) and produces a DataFrame matching the Metrica schema:
    - Period, Frame, Time [s]
    - {player_id}_x, {player_id}_y per player (0-1 normalized)
    - Ball_x, Ball_y

    NOT IMPLEMENTED — depends on read_respovision output schema.

    Expected implementation (pseudocode):

        for each frame:
            for each player:
                centroid_x = mean(keypoint_x values)
                centroid_y = mean(keypoint_y values)
                # → becomes {player_id}_x, {player_id}_y columns

    With NumPy vectorization this is a groupby + mean, not a nested loop.
    """
    raise NotImplementedError(
        "Conversion depends on Respo.Vision reader output format. See respovision.py for schema expectations."
    )


def respovision_to_metrica_from_file(
    respo_path: str | bytes,
    roster_path: str | bytes,
    output_dir: str | bytes,
) -> None:
    """Full pipeline: Respo.Vision JSON → de-identified Metrica-equivalent Parquet.

    1. Read Respo.Vision JSON
    2. Apply de-identification (TwoLayerMapping from roster JSON)
    3. Project 3D → 2D centroids
    4. Write as Metrica-format CSV + Parquet

    NOT IMPLEMENTED — compose from respovision.read_respovision,
    respovision.deidentify_respovision, and respovision_to_metrica
    once those are built.
    """
    raise NotImplementedError("End-to-end conversion pipeline — build when sample data arrives.")
