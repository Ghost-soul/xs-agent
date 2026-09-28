from copy import deepcopy

import pytest

from novel_writer.generation import format_trial, reliability_contract, trial_prompts
from novel_writer.generation.content import json_text
from novel_writer.generation.trial_pipeline import scheduling_plan
from novel_writer.services.errors import WorkflowError
from tests.unit.test_editable_rules import bind


def test_program_schedule_groups_all_excess_material_without_increasing_calls():
    spec, _, _ = bind()
    raw = json_text({"scenes": [{"event": f"event-{i}"} for i in range(11)]})
    plan = scheduling_plan(raw, spec)
    assert len(plan["scenes"]) == spec.unit_limit
    assert plan["raw_response"] == raw
    assert plan["trial_schedule"]["grouped"]
    material = " ".join(s["unvalidated_chief_material"] for s in plan["scenes"])
    assert all(f'"event-{i}"' in material for i in range(11))
    assert all(s["character_ids"] == [] for s in plan["scenes"])


@pytest.mark.parametrize(
    "raw", ["[]", "false", "设计原文", '{"scenes": {}}', '{"scenes": []}', '{"scenes": null}']
)
def test_unreadable_plan_uses_author_slots_without_inventing_cast(raw):
    spec, _, _ = bind()
    plan = scheduling_plan(raw, spec)
    assert plan["trial_schedule"]["fallback"]
    assert len(plan["scenes"]) == spec.stage_scale.preferred_units
    assert all(s["character_ids"] == [] for s in plan["scenes"])
    assert plan["raw_response"] == raw


def test_standard_contract_and_rendering_are_identical_and_trial_binding_is_checked():
    spec, snapshot, plan = bind()
    reliability_contract.bind_snapshot(spec, snapshot)
    before = deepcopy(snapshot)
    assert trial_prompts.contract_for(spec, snapshot) == reliability_contract.contract_for(
        spec, snapshot
    )
    for action in ("plan", "write:1"):
        assert trial_prompts.render_for(
            spec, snapshot, action, plan
        ) == reliability_contract.render_for(
            spec,
            snapshot,
            action,
            plan,
        )
    assert snapshot == before
    format_trial.bind_snapshot(spec, snapshot)
    assert trial_prompts.contract_for(spec, snapshot) != reliability_contract.contract_for(
        spec, snapshot
    )
    snapshot[format_trial.KEY]["sha256"] = "0" * 64
    with pytest.raises(WorkflowError, match="冻结预览"):
        trial_prompts.render_for(spec, snapshot, "plan")
