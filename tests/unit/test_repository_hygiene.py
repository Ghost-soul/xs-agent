from __future__ import annotations

import runpy
from pathlib import Path


def test_source_modules_are_referenced_or_explicit_entrypoints() -> None:
    root = Path(__file__).resolve().parents[2]
    namespace = runpy.run_path(root / "scripts" / "check-unused-modules.py")

    assert namespace["check"]() == []


def test_requirements_is_only_a_pyproject_compatibility_pointer() -> None:
    root = Path(__file__).resolve().parents[2]
    requirements = (root / "requirements.txt").read_text(encoding="utf-8").splitlines()
    semantic_lines = [
        line.strip()
        for line in requirements
        if line.strip() and not line.startswith("#")
    ]

    assert semantic_lines == ["-e ."]
    assert (root / "uv.lock").is_file()
    assert (root / "frontend" / "pnpm-lock.yaml").is_file()
