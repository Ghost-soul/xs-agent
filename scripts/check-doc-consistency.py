"""Validate public documentation without requiring private workstation records."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "docs" / "DEVELOPMENT.md"
ACTIVE_DOCS = (
    ROOT / "README.md",
    ROOT / "SECURITY.md",
    ROOT / "deploy" / "README.md",
    CURRENT,
    ROOT / "docs" / "ARCHITECTURE.md",
)


def check() -> list[str]:
    errors: list[str] = []
    texts: dict[Path, str] = {}
    for path in ACTIVE_DOCS:
        if not path.is_file():
            errors.append(f"missing active document: {path.relative_to(ROOT)}")
        else:
            texts[path] = path.read_text(encoding="utf-8")
    current = texts.get(CURRENT, "")
    for path, text in texts.items():
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            target = target.strip("<>").split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            if not (path.parent / target).exists():
                errors.append(f"{path.relative_to(ROOT)} has a missing local link: {target}")
    configuration = Config(str(ROOT / "alembic.ini"))
    configuration.set_main_option("script_location", str(ROOT / "migrations"))
    configuration.set_main_option("path_separator", "os")
    heads = ScriptDirectory.from_config(configuration).get_heads()
    declared = re.search(r"^仓库 Alembic head：`([^`]+)`", current, re.MULTILINE)
    if len(heads) != 1 or declared is None or declared.group(1) != heads[0]:
        errors.append("public development guide must match the preserved migration chain")
    return errors


if __name__ == "__main__":
    findings = check()
    if findings:
        print("Documentation consistency check failed:", file=sys.stderr)
        for finding in findings:
            print(f"- {finding}", file=sys.stderr)
        raise SystemExit(1)
    print("Documentation consistency check passed")
