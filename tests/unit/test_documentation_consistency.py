from __future__ import annotations

import runpy
from pathlib import Path


def test_public_documentation_links_and_migration_chain() -> None:
    root = Path(__file__).resolve().parents[2]
    namespace = runpy.run_path(root / "scripts" / "check-doc-consistency.py")

    assert namespace["check"]() == []
