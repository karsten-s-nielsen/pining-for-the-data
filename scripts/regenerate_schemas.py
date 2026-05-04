"""Regenerate schemas/{matches,players}.schema.json from Pydantic models.

Run after any edit to MatchEntry or PlayerRecord in shared.py. The schema-drift
test (src/tests/test_schemas.py) fails CI if you forget.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from canonical.models import MatchEntry, PlayerRecord  # noqa: E402

SCHEMAS_DIR = REPO_ROOT / "schemas"


def main() -> None:
    SCHEMAS_DIR.mkdir(exist_ok=True)
    for name, model in [("matches", MatchEntry), ("players", PlayerRecord)]:
        path = SCHEMAS_DIR / f"{name}.schema.json"
        path.write_text(
            json.dumps(model.model_json_schema(), indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
