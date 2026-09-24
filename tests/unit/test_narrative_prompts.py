import json
from copy import deepcopy
from pathlib import Path

import pytest

from novel_writer.generation import focused_context, narrative_prompts
from novel_writer.generation.amendments import with_limits
from novel_writer.generation.content import fingerprint
from novel_writer.generation.novel import slots_for
from novel_writer.generation.schemas import AmendmentRequest, FrozenGenerationSpec, NovelRunSpec

BASELINE = Path("tests/fixtures/generation-pre-narrative-prompts.json")


def fixture(**changes):
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    spec = FrozenGenerationSpec.model_validate(data["spec"]).model_copy(update={
        "narrative_policy": "causal-v1", "card_selection_policy": "separate-v1",
        "narrative_card_ids": ["narrative"], "context_policy": "focused-v1", **changes,
    })
    return spec, data["snapshot"], data["plan"]


def test_pre_update_contracts_and_168_requests_are_byte_identical():
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    original = FrozenGenerationSpec.model_validate(data["spec"])
    assert original.narrative_policy == "legacy-v1"
    for case in data["cases"]:
        spec = original.model_copy(update=case["changes"])
        assert narrative_prompts.contract_for(spec) == case["contract"]
        assert narrative_prompts.amendment_contract(spec) == case["amendment_contract"]
        for action, expected in case["render_sha256"].items():
            actual = narrative_prompts.render_for(
                spec, data["snapshot"], action, data["plan"], data["body"], data["author_note"]
            )
            assert fingerprint(actual) == expected, (case["changes"], action)


def test_defaults_and_saved_amendment_policies_do_not_rebind_old_authorizations():
    spec, _, _ = fixture()
    data = spec.model_dump(mode="json")
    data.pop("narrative_policy")
    assert NovelRunSpec.model_validate(data).narrative_policy == "causal-v1"
    old = FrozenGenerationSpec.model_validate(data)
    assert old.narrative_policy == "legacy-v1"
    assert AmendmentRequest.model_fields["narrative_policy"].default == "causal-v1"
    assert with_limits(old, {"max_cost_cny": "2"}).narrative_policy == "legacy-v1"
    upgraded = with_limits(old, {"max_cost_cny": "2", "narrative_policy": "causal-v1"})
    assert narrative_prompts.amendment_contract(upgraded) != (
        narrative_prompts.amendment_contract(old)
    )
    assert slots_for(upgraded) == slots_for(old)


def without_descriptions(value):
    if isinstance(value, dict):
        return {k: without_descriptions(v) for k, v in value.items() if k != "description"}
    if isinstance(value, list):
        return [without_descriptions(v) for v in value]
    return value


@pytest.mark.parametrize("action", ["plan", "chief:1"])
def test_chief_groups_frozen_cards_and_guides_existing_fields_without_new_requirements(action):
    spec, snap, plan = fixture()
    snap["cards"].append({"id": "other", "name": "未选卡", "text": "未选卡不能进入请求"})
    before = deepcopy(snap)
    system, text = narrative_prompts.render_for(spec, snap, action, plan, "已写事实")
    value = json.loads(text)
    assert list(value)[:3] == ["story_task", "narrative_design", "world_cards"]
    assert value["narrative_design"]["selected_cards"] == [snap["cards"][1]]
    assert value["world_cards"]["primary"] == snap["cards"][0]
    assert "未选卡不能进入请求" not in text
    previous = json.loads(focused_context.render_for(spec, snap, action, plan, "已写事实")[1])
    assert without_descriptions(value["output_schema"]) == without_descriptions(
        previous["output_schema"]
    )
    assert "下一步受何影响" in text
    assert "移除选定内容" in system and "不输出评分" in system
    assert value["formal_reference"] == previous["formal_reference"]
    assert snap == before
    if action != "plan":
        assert value["current_plan"] == previous["current_plan"]
        assert value["written_candidate"] == previous["written_candidate"]


@pytest.mark.parametrize("action", ["write:1", "write:2", "rewrite", "write"])
def test_writer_receives_current_edited_event_chain_and_full_voices_without_raw_cards(action):
    spec, snap, plan = fixture()
    if action == "write":
        spec = spec.model_copy(update={"stage_mode": "single-unit-v1", "unit_limit": 1})
    plan["scenes"][1]["choice_and_response"] = "作者修改：公开账册后，对方撤回继承要求"
    system, text = narrative_prompts.render_for(
        spec, snap, action, plan, "此前已查明档案来源。", "不改正式身份"
    )
    value = json.loads(text)
    assert value["effective_plan"] == plan
    assert value["plot_execution"]["stage_goal"] == plan["chapter_goal"]
    assert value["story_task"]["author_decisions"] == "不改正式身份"
    assert "世界原文" not in text and "身份调查改变继承选择" not in text
    assert value["formal_reference"]["characters"][0]["speech_style"] == "林青完整声音档案"
    assert value["already_written"] == "此前已查明档案来源。"
    task = value["plot_execution"]["current_task"]
    if action.startswith("write:"):
        assert task == plan["scenes"][int(action[-1]) - 1]
    elif action == "write":
        assert task == plan["scenes"]
    else:
        assert "改写" in task
    assert "选择实际改变了什么" in system


@pytest.mark.parametrize("action", [
    "memory:1", "memory_amend", "checker", "checker_amend", "reader", "reader_amend", "amend",
])
def test_evidence_cold_reading_and_local_editing_keep_their_original_source_boundaries(action):
    spec, snap, plan = fixture(direction="未公开作者意图", author_boundaries="未公开边界")
    reports = {"edit_scope": {"paragraph_ids": [], "instruction": "只改授权段落"}}
    args = (spec, snap, action, plan, "当前公开正文", "未公开作者决定", reports)
    previous_system, previous_text = focused_context.render_for(*args)
    system, text = narrative_prompts.render_for(*args)
    assert system.startswith(previous_system)
    assert json.loads(text) == json.loads(previous_text)
    assert "身份调查改变继承选择" not in text
    assert "世界原文" not in text
    if action.startswith("reader"):
        assert "未公开" not in text
        assert set(json.loads(text)) == {"public_preceding", "candidate"}
    if action.startswith("checker"):
        assert "只列证据充分" in system and "不裁定情节力度" in system
    if action.startswith("memory"):
        assert "不从题材或计划推断事实" in system


def test_empty_selection_does_not_invent_cards_and_title_stays_outside_plot_design():
    spec, snap, plan = fixture(narrative_card_ids=[])
    system, text = narrative_prompts.render_for(spec, snap, "plan")
    assert json.loads(text)["narrative_design"]["selected_cards"] == []
    assert "不自行加入卡片" in system
    assert narrative_prompts.render_for(spec, snap, "title", plan, "正文") == (
        focused_context.render_for(spec, snap, "title", plan, "正文")
    )
