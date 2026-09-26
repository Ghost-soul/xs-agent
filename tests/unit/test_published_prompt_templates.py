import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from novel_writer.generation.prompt_templates import checked_bundle
from novel_writer.generation.template_catalog import default_text
from novel_writer.services import prompt_template_store
from novel_writer.services.errors import ConflictError, WorkflowError
from novel_writer.services.prompt_template_store import PromptTemplateStore


def published(tmp_path):
    value = PromptTemplateStore._bundle(
        str(uuid4()), {"chief": default_text("chief").model_dump()}, "公开测试模板", "",
    )
    path = tmp_path / "published.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path, value


def test_first_install_saves_version_and_repeated_start_preserves_author_changes(tmp_path):
    source, expected = published(tmp_path)
    store = PromptTemplateStore(tmp_path / "instance" / "profiles.json")
    assert store.seed_from(source)
    assert store.current() == expected == store.version(expected["revision"])
    author = store.save("chief", default_text("chief").model_copy(
        update={"system_text": "作者修改"},
    ), expected["revision"], "本地")
    source.unlink()  # Existing settings must not depend on the published file anymore.
    assert not store.seed_from(source)
    assert store.current() == author


@pytest.mark.parametrize("state", ["empty", "corrupt"])
def test_explicit_reset_or_corrupt_local_pointer_is_never_replaced(tmp_path, state):
    source, _ = published(tmp_path)
    store = PromptTemplateStore(tmp_path / "instance" / "profiles.json")
    store.save("chief", None, "builtin", "主动恢复内置")
    path = store.root / "current.json"
    if state == "corrupt":
        path.write_bytes(b"broken")
    previous = path.read_bytes()
    assert not store.seed_from(source)
    assert path.read_bytes() == previous


def test_seed_failure_can_retry_without_rewriting_saved_history(tmp_path, monkeypatch):
    source, expected = published(tmp_path)
    store = PromptTemplateStore(tmp_path / "instance" / "profiles.json")
    real_write = prompt_template_store.atomic_private_write

    def fail_pointer(path, data):
        if path.name == "current.json":
            raise OSError("simulated disk failure")
        real_write(path, data)

    monkeypatch.setattr(prompt_template_store, "atomic_private_write", fail_pointer)
    with pytest.raises(OSError):
        store.seed_from(source)
    version = store.root / "versions" / f"{expected['revision']}.json"
    before = version.stat().st_mtime_ns
    monkeypatch.setattr(prompt_template_store, "atomic_private_write", real_write)
    assert store.seed_from(source)
    assert version.stat().st_mtime_ns == before
    assert store.current() == expected


def test_existing_different_version_and_invalid_source_fail_closed(tmp_path):
    source, value = published(tmp_path)
    store = PromptTemplateStore(tmp_path / "instance" / "profiles.json")
    version = store.root / "versions" / f"{value['revision']}.json"
    version.parent.mkdir(parents=True)
    different = PromptTemplateStore._bundle(value["revision"], {}, "different", "")
    version.write_text(json.dumps(different))
    with pytest.raises(ConflictError):
        store.seed_from(source)
    assert not (store.root / "current.json").exists()
    assert store.version(value["revision"]) == different
    source.write_text("broken")
    with pytest.raises(WorkflowError):
        store.seed_from(source)


def test_concurrent_author_save_is_checked_inside_lock(tmp_path, monkeypatch):
    from contextlib import contextmanager

    source, _ = published(tmp_path)
    store = PromptTemplateStore(tmp_path / "instance" / "profiles.json")
    real_lock = store._lock
    author = PromptTemplateStore._bundle(str(uuid4()), {}, "author", "")

    @contextmanager
    def lock_after_author_saved():
        with real_lock():
            prompt_template_store.atomic_private_write(
                store.root / "current.json", json.dumps(author).encode(),
            )
            yield

    monkeypatch.setattr(store, "_lock", lock_after_author_saved)
    assert not store.seed_from(source)
    assert store.current() == author


def test_published_asset_contains_only_valid_template_snapshot():
    source = Path(__file__).resolve().parents[2] / "configs/prompt-templates/published.json"
    value = checked_bundle(json.loads(source.read_text(encoding="utf-8")))
    assert set(value) == {"format", "revision", "templates", "note", "created_at", "sha256"}
    assert set(value["templates"]) == {"chief", "writer"}
    for template in value["templates"].values():
        assert set(template) == {"system_text", "task_template"}
    frozen = deepcopy(value)
    assert checked_bundle(frozen) == value
