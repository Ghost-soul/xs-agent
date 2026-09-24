import json
from copy import deepcopy
from pathlib import Path

import pytest

from novel_writer.generation import narrative_prompts, plan_capacity
from novel_writer.generation.content import fingerprint, json_text
from novel_writer.generation.schemas import FrozenGenerationSpec, NovelRunSpec
from tests.unit.test_generation_guidance import setup
from tests.unit.test_narrative_prompts import fixture


def test_historical_contracts_and_causal_requests_remain_exact():
    data = json.loads(Path("tests/fixtures/generation-pre-narrative-prompts.json").read_text())
    original = FrozenGenerationSpec.model_validate(data["spec"])
    for case in data["cases"]:
        spec = original.model_copy(update=case["changes"])
        assert plan_capacity.contract_for(spec) == case["contract"]
        assert plan_capacity.amendment_contract(spec) == case["amendment_contract"]
        for action, sha in case["render_sha256"].items():
            args = (spec, data["snapshot"], action, data["plan"], data["body"], data["author_note"])
            assert fingerprint(plan_capacity.render_for(*args)) == sha
    saved = json.loads(Path("tests/fixtures/generation-pre-plan-capacity.json").read_text())
    spec, snap, plan = fixture()
    assert plan_capacity.contract_for(spec) == saved["contract"]
    assert plan_capacity.amendment_contract(spec) == saved["amendment_contract"]
    for action, sha in saved["renders"].items():
        assert fingerprint(plan_capacity.render_for(spec, snap, action, plan, "已写事实")) == sha


@pytest.mark.parametrize("count", [1, 3, 5])
def test_only_cardinality_is_relaxed_without_mutating_frozen_spec(count):
    spec, snap, plan = setup(unit_limit=5)
    plan["scenes"] = [deepcopy(plan["scenes"][0]) for _ in range(count)]
    before = deepcopy((spec, snap, plan))
    assert len(plan_capacity.parse_stage(json_text(plan), spec, snap).scenes) == count
    assert (spec, snap, plan) == before
    plan["scenes"][-1]["character_ids"] = ["outside-cast"]
    with pytest.raises(ValueError, match="人物越界"):
        plan_capacity.parse_stage(json_text(plan), spec, snap)


@pytest.mark.parametrize("count", [0, 6])
def test_empty_or_over_budget_plans_still_fail(count):
    spec, snap, plan = setup(unit_limit=5)
    plan["scenes"] = [deepcopy(plan["scenes"][0]) for _ in range(count)]
    with pytest.raises(ValueError, match="1 至 5"):
        plan_capacity.parse_stage(json_text(plan), spec, snap)


@pytest.mark.parametrize("action,completed", [("plan", 0), ("chief:1", 2)])
def test_new_chief_prompt_and_schema_agree_with_authorized_range(action, completed):
    spec, snap, plan = fixture(unit_limit=5, plan_policy="bounded-v1")
    system, raw = plan_capacity.render_for(
        spec, snap, action, plan, "已写事实", reports={"completed_units": completed}
    )
    assert "1 至 unit_limit" in system and "scenes 等于 unit_limit" not in system
    value = json.loads(raw)
    schema = value["output_schema"]
    if action != "plan":
        schema = schema["$defs"]["BackgroundPlan"]
    assert schema["properties"]["scenes"]["minItems"] == max(1, completed)
    assert schema["properties"]["scenes"]["maxItems"] == 5
    assert value["narrative_design"]["selected_cards"] == [snap["cards"][1]]
    assert plan_capacity.contract_for(spec) != narrative_prompts.contract_for(spec)


@pytest.mark.parametrize("policy", ["exact-v1", "bounded-v1"])
def test_short_plan_writer_finishes_at_real_end_with_stage_length_preserved(policy):
    spec, snap, plan = fixture(unit_limit=5, chapter_count=2, plan_policy=policy)
    plan["scenes"] = [deepcopy(plan["scenes"][0]) for _ in range(3)]
    _, raw = plan_capacity.render_for(spec, snap, "write:3", plan, "已写事实")
    value = json.loads(raw)
    assert value["unit_position"] == {"current": 3, "total": 3, "last_unit": True}
    assert value["unit_target_characters"] == round(spec.target_characters * 2 / 3)
    with pytest.raises(ValueError, match="超出有效计划"):
        plan_capacity.render_for(spec, snap, "write:4", plan)


def test_only_new_specs_default_to_bounded_plans():
    spec, _, _ = fixture()
    payload = spec.model_dump(mode="json")
    payload.pop("plan_policy")
    assert NovelRunSpec.model_validate(payload).plan_policy == "bounded-v1"
    assert FrozenGenerationSpec.model_validate(payload).plan_policy == "exact-v1"
