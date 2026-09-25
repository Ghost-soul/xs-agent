import json
from copy import deepcopy

import pytest

from novel_writer.generation import unit_scope, world_context
from novel_writer.generation.content import json_text
from novel_writer.generation.schemas import AmendmentRequest, FrozenGenerationSpec, NovelRunSpec
from novel_writer.services.errors import WorkflowError
from tests.unit.test_focused_context import fixture


def setup():
    spec, snapshot, plan = fixture()
    spec = spec.model_copy(
        update={"context_policy": world_context.POLICY, "direction": "在渡口履约"}
    )
    snapshot["context"]["world_rules"] = [{"id": "rule", "statement": "人的记忆不能凭空改变"}]
    snapshot["context"]["world_lore"] = [
        {
            "id": "contract",
            "name": "回潮契约",
            "summary": "渡口履约的条件",
            "category": "society",
            "risk_level": "high",
            "details": ["只有持有旧印才能渡河；遵循 tide-source 的条件"],
        },
        {
            "id": "tide-source",
            "name": "潮汐出处",
            "summary": "旧印只在退潮时生效",
            "category": "history",
            "risk_level": "low",
            "details": ["不能用新印冒充"],
        },
    ]
    return spec, snapshot, plan


def decode(spec, snapshot, plan, action="write:1", body="回潮契约仍然有效。", reports=None):
    system, raw = world_context.render_for(spec, snapshot, action, plan, body, reports=reports)
    return system, json.loads(raw)


@pytest.mark.parametrize(
    "action", ["plan", "write:1", "memory:1", "checker", "rewrite", "memory_amend", "checker_amend"]
)
def test_required_world_facts_dependencies_and_current_evidence_are_preserved(action):
    spec, snapshot, plan = setup()
    before = deepcopy(snapshot)
    system, payload = decode(spec, snapshot, plan, action)
    reference = next(
        payload[key]
        for key in (
            "formal_reference",
            "formal_start",
            "candidate_working_reference",
            "formal_facts",
        )
        if key in payload
    )
    assert reference["world_rules"] == snapshot["context"]["world_rules"]
    assert reference["world_lore"] == snapshot["context"]["world_lore"]
    assert (
        payload["world_context_selection"]["material_count"]
        <= payload["world_context_selection"]["limit"]
    )
    assert "省略不表示不存在" in system
    assert snapshot == before
    if action.startswith("write"):
        assert payload["effective_plan"] == plan
        assert "完整声音样本" in json_text(payload)


@pytest.mark.parametrize("count", [20, 200, 1000])
def test_unrelated_high_risk_library_growth_does_not_expand_the_world_packet(count):
    spec, snapshot, plan = setup()
    snapshot["context"]["world_lore"] += [
        {
            "id": f"remote-{n:04}",
            "name": f"远疆档案编号x{n:04}",
            "summary": "遥远沙漠历史",
            "risk_level": "high",
            "category": "core_secret",
            "details": ["不相关长篇档案" * 250],
        }
        for n in range(count)
    ]
    _, value = decode(spec, snapshot, plan)
    ref = value["formal_reference"]
    assert len(ref["world_lore"]) == 2
    assert len(ref["world_lore_overview"]) <= 8
    assert len(json_text(world_context.world_material(value)).encode()) < 5000
    assert value["world_context_selection"]["omitted_count"] >= count - 8
    assert len(value["context_selection"]["world_lore_full"]) == 2
    assert "不相关长篇档案" not in json_text(value)


def test_history_and_macro_do_not_reintroduce_omitted_world_entries_or_rebind_old_values():
    spec, snapshot, plan = setup()
    remote = {
        "id": "remote",
        "name": "遥远冰海",
        "summary": "偏远风俗",
        "details": ["冰海档案" * 500],
    }
    snapshot["context"]["world_lore"].append(remote)
    current = snapshot["context"]["world_lore"][0]
    old = {**current, "details": ["当时旧印尚未失效"]}
    delta = snapshot["context"]["formal_summaries"][0]["factual_changes"]
    delta.update(add_world_lore=[deepcopy(current), deepcopy(remote)], update_world_lore=[old])
    snapshot["macro_diagnostic"]["actual_recent_changes"][1]["factual_changes"] = deepcopy(delta)
    _, payload = decode(spec, snapshot, plan)
    history = payload["formal_reference"]["formal_summaries"][0]["factual_changes"]
    assert history["add_world_lore"] == [{"same_as": "formal_reference.world_lore[0]"}]
    assert history["update_world_lore"] == [old]
    assert "冰海档案" not in json_text(payload)
    assert (
        snapshot["context"]["formal_summaries"][0]["factual_changes"]["add_world_lore"][1] == remote
    )


def test_working_context_and_directly_referenced_removed_history_keep_temporal_meaning():
    spec, snapshot, plan = setup()
    working = deepcopy(snapshot["context"])
    working["world_lore"][0]["details"] = ["旧印现已被销毁"]
    prior = {"id": "lost", "name": "断潮旧约", "summary": "已废止", "details": ["废止前曾有效"]}
    working["formal_summaries"][0]["factual_changes"].update(
        add_world_lore=[prior], remove_world_lore=["lost"]
    )
    _, payload = decode(
        spec, snapshot, plan, "memory:2", "回潮契约与断潮旧约有别。", {"working_context": working}
    )
    ref = payload["candidate_working_reference"]
    assert ref["world_lore"][0]["details"] == ["旧印现已被销毁"]
    assert not any(entry["id"] == "lost" for entry in ref["world_lore"])
    _, writer = decode(
        spec, snapshot, plan, "write:2", "回潮契约与断潮旧约有别。", {"working_context": working}
    )
    changes = writer["formal_reference"]["formal_summaries"][0]["factual_changes"]
    assert changes["add_world_lore"] == [prior]
    assert changes["remove_world_lore"] == ["lost"]


def test_optional_complete_entry_brings_its_dependencies_or_remains_an_overview():
    spec, snapshot, plan = setup()
    snapshot["context"]["world_lore"] += [
        {
            "id": "opt",
            "name": "远行手册",
            "summary": "渡口履约的习俗",
            "details": ["依赖 hidden-archive"],
        },
        {
            "id": "hidden-archive",
            "name": "深山卷宗",
            "summary": "旧知识",
            "details": ["额外完整条件"],
        },
    ]
    _, value = decode(spec, snapshot, plan)
    chosen = {e["id"] for e in value["formal_reference"]["world_lore"]}
    assert "opt" in chosen and "hidden-archive" in chosen
    assert not chosen & {e["id"] for e in value["formal_reference"]["world_lore_overview"]}


def test_mandatory_overflow_is_explicit_not_silent_truncation():
    spec, snapshot, plan = setup()
    snapshot["context"]["world_rules"][0]["statement"] = "不可删除的正式规则" * 3000
    with pytest.raises(WorkflowError, match="未截断设定、未发送模型请求"):
        decode(spec, snapshot, plan)


def test_world_material_uses_verified_role_tokenizer_and_detects_tampering(tmp_path, monkeypatch):
    from novel_writer.generation import budget
    from tests.unit.test_tokenizer_cache import install

    monkeypatch.setattr(budget, "TOKENIZER_ROOT", tmp_path)
    monkeypatch.setattr(world_context, "TOKENIZER_ROOT", tmp_path)
    install(tmp_path, {"[UNK]": 0, "word": 1})
    config = budget.counting_config("fixture", "fixture")
    spec, snapshot, plan = setup()
    snapshot["counting"] = {"chief": config, "writer": config}
    _, payload = decode(spec, snapshot, plan)
    material = json_text(world_context.world_material(payload))
    expected = budget.input_tokens(material, config) - 2048
    assert payload["world_context_selection"]["material_count"] == expected
    assert payload["world_context_selection"]["counting_method"] == "local-tokenizer"
    (tmp_path / "fixture.json").write_text("changed", encoding="utf-8")
    with pytest.raises(WorkflowError, match="SHA"):
        decode(spec, snapshot, plan)


@pytest.mark.parametrize("policy", ["full-v1", "bounded-v1", "focused-v1"])
def test_old_contracts_and_rendered_requests_remain_exact(policy):
    spec, snapshot, plan = setup()
    spec = spec.model_copy(update={"context_policy": policy})
    assert world_context.contract_for(spec) == unit_scope.contract_for(spec)
    assert world_context.amendment_contract(spec) == unit_scope.amendment_contract(spec)
    for action in ["plan", "write:1", "memory:1", "checker", "reader", "amend"]:
        assert world_context.render_for(
            spec, snapshot, action, plan, "正文"
        ) == unit_scope.render_for(spec, snapshot, action, plan, "正文")


def test_new_defaults_do_not_upgrade_frozen_specs_and_reader_and_editor_remain_scoped():
    assert NovelRunSpec.model_fields["context_policy"].default == "chief-focus-v4"
    assert AmendmentRequest.model_fields["context_policy"].default == "chief-focus-v4"
    assert FrozenGenerationSpec.model_fields["context_policy"].default == "full-v1"
    spec, snapshot, plan = setup()
    for action in ["reader", "amend", "title"]:
        assert world_context.render_for(
            spec, snapshot, action, plan, "正文"
        ) == unit_scope.render_for(
            world_context.previous_spec(spec), snapshot, action, plan, "正文"
        )
