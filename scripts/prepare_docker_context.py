"""Prepare the allowlisted build context used by CI to publish the GHCR image."""

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXACT_FILES = (
    "Dockerfile", ".dockerignore", "pyproject.toml", "uv.lock",
    "frontend/package.json", "frontend/pnpm-lock.yaml", "frontend/index.html",
    "frontend/tsconfig.json", "frontend/tsconfig.app.json", "frontend/tsconfig.node.json",
    "frontend/vite.config.ts",
    "deploy/entrypoint.py", "deploy/web.py", "deploy/healthcheck.py",
    "deploy/alembic.ini", "deploy/README.md", "deploy/__init__.py",
    "configs/prompt-templates/published.json",
)
PATTERNS = (
    "src/novel_writer/**/*.py", "migrations/**/*.py",
    "configs/genre-quality-cards/genres/*.md", "configs/genre-quality-cards/narrative/*.md",
    "frontend/src/**/*.ts", "frontend/src/**/*.tsx", "frontend/src/**/*.css",
    "frontend/src/**/*.svg",
)
SECRET_PATTERN = re.compile(
    rb"sk-(?:proj-)?[A-Za-z0-9_-]{24,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)


def source_files(root: Path) -> list[Path]:
    selected = {root / name for name in EXACT_FILES}
    for pattern in PATTERNS:
        selected.update(root.glob(pattern))
    result = []
    for path in sorted(selected):
        if ".test." in path.name or path.name == "testSetup.ts":
            continue
        linked = path.is_symlink() or any(
            (root / parent).is_symlink() for parent in path.relative_to(root).parents
        )
        if linked or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"拒绝链接或越界文件：{path.relative_to(root)}")
        if not path.is_file():
            raise ValueError(f"缺少必要文件：{path.relative_to(root)}")
        # Detect common hard-coded credentials without printing their values.
        if SECRET_PATTERN.search(path.read_bytes()):
            raise ValueError(f"检测到疑似密钥，停止打包：{path.relative_to(root)}")
        result.append(path)
    return result


def prepare_context(root: Path, destination: Path) -> dict[str, str | int]:
    if destination.exists():
        raise ValueError("输出目录已存在，拒绝覆盖；请使用新目录")
    files = source_files(root)
    context = destination / "source"
    context.mkdir(parents=True)
    manifest = {}
    for path in files:
        name = path.relative_to(root).as_posix()
        target = context / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        manifest[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    details = {
        "format": "novel-writer-public-source-v1", "files": manifest,
        "excludes": [
            "all local data and databases", "API keys and credentials", "provider profiles",
            "author settings", "novels and provider responses", "logs and backups",
            "reference corpora", "local tokenizers", "Git history", "local runtime state",
        ],
    }
    (context / "SOURCE-MANIFEST.json").write_text(
        json.dumps(details, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return {"files": len(files), "context": str(context)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare_context(ROOT, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
