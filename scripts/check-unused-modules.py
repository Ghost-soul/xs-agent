from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PACKAGE = SRC / "novel_writer"
ENTRYPOINTS = ROOT / "configs" / "module-entrypoints.txt"


def module_name(path: Path) -> str:
    return ".".join(path.relative_to(SRC).with_suffix("").parts)


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    try:
        current = module_name(path).split(".")
    except ValueError:
        current = []
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if not current:
                    continue
                base = current[: -node.level]
                if node.module:
                    base.extend(node.module.split("."))
                resolved = ".".join(base)
            else:
                resolved = node.module or ""
            if resolved:
                found.add(resolved)
                found.update(f"{resolved}.{alias.name}" for alias in node.names)
    return found


def check() -> list[str]:
    modules = {
        module_name(path): path
        for path in PACKAGE.rglob("*.py")
        if path.name != "__init__.py"
    }
    referenced: set[str] = set()
    for root in (ROOT / "src", ROOT / "tests", ROOT / "scripts", ROOT / "migrations"):
        for path in root.rglob("*.py"):
            referenced.update(imported_modules(path))
    allowed = {
        line.strip()
        for line in ENTRYPOINTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    return [
        str(path.relative_to(ROOT))
        for name, path in sorted(modules.items())
        if name not in referenced and name not in allowed
    ]


if __name__ == "__main__":
    unused = check()
    if unused:
        print("Unreferenced modules require deletion or an explicit entrypoint:", file=sys.stderr)
        for path in unused:
            print(f"- {path}", file=sys.stderr)
        raise SystemExit(1)
    print("Unused-module check passed")
