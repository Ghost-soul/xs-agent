from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from novel_writer.services.errors import ConflictError
from novel_writer.services.project_deletion_inventory import DeletionInventory, content_hashes
from novel_writer.services.provider_output_store import provider_output_path


@dataclass(frozen=True)
class DeletionRoots:
    content: Path
    workspace: Path

    def root(self, name: str) -> Path:
        if name not in {"content", "workspace"}:
            raise ConflictError("删除清单的根目录无效")
        return (self.content if name == "content" else self.workspace).resolve()

    def binding(self) -> dict[str, str]:
        return {name: str(self.root(name)) for name in ("content", "workspace")}

    def checked(self, name: str, relative: str) -> Path:
        root = self.root(name)
        relative_path = Path(relative)
        path = root / relative_path
        if (
            path == root
            or relative_path.anchor
            or ".." in relative_path.parts
            or not path.resolve().is_relative_to(root)
        ):
            raise ConflictError("删除路径超出允许范围")
        return _checked_links(root, path)


class _ScanRoots(DeletionRoots):
    def root(self, name: str) -> Path:
        if name not in {"content", "workspace"}:
            raise ConflictError("删除清单的根目录无效")
        return self.content if name == "content" else self.workspace

    def checked(self, name: str, relative: str) -> Path:
        root = self.root(name)
        relative_path = Path(relative)
        path = root / relative_path
        if (
            path == root
            or relative_path.anchor
            or ".." in relative_path.parts
            or root.drive
            and any(part.endswith((".", " ")) or ":" in part for part in relative_path.parts)
        ):
            raise ConflictError("删除路径超出允许范围或不是规范相对路径")
        return _checked_links(root, path)


def _checked_links(root: Path, path: Path) -> Path:
    current = path
    while current != root:
        if current.is_symlink() or current.is_junction():
            raise ConflictError("删除范围内存在符号链接或目录联接，拒绝跟随")
        if current.parent == current:
            raise ConflictError("删除路径无效")
        current = current.parent
    return path


def _walk(roots: DeletionRoots, name: str, relative: str) -> list[Path]:
    path = roots.checked(name, relative)
    if not path.exists():
        return []
    if path.is_file():
        return [path]
    files: list[Path] = []
    for child in path.iterdir():
        files.extend(_walk(roots, name, child.relative_to(roots.root(name)).as_posix()))
    return files


def _scopes(
    roots: DeletionRoots,
    inventory: DeletionInventory,
    project_id: UUID,
) -> list[tuple[str, str]]:
    scopes = [
        ("content", f"run-safety/{identifier}")
        for identifier in inventory.ids("novel_automation_runs")
    ]
    scopes += [
        ("content", f"local-tasks/{identifier}") for identifier in inventory.ids("local_tasks")
    ]
    for row in inventory.table_rows("import_previews"):
        key = row.values["storage_key"]
        if Path(key).name != key or Path(key).suffix != ".bin":
            raise ConflictError("导入暂存文件路径无效")
        scopes.append(("content", f"import-staging/{key}"))
    root = roots.root("workspace")
    if root.exists():
        for folder in root.iterdir():
            if not folder.name.endswith(f"--{str(project_id)[:8]}"):
                continue
            manifest_path = roots.checked("workspace", f"{folder.name}/manifest.json")
            if not manifest_path.is_file():
                raise ConflictError("作品镜像缺少归属清单，无法安全删除")
            manifest = json.loads(manifest_path.read_bytes())
            if manifest.get("project_id") == str(project_id):
                scopes.append(("workspace", folder.name))
    return scopes


def _reachable_hashes(roots: DeletionRoots, seeds: set[str]) -> set[str]:
    reached: set[str] = set()
    pending = set(seeds)
    content_root = roots.root("content")
    while pending:
        digest = pending.pop()
        if digest in reached:
            continue
        reached.add(digest)
        path = provider_output_path(content_root, digest)
        path = roots.checked("content", f"provider-outputs/{digest[:2]}/{path.name}")
        if path.is_file():
            pending.update(content_hashes(path.read_bytes()) - reached)
    return reached


def retained_hashes(
    roots: DeletionRoots,
    inventory: DeletionInventory,
    excluded_paths: set[Path],
) -> set[str]:
    source_roots = roots
    roots = _ScanRoots(roots.root("content"), roots.root("workspace"))
    owned_keys = {(row.table, row.key) for row in inventory.owned}
    seeds = {
        digest
        for row in inventory.rows
        if (row.table, row.key) not in owned_keys
        for digest in row.hashes
    }
    for name in ("content", "workspace"):
        root = roots.root(name)
        if not root.exists():
            continue
        for child in root.iterdir():
            if name == "content" and child.name == "provider-outputs":
                continue
            for path in _walk(roots, name, child.name):
                if path not in excluded_paths:
                    seeds.update(content_hashes(path.read_bytes()))
    protected = _reachable_hashes(roots, seeds)
    if source_roots.binding() != roots.binding():
        raise ConflictError("存储根目录在盘点期间发生变化，请重新核对")
    return protected


def collect_files(
    roots: DeletionRoots,
    inventory: DeletionInventory,
    project_id: UUID,
) -> dict[str, Any]:
    source_roots = roots
    roots = _ScanRoots(roots.root("content"), roots.root("workspace"))
    scopes = _scopes(roots, inventory, project_id)
    scoped = {
        (name, path.relative_to(roots.root(name)).as_posix()): path
        for name, relative in scopes
        for path in _walk(roots, name, relative)
    }
    candidates = {digest for row in inventory.owned for digest in row.hashes}
    for path in scoped.values():
        candidates.update(content_hashes(path.read_bytes()))
    candidates = _reachable_hashes(roots, candidates)
    protected = retained_hashes(roots, inventory, set(scoped.values()))
    owned_keys = {(row.table, row.key) for row in inventory.owned}
    shared_imports = {
        f"import-staging/{row.values['storage_key']}"
        for row in inventory.rows
        if row.table == "import_previews" and (row.table, row.key) not in owned_keys
    }
    files: list[dict[str, Any]] = []
    shared = 0
    for (name, relative), path in sorted(scoped.items()):
        if name == "content" and relative in shared_imports:
            shared += 1
        else:
            files.append(_entry(name, relative, path, "scoped"))
    for digest in sorted(candidates):
        path = provider_output_path(roots.root("content"), digest)
        if not path.is_file():
            continue
        if digest in protected:
            shared += 1
        else:
            relative = path.relative_to(roots.root("content")).as_posix()
            files.append(_entry("content", relative, path, "cas"))
    if source_roots.binding() != roots.binding():
        raise ConflictError("存储根目录在盘点期间发生变化，请重新核对")
    return {
        "roots": roots.binding(),
        "files": files,
        "scopes": [
            list(scope)
            for scope in scopes
            if scope[0] != "content" or scope[1] not in shared_imports
        ],
        "shared_files_preserved": shared,
        "estimated_file_bytes": sum(item["size"] for item in files),
    }


def _entry(name: str, relative: str, path: Path, kind: str) -> dict[str, Any]:
    encoded = path.read_bytes()
    return {
        "root": name,
        "relative": relative,
        "kind": kind,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "size": len(encoded),
    }


def cleanup_files(
    roots: DeletionRoots,
    manifest: dict[str, Any],
    inventory: DeletionInventory,
) -> int:
    if manifest.get("roots") != roots.binding():
        raise ConflictError("存储根目录已变化，请恢复删除时的目录配置再继续清理")
    entries = manifest["files"]
    planned = {roots.checked(item["root"], item["relative"]) for item in entries}
    for name, relative in manifest["scopes"]:
        if any(path not in planned for path in _walk(roots, name, relative)):
            raise ConflictError("删除确认后出现新文件，需人工核对")
    protected = retained_hashes(roots, inventory, planned)
    shared_imports = {
        f"import-staging/{row.values['storage_key']}"
        for row in inventory.rows
        if row.table == "import_previews"
    }
    shared = 0
    for item in entries:
        path = roots.checked(item["root"], item["relative"])
        if (
            item["kind"] == "cas"
            and path.stem in protected
            or (item["root"] == "content" and item["relative"] in shared_imports)
        ):
            shared += 1
            continue
        if not path.exists():
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ConflictError("删除确认后文件已变化，拒绝删除")
        path.unlink()
    for name, relative in manifest["scopes"]:
        path = roots.checked(name, relative)
        if path.is_dir():
            directories = sorted(
                (item for item in path.rglob("*") if item.is_dir()),
                key=lambda item: len(item.parts),
                reverse=True,
            )
            for directory in [*directories, path]:
                roots.checked(name, directory.relative_to(roots.root(name)).as_posix())
                if not any(directory.iterdir()):
                    directory.rmdir()
    return shared
