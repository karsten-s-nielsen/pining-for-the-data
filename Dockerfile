# GPU-enabled container for compute-heavy processing (future).
#
# STATUS: Scaffolding only — no GPU workload exists yet.
#
# USE WHEN:
# - Respo.Vision keypoint analytics requiring GPU acceleration
# - Running pose estimation models (RTMPose, MMPose) locally
# - Batch spatial analytics on 3D tracking data
#
# NOT NEEDED FOR:
# - Roster generation, HF publishing
# - SkillCorner V3 validation and redistribution (CPU-only, runs via uv directly)
#
# BUILD:
#   docker build -t pining-for-the-data .
#
# RUN (with GPU):
#   docker run --gpus all -v ./data:/data pining-for-the-data pining-ingest ...
#
# The NVIDIA base image + Docker GPU passthrough abstracts away the
# Windows/Linux difference — same container runs on any NVIDIA GPU host.

FROM nvidia/cuda:12.8.0-runtime-ubuntu24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 \
    python3.12-venv \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Dependencies first (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Application code
COPY src/ src/
COPY name_pools/ name_pools/
COPY rosters/ rosters/

# Install the package
RUN uv sync --frozen --no-dev

ENTRYPOINT ["uv", "run"]
