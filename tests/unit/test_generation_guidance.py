import json
from copy import deepcopy
from pathlib import Path

import pytest

from novel_writer.generation import background, guidance, logic
from novel_writer.generation.novel import slots_for
from novel_writer.generation.schemas import AmendmentRequest, BackgroundPlan, FrozenGenerationSpec
from tests.unit.test_generation_casting import roster, snapshot
from tests.unit.test_generation_creation import creative
from tests.unit.test_genre_generation import plan


def setup(**changes):
    spec = creative(writing_policy="guided-v1", feedback_policy="logic-v1", **changes)
    snap = snapshot(spec, roster())
    snap["cards"] = [
        {"id": "world", "name": "奇幻", "text": "世界卡原文", "sha256": "world"},
        {"id": spec.focus_card_id, "name": "百合", "text": "百合卡原文", "sha256": "gl"},
    ]
    ids = [c["id"] for c in snap["context"]["characters"]][:2]
    snap["context"]["relationships"] = [
        {
            "source_character_id": ids[0],
            "target_character_id": ids[1],
            "relation_type": "约定再见",
            "description": "已经留下记号，尚未确认爱情",
        }
    ]
    snap["context"]["reference_style"] = {"positive_contract": "克制叙事"}
    p = BackgroundPlan.model_validate(plan(ids)).model_dump(mode="json")
    return spec, snap, p


def test_all_pre_guidance_contracts_and_amendment_bindings_stay_frozen():
    saved = json.loads(Path("tests/fixtures/generation-pre-guidance-contracts.json").read_text())
    for key, sha in saved.items():
        stage, feedback, writing = key.split("|")
        spec = creative(
            stage_mode=stage,
            unit_limit=1 if stage == "single-unit-v1" else 2,
            feedback_policy=feedback,
            writing_policy=writing,
        )
        assert guidance.contract_for(spec) == sha
        assert guidance.amendment_contract(spec) == logic.contract_for(spec)
    spec, _, _ = setup()
    assert guidance.contract_for(spec) != background.contract_for(guidance.previous_spec(spec))
    assert guidance.amendment_contract(spec) == guidance.contract_for(spec)
    assert slots_for(spec) == slots_for(guidance.previous_spec(spec))
    payload = spec.model_dump()
    payload.pop("writing_policy")
    assert FrozenGenerationSpec.model_validate(payload).writing_policy == "legacy-v1"
    assert AmendmentRequest.model_fields["writing_policy"].default == "guided-v1"


@pytest.mark.parametrize("action", ["plan", "chief:1"])
def test_chief_keeps_author_genre_selection_without_new_plan_requirements(action):
    spec, snap, p = setup(supporting_card_id="world", author_boundaries="本阶段不表白")
    before = deepcopy(snap)
    system, raw = guidance.render_for(spec, snap, action, p, "已写正文")
    data = json.loads(raw)
    assert data["genre_direction"] == {
        "focus": {"id": spec.focus_card_id, "name": "百合"},
        "supporting": {"id": "world", "name": "奇幻"},
    }
    assert data["background_cards"] == snap["cards"]
    assert data["author_boundaries"] == "本阶段不表白"
    previous = json.loads(background.render_for(guidance.previous_spec(spec), snap, action, p)[1])
    assert data["output_schema"] == previous["output_schema"]
    assert "不预定告白或最终配对" in system
    assert "chapter_goal、event、choice_and_response、consequence" in system
    assert snap == before


@pytest.mark.parametrize(
    "stage,action",
    [
        ("single-unit-v1", "write"),
        ("longform-v1", "write:2"),
        ("longform-v1", "rewrite"),
    ],
)
def test_writer_receives_concrete_intent_and_existing_relationship_but_no_card(stage, action):
    spec, snap, p = setup(stage_mode=stage, unit_limit=1 if stage == "single-unit-v1" else 2)
    p["scenes"][0]["choice_and_response"] = "她主动邀请，对方同意再见，尚未表白"
    system, raw = guidance.render_for(spec, snap, action, p, "已写正文", "保留拒绝的余地")
    data = json.loads(raw)
    assert data["effective_plan"] == p
    assert data["formal_reference"]["relationships"] == snap["context"]["relationships"]
    assert data["formal_reference"]["characters"][0]["speech_style"] == "林青完整声音档案"
    assert data["formal_reference"]["reference_style"] == {"positive_contract": "克制叙事"}
    assert data["author_decisions"] == "保留拒绝的余地"
    assert not {"cards", "background_cards", "genre_direction"} & data.keys()
    assert "百合卡原文" not in raw and "世界卡原文" not in raw
    assert "不擅自升级关系" in system


@pytest.mark.parametrize("action", ["memory", "memory:1", "memory_amend"])
def test_memory_only_retains_evidenced_changes_without_receiving_creative_intent(action):
    spec, snap, p = setup()
    stage = "longform-v1" if action == "memory:1" else "single-unit-v1"
    spec = spec.model_copy(update={"stage_mode": stage})
    system, raw = guidance.render_for(spec, snap, action, p, "她答应再见。")
    prior_system, prior_raw = background.render_for(
        guidance.previous_spec(spec), snap, action, p, "她答应再见。"
    )
    assert raw == prior_raw and system.startswith(prior_system)
    assert "不从题材或计划推断爱情" in system
    assert "genre_direction" not in raw and "百合卡原文" not in raw


@pytest.mark.parametrize("action", ["checker", "reader", "editor", "title"])
def test_feedback_scope_and_cold_reading_are_unchanged(action):
    spec, snap, p = setup()
    args = (snap, action, p, "公开正文", "秘密作者方向")
    assert guidance.render_for(spec, *args) == background.render_for(
        guidance.previous_spec(spec), *args
    )


@pytest.mark.parametrize("policy", ["legacy-v1", "creative-v1", "background-v1"])
@pytest.mark.parametrize("action", ["plan", "chief:1", "write:1", "memory:1"])
def test_existing_saved_policies_keep_exact_rendering(policy, action):
    spec, snap, p = setup()
    spec = spec.model_copy(update={"writing_policy": policy})
    legacy_plan = plan([c["id"] for c in snap["context"]["characters"]][:2])
    args = (spec, snap, action, legacy_plan, "已有正文")
    assert guidance.render_for(*args) == background.render_for(*args)
