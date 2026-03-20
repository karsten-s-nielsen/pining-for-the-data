"""Push de-identified tracking data to HuggingFace Hub.

Publishes Parquet files as a HuggingFace Dataset under the luxury-lakehouse
organization. Generates/updates the dataset card with CC-BY-4.0 license metadata.
"""

from __future__ import annotations

from pathlib import Path

HF_ORG = "luxury-lakehouse"
HF_DATASET = "pining-for-the-data"
HF_REPO_ID = f"{HF_ORG}/{HF_DATASET}"

DATASET_CARD_TEMPLATE = """\
---
license: cc-by-4.0
language:
  - en
tags:
  - soccer
  - football
  - tracking-data
  - sports-analytics
  - de-identified
  - youth-soccer
pretty_name: "Pining for the Data — De-identified Youth Soccer Tracking"
size_categories:
  - 1K<n<10K
---

# Pining for the Data

De-identified XY tracking data from youth club soccer matches, recorded on
Veo3 and processed through commercial tracking providers.

> *"It's not pinin', it's passed on! This parrot is no more!"*
> — Monty Python's Flying Circus, Dead Parrot sketch

## What This Is

Real match tracking data from youth club soccer,
fully de-identified with synthetic player and team names sourced from
GOT, LOTR, Breaking Bad, Princess Bride, and other fictional universes.

- **Home team:** Wakanda FC
- **Format:** Metrica Sports CSV (0-1 normalized XY coordinates)
- **Coordinate system:** (0,0) = top-left, (1,1) = bottom-right
- **Frame rate:** 25 fps

## Usage

```python
from datasets import load_dataset

ds = load_dataset("{repo_id}")
```

## License

Data: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)
Code: [MIT](https://opensource.org/licenses/MIT)

## Source

Companion dataset to luxury-lakehouse, a serverless soccer analytics platform.

Code and tooling: [pining-for-the-data](https://github.com/karstenskyt/pining-for-the-data)
"""


def generate_dataset_card() -> str:
    """Generate the dataset card markdown."""
    return DATASET_CARD_TEMPLATE.format(repo_id=HF_REPO_ID)


def push_to_hub(
    parquet_dir: Path,
    repo_id: str = HF_REPO_ID,
    commit_message: str | None = None,
) -> str:
    """Push Parquet files to HuggingFace Hub.

    Parameters
    ----------
    parquet_dir : Path
        Directory containing .parquet files to upload.
    repo_id : str
        HuggingFace dataset repository ID.
    commit_message : str | None
        Commit message for the upload.

    Returns
    -------
    str
        URL of the published dataset.
    """
    from huggingface_hub import HfApi

    api = HfApi()

    api.create_repo(repo_id=repo_id, repo_type="dataset", exist_ok=True, private=False)

    parquet_files = sorted(parquet_dir.glob("*.parquet"))
    if not parquet_files:
        msg = f"No .parquet files found in {parquet_dir}"
        raise FileNotFoundError(msg)

    # Upload dataset card
    card_content = generate_dataset_card()
    api.upload_file(
        path_or_fileobj=card_content.encode("utf-8"),
        path_in_repo="README.md",
        repo_id=repo_id,
        repo_type="dataset",
        commit_message="Update dataset card",
    )

    # Upload parquet files
    message = commit_message or f"Add {len(parquet_files)} tracking data file(s)"
    api.upload_folder(
        folder_path=str(parquet_dir),
        path_in_repo="data",
        repo_id=repo_id,
        repo_type="dataset",
        commit_message=message,
        allow_patterns=["*.parquet"],
    )

    return f"https://huggingface.co/datasets/{repo_id}"


def main() -> None:
    """CLI entry point for publishing to HuggingFace Hub."""
    import argparse

    parser = argparse.ArgumentParser(description="Push de-identified tracking data to HuggingFace Hub")
    parser.add_argument("parquet_dir", type=Path, help="Directory containing .parquet files")
    parser.add_argument("--repo-id", default=HF_REPO_ID, help=f"HF dataset repo ID (default: {HF_REPO_ID})")
    parser.add_argument("--message", default=None, help="Commit message")
    args = parser.parse_args()

    url = push_to_hub(args.parquet_dir, repo_id=args.repo_id, commit_message=args.message)
    print(f"Published to {url}")
