# Tutorial: Exploring SkillCorner Tracking Data

**Objective:** By the end of this tutorial you will understand the structure of SkillCorner V3 tracking data, validate a game using the CLI, and know how to access the full 20-match dataset.

**Prerequisites:**
- Python 3.12+ installed
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- This repository cloned and dependencies installed (`uv sync --extra dev`)

**Time:** ~10 minutes

---

## 1. What You're Looking At

This project redistributes [SkillCorner open tracking data](https://github.com/SkillCorner/opendata) — 20 A-League Men matches from the 2024/2025 season. Each game is redistributed as-is with four files (id-prefixed, e.g. `2016236_match.json`):

| File | Format | Contents |
|------|--------|----------|
| `<id>_match.json` | JSON | Match metadata — teams, players, pitch dimensions, periods, competition |
| `<id>_tracking_extrapolated.jsonl` | JSONL (one JSON object per line) | Frame-by-frame player and ball positions at 10 frames per second |
| `<id>_dynamic_events.csv` | CSV | On-ball events |
| `<id>_phases_of_play.csv` | CSV | Phases of play (in/out of possession spans) |

This tutorial focuses on the two core files — match metadata and tracking. The repository includes sample fixtures you can explore without downloading the full dataset.

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

The 20 matches are served as-is through the Mock Provider API's public tier — the same
JSON / JSONL / CSV files described above, one artifact per HTTP request (the files are
served as-is; there is no bundled Parquet build).

### From the Mock API

Provider-style ingestion (bearer token auth, HTTP download):

```bash
TOKEN="test-token-pining-for-the-data"
API="https://your-api-url/v1"

# List available games (20 matches)
curl -s -H "Authorization: Bearer $TOKEN" "$API/skillcorner/matches" | python -m json.tool

# Download a tracking file (artifact keys are id-prefixed)
curl -s -L -H "Authorization: Bearer $TOKEN" \
  "$API/skillcorner/matches/2016236/2016236_tracking_extrapolated" -o tracking.jsonl
```

### From the upstream source

The data originates from SkillCorner's MIT-licensed [open-data repository](https://github.com/SkillCorner/opendata)
under `data/matches/<id>/` — publicly fetchable without authentication.

See the [Setup Guide](../terraform/docs/setup.md) to deploy your own API instance.

> **Windows:** Replace `curl` with `Invoke-WebRequest` or use `curl.exe` (shipped with Windows 10+). Replace `python -m json.tool` with `ConvertFrom-Json` in PowerShell.

---

## Next Steps

- Browse the [API Reference](api-reference.md) for endpoint details
- Read [ARCHITECTURE.md](../ARCHITECTURE.md) for the full system design
- Explore the [C4 architecture diagrams](c4/architecture.html) in your browser
