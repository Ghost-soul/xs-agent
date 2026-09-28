from copy import deepcopy

import pytest

from novel_writer.generation import progression_contract as contract
from novel_writer.generation import progression_rules as rules
from novel_writer.generation import reliability_contract, trial_prompts
from novel_writer.generation.content import fingerprint
from novel_writer.generation.craft_templates import default_text
from novel_writer.services.errors import WorkflowError
from tests.unit.test_editable_rules import bind
from tests.unit.test_output_reliability import profile, request


def render(spec, snapshot, plan, action, renderer=contract, reports=None):
    reports = {} if reports is None else reports
    system, task = renderer.render_for(spec, snapshot, action, plan, reports=reports)
    wire = renderer.prepare_output(request().model_copy(update={
        "system_prompt": system, "user_prompt": task,
    }), profile(), action, snapshot, reports)
    return wire, reports


@pytest.mark.parametrize("action", ["plan", "write:1", "memory:1", "checker", "rewrite"])
def test_existing_requests_and_receipts_do_not_upgrade(action):
    spec, snapshot, plan = bind()
    reliability_contract.bind_snapshot(spec, snapshot)
    before = deepcopy(snapshot)
    assert render(spec, snapshot, plan, action) == render(
        spec, snapshot, plan, action, trial_prompts,
    )
    assert contract.contract_for(spec, snapshot) == trial_prompts.contract_for(spec, snapshot)
    assert snapshot == before


@pytest.mark.parametrize("variant,action,key", [
    ("chief", "plan", "chief_scope"), ("writer", "write:1", "writer_scope"),
])
def test_new_guidance_keeps_author_text_data_schema_and_capacity(variant, action, key):
    spec, snapshot, plan = bind(variant=variant)
    reliability_contract.bind_snapshot(spec, snapshot)
    bundle = snapshot["prompt_templates"]
    bundle["templates"][variant] = default_text(variant).model_copy(update={
        "system_text": "AUTHOR_SYSTEM_KEEP",
    }).model_dump()
    bundle["sha256"] = fingerprint({k: v for k, v in bundle.items() if k != "sha256"})
    old_wire, old_reports = render(spec, snapshot, plan, action)
    contract.bind_snapshot(spec, snapshot)
    before = deepcopy((snapshot, plan))
    wire, reports = render(spec, snapshot, plan, action)
    assert wire.system_prompt.startswith("AUTHOR_SYSTEM_KEEP")
    assert wire.system_prompt.count(rules.SCOPES[key]) == 1
    assert rules.previous.SCOPES[key] not in wire.system_prompt
    assert wire.user_prompt == old_wire.user_prompt
    assert wire.json_schema == old_wire.json_schema
    assert wire.max_output_tokens == old_wire.max_output_tokens
    assert wire.reasoning_effort == old_wire.reasoning_effort
    assert reports["prompt_template_source"] == old_reports["prompt_template_source"]
    assert reports[contract.KEY] == snapshot[contract.KEY]
    if variant == "writer":
        assert reports["writer_scale"] == old_reports["writer_scale"]
    assert (snapshot, plan) == before


@pytest.mark.parametrize("custom", ["AUTHOR_SCOPE", ""])
@pytest.mark.parametrize("variant,action,key", [
    ("chief", "plan", "chief_scope"), ("writer", "write:1", "writer_scope"),
])
def test_custom_and_explicitly_cleared_rules_win(custom, variant, action, key):
    spec, snapshot, plan = bind(variant=variant, texts={key: custom})
    reliability_contract.bind_snapshot(spec, snapshot)
    contract.bind_snapshot(spec, snapshot)
    wire, reports = render(spec, snapshot, plan, action)
    assert reports["prompt_program_settings"][variant]["texts"][key] == custom
    assert rules.SCOPES[key] not in wire.system_prompt
    assert not custom or wire.system_prompt.count(custom) == 1


def test_natural_size_and_independent_revisions_do_not_acquire_expansion_targets():
    spec, snapshot, plan = bind()
    reliability_contract.bind_snapshot(spec, snapshot)
    contract.bind_snapshot(spec, snapshot)
    natural = spec.model_copy(update={"stage_scale": spec.stage_scale.model_copy(update={
        "scale_mode": "natural",
    })})
    for action in ("plan", "write:1", "rewrite"):
        wire, _ = render(natural, snapshot, plan, action)
        assert rules.CHIEF_SCOPE not in wire.system_prompt
        assert rules.WRITER_SCOPE not in wire.system_prompt
    # Old amendment receipts explicitly suppress the original stage's new binding.
    old = render(spec, snapshot, plan, "rewrite", trial_prompts, {contract.KEY: None})
    assert render(spec, snapshot, plan, "rewrite", reports={contract.KEY: None}) == old


def test_forged_new_binding_is_rejected_without_downgrading():
    spec, snapshot, plan = bind()
    reliability_contract.bind_snapshot(spec, snapshot)
    contract.bind_snapshot(spec, snapshot)
    snapshot[contract.KEY]["sha256"] = "tampered"
    with pytest.raises(WorkflowError, match="冻结"):
        render(spec, snapshot, plan, "plan")
