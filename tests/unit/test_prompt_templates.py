import json
from copy import deepcopy

import pytest

from novel_writer.generation import event_units, prompt_templates, template_catalog
from novel_writer.generation.budget import input_tokens, request_preview
from novel_writer.generation.request_preparation import InputCapacityError, prepare_request
from novel_writer.generation.template_catalog import TemplateText, default_text
from novel_writer.services.errors import ConflictError, WorkflowError
from novel_writer.services.prompt_template_store import PromptTemplateStore
from tests.unit.test_event_units import fixture
from tests.unit.test_generation_context_budget import profile


def bundle(tmp_path, variant, text=None):
    store = PromptTemplateStore(tmp_path / "profiles.json")
    return store.save(variant, text or default_text(variant), store.current()["revision"], "测试")


@pytest.mark.parametrize(
    "action",
    [
        "plan",
        "write",
        "write:1",
        "chief:1",
        "rewrite",
        "memory:1",
        "memory_amend",
        "checker",
        "checker_amend",
        "amend",
        "reader",
        "title",
    ],
)
def test_no_template_preserves_prior_contracts_and_renders(action):
    spec, snap, plan = fixture()
    args = (spec, snap, action, plan, "已有正文", None, {})
    assert prompt_templates.render_for(*args) == event_units.render_for(*args)
    assert prompt_templates.contract_for(spec, snap) == event_units.contract_for(spec)
    assert prompt_templates.amendment_contract(spec, snap) == event_units.amendment_contract(spec)


@pytest.mark.parametrize(
    "variant,action",
    [
        ("chief", "plan"),
        ("writer", "write:1"),
        ("rewrite", "rewrite"),
        ("memory", "memory:1"),
        ("checker", "checker"),
        ("editor", "amend"),
        ("title", "title"),
    ],
)
def test_custom_text_replaces_system_preserves_engine_and_exact_sources(tmp_path, variant, action):
    spec, snap, plan = fixture()
    reports = {
        "edit_scope": {"instruction": "只改第一段", "paragraph_ids": []},
        "chapter_bodies": [{"id": "c1", "body": "章节原文"}],
    }
    snap["candidate_chapter"] = {"id": "chapter-1", "ordinal": 1}
    original = deepcopy(snap)
    template = default_text(variant).model_copy(update={"system_text": "AUTHOR_SYSTEM"})
    snap["prompt_templates"] = bundle(tmp_path, variant, template)
    system, task = prompt_templates.render_for(spec, snap, action, plan, "已有正文", None, reports)
    assert system == "AUTHOR_SYSTEM\n\n" + template_catalog.ENGINE_SYSTEM
    assert "【程序输出与来源合同】" in task
    source = reports["prompt_template_source"]
    expected = json.loads(
        event_units.render_for(
            spec,
            original,
            action,
            plan,
            "已有正文",
            None,
            reports,
        )[1]
    )
    assert source == expected
    for field in ("output_schema", "reference_boundary", "scope", "candidate"):
        if field in source:
            assert json.dumps(source[field], ensure_ascii=False, separators=(",", ":")) in task or (
                json.dumps(source[field], ensure_ascii=False) in task
            )
    if variant == "rewrite":
        assert "已有正文" in task and "只改第一段" in task
        assert "单元执行" not in system
    if variant == "title":
        assert '"chapters"' in task and "c1" in task


def test_default_systems_match_actual_active_role_text():
    spec, snap, plan = fixture()
    for variant, action in (
        ("chief", "plan"),
        ("writer", "write:1"),
        ("rewrite", "rewrite"),
        ("memory", "memory:1"),
        ("checker", "checker"),
        ("editor", "amend"),
    ):
        assert (
            default_text(variant).system_text
            == event_units.render_for(
                spec,
                snap,
                action,
                plan,
                "原文",
            )[0]
        )


@pytest.mark.parametrize(
    "suffix,match",
    [
        ("{{unknown}}", "未知"),
        ("{{candidate}}", "重复"),
        ("{{ candidate.__class__ }}", "未知"),
        ("{{ tool() }}", "格式"),
    ],
)
def test_invalid_placeholders_rejected(suffix, match):
    template = default_text("memory")
    template.task_template += suffix
    with pytest.raises(WorkflowError, match=match):
        prompt_templates.validate_template("memory", template)


def test_missing_required_and_optional_omission_and_no_recursive_expansion():
    with pytest.raises(WorkflowError, match="缺少"):
        prompt_templates.validate_template(
            "memory", TemplateText(system_text="s", task_template="hi")
        )
    template = default_text("checker")
    template.task_template = "{{formal_start}}\n正文\n{{candidate}}"
    system, task, audit = prompt_templates.apply_template(
        "checker",
        template,
        {
            "formal_start": {},
            "candidate": [{"id": "p1", "text": "{{formal_start}} <script>"}],
            "output_contract": "JSON",
        },
    )
    assert audit["omitted_optional"] == ["knowledge_context"]
    assert "{{formal_start}} <script>" in task
    assert task.startswith("{}\n正文\n")


def test_store_history_concurrent_edits_reset_and_frozen_bundle(tmp_path):
    store = PromptTemplateStore(tmp_path / "profiles.json")
    assert not store.root.exists()
    first = bundle(tmp_path, "writer")
    snapshot = {"prompt_templates": deepcopy(first)}
    spec, _, _ = fixture()
    contract = prompt_templates.contract_for(spec, snapshot)
    newer = store.save(
        "writer",
        TemplateText(
            system_text="changed",
            task_template=default_text("writer").task_template,
        ),
        first["revision"],
        "第二版",
    )
    assert prompt_templates.contract_for(spec, snapshot) == contract
    with pytest.raises(ConflictError):
        store.save("chief", default_text("chief"), first["revision"], "旧页面")
    restored = store.save("writer", None, newer["revision"], "恢复默认")
    assert restored["templates"] == {}
    assert store.version(first["revision"]) == first
    assert len(store.history()) == 3
    assert store.current() == restored


def test_atomic_failure_preserves_current_and_corruption_fails_closed(tmp_path, monkeypatch):
    from novel_writer.services import prompt_template_store

    store = PromptTemplateStore(tmp_path / "profiles.json")
    first = bundle(tmp_path, "writer")
    real_write = prompt_template_store.atomic_private_write

    def fail_pointer(path, data):
        if path.name == "current.json":
            raise OSError("disk failure")
        real_write(path, data)

    monkeypatch.setattr(prompt_template_store, "atomic_private_write", fail_pointer)
    with pytest.raises(OSError):
        store.save("chief", default_text("chief"), first["revision"], "失败保存")
    assert store.current() == first
    (store.root / "current.json").write_text("broken")
    with pytest.raises(WorkflowError, match="损坏"):
        store.current()


def test_amendment_explicit_none_does_not_inherit_batch_template(tmp_path):
    spec, snap, plan = fixture()
    snap["prompt_templates"] = bundle(tmp_path, "rewrite")
    args = (spec, snap, "rewrite", plan, "原稿", None, {"prompt_templates": None})
    assert prompt_templates.render_for(*args) == event_units.render_for(*args)


def test_tampered_bundle_rejected(tmp_path):
    saved = bundle(tmp_path, "writer")
    saved["templates"]["writer"]["system_text"] = "tampered"
    with pytest.raises(WorkflowError, match="摘要"):
        prompt_templates.checked_bundle(saved)


def test_final_input_capacity_counts_custom_template(tmp_path):
    spec, snap, plan = fixture()
    capable = profile()
    capable.models[0].max_output_tokens = 100000
    capable.models[0].context_window = 300000
    spec = spec.model_copy(update={"writer_model": "test", "chief_model": "test", "roles": {}})
    snap["profile"] = capable.model_dump(mode="json")
    snap["counting"] = {
        "chief": {"method": "utf8-byte-upper-bound"},
        "writer": {"method": "utf8-byte-upper-bound"},
    }
    snap["prompt_templates"] = bundle(tmp_path, "writer")
    reports = {}
    request, count, counting, _ = prepare_request(
        spec, snap, "write:1", plan, None, None, reports, {}
    )
    assert count == input_tokens(request_preview(request), counting)
    assert "prompt_template_source" in reports
    huge = default_text("writer").model_copy(update={"system_text": "大" * 70000})
    snap["prompt_templates"] = bundle(tmp_path, "writer", huge)
    with pytest.raises(InputCapacityError):
        prepare_request(spec, snap, "write:1", plan, None, None, {}, {})


def test_unsupported_policy_cannot_apply_new_template(tmp_path):
    spec, snap, plan = fixture()
    snap["prompt_templates"] = bundle(tmp_path, "writer")
    spec = spec.model_copy(update={"narrative_policy": "plot-led-v2"})
    with pytest.raises(WorkflowError, match="历史策略"):
        prompt_templates.render_for(spec, snap, "write:1", plan)
