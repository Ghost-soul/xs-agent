"""Export the FastAPI contract without starting the database lifespan."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from novel_writer.api.app import create_app  # noqa: E402


def main() -> None:
    target = PROJECT_ROOT / "frontend" / "openapi.json"
    target.write_text(
        json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
