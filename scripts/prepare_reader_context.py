"""Build a minimal reader-only context; private files can never enter the image."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "reader/Dockerfile",
    "reader/Dockerfile.dockerignore",
    "reader/requirements.txt",
    "reader/__init__.py",
    "reader/app.py",
    "reader/database.py",
    "reader/main.py",
    "deploy/__init__.py",
    "deploy/web.py",
    "deploy/healthcheck.py",
    "frontend/package.json",
    "frontend/pnpm-lock.yaml",
    "frontend/tsconfig.app.json",
    "frontend/tsconfig.reader.json",
    "frontend/vite.reader.config.ts",
    "frontend/reader/index.html",
    "frontend/src/reader/main.tsx",
    "frontend/src/reader/ReaderApp.tsx",
    "frontend/src/reader/reader.css",
)


def prepare(root: Path, destination: Path) -> dict:
    if destination.exists():
        raise ValueError("输出目录已存在，请使用新目录")
    files = [root / name for name in FILES]
    for path in files:
        if (
            not path.is_file()
            or not path.resolve().is_relative_to(root.resolve())
            or path.is_symlink()
            or any(p.is_symlink() for p in path.parents)
        ):
            raise ValueError(f"缺失或不安全的构建来源：{path.relative_to(root)}")
    manifest = {}
    for path in files:
        relative = path.relative_to(root)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        manifest[relative.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    (destination / "SOURCE-MANIFEST.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf8"
    )
    return {"files": len(files), "context": str(destination)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    print(json.dumps(prepare(ROOT, parser.parse_args().output)))
