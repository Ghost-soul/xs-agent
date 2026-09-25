import json
from copy import deepcopy
from pathlib import Path

import pytest

from novel_writer.generation import chief_context, narrative_drive
from novel_writer.generation.content import fingerprint
from novel_writer.generation.request_preparation import InputCapacityError, prepare_request
from tests.unit.test_generation_context_budget import profile
from tests.unit.test_narrative_prompts import without_descriptions
from tests.unit.test_role_context import configured

POLICIES = [
    "full-v1",
    "bounded-v1",
    "focused-v1",
    "world-bounded-v1",
    "knowledge-rag-v1",
    "role-rag-v2",
    "role-key-v3",
    "chief-focus-v4",
]


def fixture(**changes):
    spec, snapshot, plan = configured()
    spec = spec.model_copy(
        update={
            "context_policy": "chief-focus-v4",
            "narrative_policy": "plot-led-v2",
            "narrative_card_ids": ["narrative", "second"],
            **changes,
        }
    )
    snapshot["cards"].append({"id": "second", "name": "另一叙事", "text": "SECOND_FULL_CARD"})
    return spec, snapshot, plan


def test_eight_frozen_contracts_and_eighty_requests_are_unchanged():
    cases = json.loads(Path("tests/fixtures/generation-pre-narrative-drive.json").read_text())
    for case in cases:
        spec, snapshot, plan = configured()
        spec = spec.model_copy(update={"context_policy": case["policy"]})
        assert narrative_drive.contract_for(spec) == case["contract"]
        assert narrative_drive.amendment_contract(spec) == case["amendment_contract"]
        for action, sha in case["renders"].items():
            assert (
                fingerprint(
                    narrative_drive.render_for(spec, snapshot, action, plan, "source paragraph")
                )
                == sha
            ), (case["policy"], action)


@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize("action", ["plan", "chief:1"])
def test_cards_lead_plot_design_without_changing_constraints_or_frozen_sources(policy, action):
    spec, snapshot, plan = fixture(context_policy=policy)
    snapshot["cards"].append({"id": "unused", "name": "未选卡", "text": "UNSELECTED_CARD"})
    original = deepcopy(snapshot)
    base = narrative_drive.previous_spec(spec)
    _, old = chief_context.render_for(base, snapshot, action, plan, "已写事件")
    system, raw = narrative_drive.render_for(spec, snapshot, action, plan, "已写事件")
    before, after = json.loads(old), json.loads(raw)
    assert list(after)[:2] == ["story_task", "narrative_design"]
    assert (
        after["narrative_design"]["selected_cards"] == before["narrative_design"]["selected_cards"]
    )
    assert len(after["narrative_design"]["selected_cards"]) == 2
    assert "SECOND_FULL_CARD" in raw and "UNSELECTED_CARD" not in raw
    assert after["formal_reference"] == before["formal_reference"]
    assert after["world_cards"] == before["world_cards"]
    assert after["story_task"] == before["story_task"]
    assert without_descriptions(after["output_schema"]) == without_descriptions(
        before["output_schema"]
    )
    assert system.startswith("【核心创作任务：让选定叙事卡主导本阶段情节】")
    assert "去掉任意一张选定卡" in system and "不输出评分或逐卡验收表" in system
    assert narrative_drive.contract_for(spec) != chief_context.contract_for(base)
    assert snapshot == original
    if action != "plan":
        assert after["current_plan"] == plan
        assert after["written_candidate"] == before["written_candidate"]


@pytest.mark.parametrize("action", ["write", "write:1", "write:2"])
def test_writer_obeys_current_edited_plan_without_expanding_cards_or_later_units(action):
    spec, snapshot, plan = fixture()
    plan["scenes"][1]["choice_and_response"] = "AUTHOR_EDIT_CURRENT_CHOICE"
    system, raw = narrative_drive.render_for(spec, snapshot, action, plan, "此前选择已有后果")
    data = json.loads(raw)
    execution = data["plot_execution"]
    expected = plan["scenes"] if action == "write" else plan["scenes"][int(action[-1]) - 1]
    assert execution["current_task"] == expected
    assert execution["selected_narratives"] == [
        {"id": card["id"], "name": card["name"]} for card in snapshot["cards"][1:]
    ]
    assert "SECOND_FULL_CARD" not in raw and "世界原文" not in raw
    assert "场景" in execution["narrative_mandate"]
    assert "不凭卡名新增身份" in system
    _, old = chief_context.render_for(
        narrative_drive.previous_spec(spec), snapshot, action, plan, "此前选择已有后果"
    )
    assert data["continuity"] == json.loads(old)["continuity"]
    assert data["stage_context"] == json.loads(old)["stage_context"]


@pytest.mark.parametrize(
    "action",
    [
        "memory:1",
        "memory_amend",
        "checker",
        "checker_amend",
        "reader",
        "reader_amend",
        "amend",
        "rewrite",
        "title",
    ],
)
def test_fact_review_cold_reading_and_authorized_revisions_remain_exact(action):
    spec, snapshot, plan = fixture()
    reports = {"edit_scope": {"instruction": "只改语气，保持原事件", "paragraph_ids": []}}
    args = (snapshot, action, plan, "完整原稿", "作者要求", reports)
    assert narrative_drive.render_for(spec, *args) == chief_context.render_for(
        narrative_drive.previous_spec(spec), *args
    )


def test_empty_selection_does_not_invent_a_narrative():
    spec, snapshot, plan = fixture(narrative_card_ids=[])
    system, raw = narrative_drive.render_for(spec, snapshot, "plan", plan)
    assert json.loads(raw)["narrative_design"]["selected_cards"] == []
    assert "不自行补卡" in system
    _, raw = narrative_drive.render_for(spec, snapshot, "write:1", plan)
    assert json.loads(raw)["plot_execution"]["selected_narratives"] == []


def test_full_cards_and_new_guidance_count_toward_hard_input_limit():
    spec, snapshot, _ = fixture(input_limit=8000)
    capable = profile()
    spec = spec.model_copy(update={"chief_model": capable.models[0].id})
    snapshot["profile"] = capable.model_dump(mode="json")
    snapshot["counting"] = {"chief": {"method": "utf8-byte-upper-bound"}}
    snapshot["cards"][-1]["text"] = "完整叙事卡内容" * 2000
    with pytest.raises(InputCapacityError) as failure:
        prepare_request(spec, snapshot, "plan", None, None, None, {}, {})
    request = failure.value.request
    assert request.system_prompt.startswith(narrative_drive.CHIEF)
    assert (
        json.loads(request.user_prompt)["narrative_design"]["selected_cards"][-1]
        == snapshot["cards"][-1]
    )
