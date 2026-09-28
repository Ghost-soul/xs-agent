from copy import deepcopy

import pytest

from novel_writer.generation import editable_contract as previous
from novel_writer.generation import unit_delivery as delivery
from novel_writer.generation import unit_delivery_rules as rules
from novel_writer.generation.content import fingerprint, parse_object
from novel_writer.generation.craft_templates import default_text
from novel_writer.services.errors import WorkflowError
from tests.unit.test_editable_rules import bind
from tests.unit.test_output_reliability import profile, request


def render(spec, snapshot, plan, action):
    reports = {}
    system, task = delivery.render_for(spec, snapshot, action, plan, reports=reports)
    result = delivery.prepare_output(request().model_copy(update={
        "system_prompt": system, "user_prompt": task,
    }), profile(), action, snapshot, reports)
    return result, reports


@pytest.mark.parametrize("count", range(1, 7))
def test_equal_allocation_preserves_exact_total_and_balanced_remainders(count):
    scale = {"min_characters": 15001, "max_characters": 20003}
    values = [delivery.reference(scale, count, ordinal) for ordinal in range(1, count + 1)]
    for field, total in scale.items():
        assert sum(v[field] for v in values) == total
        assert max(v[field] for v in values) - min(v[field] for v in values) <= 1


@pytest.mark.parametrize("template", [False, True])
def test_actual_writer_target_ignores_unequal_weights_and_keeps_plan_and_template(template):
    spec, snapshot, plan = bind()
    plan["scenes"] = [
        {**deepcopy(plan["scenes"][0]), "size_weight": weight} for weight in (1, 2, 3, 2, 1)
    ]
    if template:
        bundle = snapshot["prompt_templates"]
        bundle["templates"]["writer"] = default_text("writer").model_copy(update={
            "system_text": "AUTHOR_SYSTEM_KEEP",
        }).model_dump()
        bundle["sha256"] = fingerprint({k: v for k, v in bundle.items() if k != "sha256"})
    delivery.bind_snapshot(spec, snapshot)
    before = deepcopy((snapshot, plan))
    for ordinal in range(1, 6):
        wire, reports = render(spec, snapshot, plan, f"write:{ordinal}")
        target = {"min_characters": 3000, "max_characters": 4000}
        assert reports["writer_scale"]["current_unit_reference"] == target
        assert reports["prompt_template_source"]["stage_scale"]["current_unit_reference"] == target
        assert "当前单元目标 3,000–4,000 字" in wire.system_prompt
        assert wire.system_prompt.count(rules.WRITER_SCOPE) == 1
        assert reports[delivery.KEY] == delivery.binding()
        if template:
            assert wire.system_prompt.startswith("AUTHOR_SYSTEM_KEEP")
        else:
            assert parse_object(wire.user_prompt) == reports["prompt_template_source"]
    assert (snapshot, plan) == before


def test_chief_projects_event_outcomes_without_changing_fields_or_domain_schema():
    spec, snapshot, plan = bind()
    _, old = previous.raw_render(spec, snapshot, "plan")
    delivery.bind_snapshot(spec, snapshot)
    wire, reports = render(spec, snapshot, plan, "plan")
    source = reports["prompt_template_source"]
    old_schema = parse_object(old)["output_schema"]
    props = source["output_schema"]["$defs"]["CraftUnit"]["properties"]
    assert set(props) == set(old_schema["$defs"]["CraftUnit"]["properties"])
    assert "阶段性产出" in wire.system_prompt
    assert "不新增文学评分" in wire.system_prompt
    assert source["stage_scale"]["allocation_policy"] == "equal-units-v1"
    assert "均分" in props["size_weight"]["description"]
    assert wire.json_schema == source["output_schema"]


@pytest.mark.parametrize("custom", ["AUTHOR_SCOPE", ""])
@pytest.mark.parametrize("variant,action,key", [
    ("chief", "plan", "chief_scope"), ("writer", "write:1", "writer_scope"),
])
def test_custom_and_explicit_empty_guidance_stay_editable(custom, variant, action, key):
    spec, snapshot, plan = bind(texts={key: custom}, variant=variant)
    delivery.bind_snapshot(spec, snapshot)
    wire, reports = render(spec, snapshot, plan, action)
    assert reports["prompt_program_settings"][variant]["texts"][key] == custom
    assert rules.SCOPES[key] not in wire.system_prompt
    if custom:
        assert wire.system_prompt.count(custom) == 1


@pytest.mark.parametrize("action", ["plan", "write:1", "memory:1", "rewrite", "checker", "title"])
def test_existing_contracts_requests_and_receipts_stay_identical(action):
    spec, snapshot, plan = bind()
    old_reports = {}
    old_system, old_task = previous.render_for(spec, snapshot, action, plan, reports=old_reports)
    old_wire = previous.prepare_output(request().model_copy(update={
        "system_prompt": old_system, "user_prompt": old_task,
    }), profile(), action, snapshot, old_reports)
    wire, reports = render(spec, snapshot, plan, action)
    assert (wire, reports) == (old_wire, old_reports)
    assert delivery.contract_for(spec, snapshot) == previous.contract_for(spec, snapshot)


def test_natural_size_rewrite_and_explicit_historical_amendment_do_not_inherit_target():
    spec, snapshot, plan = bind()
    delivery.bind_snapshot(spec, snapshot)
    spec = spec.model_copy(update={"stage_scale": spec.stage_scale.model_copy(update={
        "scale_mode": "natural",
    })})
    for action in ("plan", "write:1", "rewrite"):
        wire, reports = render(spec, snapshot, plan, action)
        assert rules.CHIEF_SCOPE not in wire.system_prompt
        assert rules.WRITER_SCOPE not in wire.system_prompt
        assert "writer_scale" not in reports
    old, new = {delivery.KEY: None}, {delivery.KEY: None}
    assert delivery.render_for(spec, snapshot, "rewrite", plan, reports=new) == (
        previous.render_for(spec, snapshot, "rewrite", plan, reports=old)
    )
    assert new == old


def test_tampered_policy_or_missing_unit_position_is_rejected_locally():
    spec, snapshot, plan = bind()
    delivery.bind_snapshot(spec, snapshot)
    snapshot[delivery.KEY]["sha256"] = "bad"
    with pytest.raises(WorkflowError, match="冻结"):
        render(spec, snapshot, plan, "plan")
    with pytest.raises(WorkflowError, match="计划位置"):
        delivery.project_source({"stage_scale": {"scale_mode": "stage-range"}}, "write:1")
