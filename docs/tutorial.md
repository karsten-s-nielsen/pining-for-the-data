# Tutorial: Exploring SkillCorner Tracking Data

**Objective:** By the end of this tutorial you will understand the structure of SkillCorner V3 tracking data, validate a game using the CLI, and know how to access the full 10-match dataset.

**Prerequisites:**
- Python 3.12+ installed
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- This repository cloned and dependencies installed (`uv sync --extra dev`)

**Time:** ~10 minutes

---

## 1. What You're Looking At

This project redistributes [SkillCorner open tracking data](https://github.com/SkillCorner/opendata) — 10 A-League Men matches from the 2024/2025 season. Each game consists of two files:

| File | Format | Contents |
|------|--------|----------|
| `match.json` | JSON | Match metadata — teams, players, pitch dimensions, periods, competition |
| `tracking.jsonl` | JSONL (one JSON object per line) | Frame-by-frame player and ball positions at 10 frames per second |

The repository includes sample fixtures you can explore without downloading the full dataset.

---

## 2. Inspect a Match Metadata File

Open the sample match file:

```bash
cat src/tests/fixtures/sample_match.json | python -m json.tool | head -50
```

Key fields to notice:

| Field | What it tells you |
|-------|-------------------|
| `home_team` / `away_team` | Team names, IDs, and short codes |
| `players[]` | Every player on the pitch — name, jersey number, position, team, and a `trackable_object` ID that links to tracking data |
| `match_periods[]` | Start and end frames for each half, with duration |
| `pitch_length` / `pitch_width` | Pitch dimensions in meters (typically 105 x 68) |
| `ball.trackable_object` | The ball's ID in tracking frames |

Each player's `trackable_object` field is the key that connects match metadata to tracking frames. In the sample, player Hiroshi Tanaka (jersey #1, GK) has `trackable_object: 101` — you'll see `player_id: 101` in the tracking data.

---

## 3. Inspect Tracking Frames

Open the sample tracking file:

```bash
head -2 src/tests/fixtures/sample_tracking.jsonl | python -m json.tool
```

Each line is one frame. Key fields:

| Field | What it tells you |
|-------|-------------------|
| `frame` | Frame number (sequential integer) |
| `timestamp` | Time within the match period (e.g., `"00:00:00.10"` = 0.1 seconds) |
| `period` | Match period (1 = first half, 2 = second half) |
| `ball_data` | Ball position: `x`, `y` in meters from pitch center, `z` for height, `is_detected` flag |
| `player_data[]` | Array of player positions: `player_id` (matches `trackable_object` from match metadata), `x`, `y`, `is_detected` |
| `possession` | Which player/team currently has the ball |

**Coordinate system:** `x` and `y` are in meters relative to the pitch center (0, 0). For a standard 105 x 68m pitch, `x` ranges from approximately -52.5 to 52.5 and `y` from -34 to 34.

Notice that some players have `"is_detected": false` — this means the tracking system lost sight of them in that frame (common with camera-based tracking). Your analytics code should handle missing detections.

---

## 4. Validate a Game

The `pining-ingest` CLI validates that a match JSON and tracking JSONL pair are structurally correct:

```bash
uv run pining-ingest \
  src/tests/fixtures/sample_match.json \
  src/tests/fixtures/sample_tracking.jsonl
```

Expected output:

```
Match 9999999: 6 players, 3 frames — OK
```

The validator checks that both files parse correctly, extracts player and frame counts, and optionally copies validated files to an output directory (`--output-dir`).

---

## 5. Access the Full Dataset

### From HuggingFace Hub (easiest)

The full 10-match dataset is published as Parquet on HuggingFace:

```python
from datasets import load_dataset

ds = load_dataset("luxury-lakehouse/pining-for-the-data")
```

### From the Mock API

If you need to test provider-style ingestion (bearer token auth, HTTP download):

```bash
TOKEN="test-token-pining-for-the-data"
API="https://your-api-url/v1"

# List available games
curl -s -H "Authorization: Bearer $TOKEN" "$API/skillcorner/matches" | python -m json.tool

# Download a tracking file
curl -s -L -H "Authorization: Bearer $TOKEN" \
  "$API/skillcorner/matches/game_03/tracking" -o tracking.jsonl
```

See the [Setup Guide](../terraform/docs/setup.md) to deploy your own API instance.

> **Windows:** Replace `curl` with `Invoke-WebRequest` or use `curl.exe` (shipped with Windows 10+). Replace `python -m json.tool` with `ConvertFrom-Json` in PowerShell.

---

## Next Steps

- Browse the [API Reference](api-reference.md) for endpoint details
- Read [ARCHITECTURE.md](../ARCHITECTURE.md) for the full system design
- Explore the [C4 architecture diagrams](c4/architecture.html) in your browser
