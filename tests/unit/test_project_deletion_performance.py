import hashlib
import re
from pathlib import Path
from threading import get_ident
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from novel_writer.services import project_deletion
from novel_writer.services.errors import ConflictError
from novel_writer.services.project_deletion_files import DeletionRoots, _ScanRoots, collect_files
from novel_writer.services.project_deletion_inventory import (
    UUID_PATTERN,
    DeletionInventory,
    InventoryRow,
    content_hashes,
)
from tests.legacy_output_fixture import store_provider_output


@pytest.mark.parametrize("length", [1, 63, 64, 65, 127, 128, 129, 1024])
def test_hash_scan_preserves_exact_hex_token_boundaries(length: int) -> None:
    encoded = b"|".join(
        [
            b"a" * length,
            b"X" + b"0" * 64 + b"G",
            b"b" * 64 + b"f",
            "正文和共享引用".encode() + b"c" * 64,
            bytes(range(256)),
        ]
    )
    legacy = re.compile(rb"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
    assert content_hashes(encoded) == {value.decode() for value in legacy.findall(encoded)}


def test_uuid_scan_preserves_existing_reference_matches() -> None:
    identifier = str(uuid4())
    source = f"正文{identifier} uppercase={identifier.upper()} prefix=aaaa{identifier}"
    legacy = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
    assert UUID_PATTERN.findall(source) == legacy.findall(source)


def test_file_scan_resolves_roots_once_and_does_not_cache_between_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = DeletionRoots(tmp_path / "content", tmp_path / "projects")
    digest = store_provider_output(roots.content, "private content")
    row = InventoryRow(
        "writing_artifacts",
        (uuid4(),),
        {},
        "",
        set(),
        {digest, *(hashlib.sha256(str(number).encode()).hexdigest() for number in range(100))},
    )
    inventory = DeletionInventory([row], [row])
    original_resolve = Path.resolve
    root_resolutions: list[Path] = []

    def resolve(path: Path, strict: bool = False) -> Path:
        if path in {roots.content, roots.workspace}:
            root_resolutions.append(path)
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    result = collect_files(roots, inventory, uuid4())
    assert len(result["files"]) == 1
    assert len(root_resolutions) == 4
    monkeypatch.setattr(Path, "is_junction", lambda path: path.name == digest[:2])
    with pytest.raises(ConflictError, match="联接"):
        collect_files(roots, inventory, uuid4())


def test_file_scan_rejects_root_binding_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = DeletionRoots(tmp_path / "content", tmp_path / "projects")
    content_reads = 0

    def root(instance: DeletionRoots, name: str) -> Path:
        nonlocal content_reads
        if name == "content":
            content_reads += 1
            return instance.content if content_reads == 1 else tmp_path / "replacement"
        return instance.workspace

    monkeypatch.setattr(DeletionRoots, "root", root)
    with pytest.raises(ConflictError, match="盘点期间发生变化"):
        collect_files(roots, DeletionInventory([], []), uuid4())


@pytest.mark.parametrize("relative", ["nested/../body", "/body"])
def test_scan_rejects_noncanonical_relative_paths(tmp_path: Path, relative: str) -> None:
    roots = DeletionRoots(tmp_path / "content", tmp_path / "projects")
    with pytest.raises(ConflictError):
        roots.checked("content", relative)


@pytest.mark.parametrize("relative", [".. /outside", "folder./body", "body:stream"])
def test_fast_scan_does_not_accept_windows_path_aliases(tmp_path: Path, relative: str) -> None:
    roots = _ScanRoots(tmp_path / "content", tmp_path / "projects")
    if tmp_path.drive:
        with pytest.raises(ConflictError):
            roots.checked("content", relative)
    else:
        assert roots.checked("content", relative) == roots.content / relative


@pytest.mark.asyncio
async def test_preview_moves_file_inventory_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = uuid4()
    row = InventoryRow(
        "story_projects",
        (project_id,),
        {"id": project_id, "title": "Archived"},
        "",
        set(),
        set(),
    )
    monkeypatch.setattr(
        project_deletion, "read_inventory", AsyncMock(return_value=DeletionInventory([row], [row]))
    )
    scan_threads: list[int] = []

    def scan(*args: object) -> dict:
        scan_threads.append(get_ident())
        return {"files": [], "estimated_file_bytes": 0, "shared_files_preserved": 0}

    monkeypatch.setattr(project_deletion, "collect_files", scan)
    service = project_deletion.ProjectDeletionService(
        SimpleNamespace(), DeletionRoots(tmp_path / "content", tmp_path / "projects")
    )
    preview = await service.preview(SimpleNamespace(), project_id)
    assert preview["project_id"] == str(project_id)
    assert scan_threads and scan_threads[0] != get_ident()
