import hashlib
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from novel_writer.services.errors import ConflictError
from novel_writer.services.project_deletion_files import DeletionRoots, cleanup_files
from novel_writer.services.project_deletion_inventory import DeletionInventory, InventoryRow
from novel_writer.services.provider_output_store import provider_output_path
from novel_writer.services.retrieval import engine as retrieval_engine
from tests.legacy_output_fixture import store_provider_output


def _manifest(roots: DeletionRoots, relative: str, content: bytes, *, kind: str = "scoped") -> dict:
    return {
        "roots": roots.binding(),
        "files": [
            {
                "root": "content",
                "relative": relative,
                "kind": kind,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        ],
        "scopes": [["content", relative]],
    }


@pytest.mark.parametrize("relative", ["../outside.txt", ".", "../../elsewhere/file"])
def test_cleanup_rejects_paths_outside_exact_root(tmp_path: Path, relative: str) -> None:
    roots = DeletionRoots(tmp_path / "content", tmp_path / "projects")
    with pytest.raises(ConflictError):
        roots.checked("content", relative)


def test_cleanup_refuses_windows_junction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = DeletionRoots(tmp_path / "content", tmp_path / "projects")
    monkeypatch.setattr(Path, "is_junction", lambda path: path.name == "escape")
    with pytest.raises(ConflictError, match="联接"):
        roots.checked("content", "escape/body.txt")


def test_changed_file_remains_for_explicit_resolution(tmp_path: Path) -> None:
    roots = DeletionRoots(tmp_path / "content", tmp_path / "projects")
    roots.content.mkdir()
    path = roots.content / "body.txt"
    path.write_bytes(b"changed after confirmation")
    with pytest.raises(ConflictError, match="文件已变化"):
        cleanup_files(roots, _manifest(roots, "body.txt", b"original"), DeletionInventory([], []))
    assert path.read_bytes() == b"changed after confirmation"


def test_new_file_in_owned_directory_is_not_silently_deleted(tmp_path: Path) -> None:
    roots = DeletionRoots(tmp_path / "content", tmp_path / "projects")
    directory = roots.content / "local-tasks" / str(uuid4())
    directory.mkdir(parents=True)
    (directory / "new.txt").write_bytes(b"new")
    manifest = {
        "roots": roots.binding(),
        "files": [],
        "scopes": [["content", directory.relative_to(roots.content).as_posix()]],
    }
    with pytest.raises(ConflictError, match="出现新文件"):
        cleanup_files(roots, manifest, DeletionInventory([], []))
    assert (directory / "new.txt").is_file()


def test_new_shared_reference_protects_content_during_cleanup(tmp_path: Path) -> None:
    roots = DeletionRoots(tmp_path / "content", tmp_path / "projects")
    content = "retained by another novel"
    digest = store_provider_output(roots.content, content)
    path = provider_output_path(roots.content, digest)
    row = InventoryRow("writing_artifacts", (uuid4(),), {}, "", set(), {digest})
    manifest = _manifest(
        roots, path.relative_to(roots.content).as_posix(), content.encode(), kind="cas"
    )
    assert cleanup_files(roots, manifest, DeletionInventory([row], [])) == 1
    assert path.is_file()


def test_shared_manifest_reference_protects_nested_output(tmp_path: Path) -> None:
    roots = DeletionRoots(tmp_path / "content", tmp_path / "projects")
    digest = store_provider_output(roots.content, "nested body")
    parent = store_provider_output(roots.content, json.dumps({"body_sha256": digest}))
    row = InventoryRow("writing_artifacts", (uuid4(),), {}, "", set(), {parent})
    path = provider_output_path(roots.content, digest)
    manifest = _manifest(
        roots, path.relative_to(roots.content).as_posix(), b"nested body", kind="cas"
    )
    assert cleanup_files(roots, manifest, DeletionInventory([row], [])) == 1
    assert path.is_file()


def test_cleanup_never_follows_a_changed_storage_configuration(tmp_path: Path) -> None:
    roots = DeletionRoots(tmp_path / "original", tmp_path / "projects")
    changed = DeletionRoots(tmp_path / "different", tmp_path / "projects")
    changed.content.mkdir()
    (changed.content / "body.txt").write_bytes(b"same bytes")
    with pytest.raises(ConflictError, match="根目录已变化"):
        cleanup_files(
            changed, _manifest(roots, "body.txt", b"same bytes"), DeletionInventory([], [])
        )
    assert (changed.content / "body.txt").read_bytes() == b"same bytes"


def test_expense_summary_keeps_known_cost_and_unknown_count_without_story_content() -> None:
    rows = [
        InventoryRow(
            "writing_chunk_calls",
            (uuid4(),),
            {"actual_cost_cny": Decimal("1.2345")},
            "",
            set(),
            set(),
        ),
        InventoryRow(
            "writing_chunk_calls", (uuid4(),), {"actual_cost_cny": None}, "", set(), set()
        ),
    ]
    assert DeletionInventory(rows, rows).cost_summary == {
        "currency": "CNY",
        "recorded_cost": "1.2345",
        "call_records": 2,
        "unknown_cost_records": 1,
    }


@pytest.mark.asyncio
async def test_index_purge_requires_success_and_is_scoped_to_exact_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid4()
    (tmp_path / "index-marker").write_bytes(b"test")
    deleted: list[str] = []
    table = SimpleNamespace(delete=deleted.append, count_rows=lambda **kwargs: 0)
    database = SimpleNamespace(
        table_names=lambda: ["novel_chapters"], open_table=lambda name: table
    )
    monkeypatch.setattr(
        retrieval_engine.importlib,
        "import_module",
        lambda name: SimpleNamespace(connect=lambda path: database),
    )
    await retrieval_engine.purge_project_indexes(project_id, tmp_path)
    assert deleted == [f"project_id = '{project_id}'"]
    table.count_rows = lambda **kwargs: 1
    with pytest.raises(RuntimeError, match="did not remove"):
        await retrieval_engine.purge_project_indexes(project_id, tmp_path)


@pytest.mark.asyncio
async def test_existing_index_without_library_is_not_reported_as_cleaned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "index-marker").write_bytes(b"test")

    def missing_module(name: str) -> None:
        raise ImportError("optional index dependency unavailable")

    monkeypatch.setattr(retrieval_engine.importlib, "import_module", missing_module)
    with pytest.raises(ImportError):
        await retrieval_engine.purge_project_indexes(uuid4(), tmp_path)
