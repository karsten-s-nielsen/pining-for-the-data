"""SkillCorner V3 format: match metadata JSON + tracking JSONL (10 fps)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def read_match_json(path: Path) -> dict:
    """Read a SkillCorner V3 match metadata JSON file."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_tracking_jsonl(path: Path) -> list[dict]:
    """Read a SkillCorner V3 tracking JSONL file (one JSON object per frame)."""
    frames: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                frames.append(json.loads(line))
    return frames


def extract_player_jerseys(match_data: dict) -> dict[str, list[int]]:
    """Extract sorted jersey numbers per team from match metadata."""
    home_team_id = match_data["home_team"]["id"]
    home: list[int] = []
    away: list[int] = []
    for player in match_data["players"]:
        if player["team_id"] == home_team_id:
            home.append(player["number"])
        else:
            away.append(player["number"])
    return {"home": sorted(home), "away": sorted(away)}


def write_match_json(match_data: dict, path: Path) -> None:
    """Write a SkillCorner V3 match metadata JSON file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(match_data, f, indent=2, ensure_ascii=False)


def write_tracking_jsonl(frames: list[dict], path: Path) -> None:
    """Write a SkillCorner V3 tracking JSONL file."""
    with open(path, "w", encoding="utf-8") as f:
        for frame in frames:
            f.write(json.dumps(frame, ensure_ascii=False) + "\n")


def validate_game(
    match_path: Path,
    tracking_path: Path,
    output_dir: Path | None = None,
) -> dict:
    """Validate a SkillCorner V3 game and optionally copy to output directory.

    Returns validation summary with match_id, player_count, frame_count.
    """
    match_data = read_match_json(match_path)
    frames = read_tracking_jsonl(tracking_path)

    result = {
        "valid": True,
        "match_id": match_data["id"],
        "player_count": len(match_data["players"]),
        "frame_count": len(frames),
    }

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        write_match_json(match_data, output_dir / "match.json")
        write_tracking_jsonl(frames, output_dir / "tracking.jsonl")

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate SkillCorner V3 tracking data")
    parser.add_argument("match", type=Path, help="Path to match metadata JSON")
    parser.add_argument("tracking", type=Path, help="Path to tracking JSONL")
    parser.add_argument("--output-dir", type=Path, default=None, help="Copy validated files to this directory")
    args = parser.parse_args()

    result = validate_game(args.match, args.tracking, args.output_dir)
    print(f"Match {result['match_id']}: {result['player_count']} players, {result['frame_count']} frames — OK")
