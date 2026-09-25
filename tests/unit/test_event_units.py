import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from novel_writer.generation import budget, event_units, narrative_drive
from novel_writer.generation.content import fingerprint
from novel_writer.providers.deepseek import DeepSeekChatProvider
from tests.unit.test_generation_context_budget import profile
from tests.unit.test_narrative_drive import POLICIES
from tests.unit.test_narrative_drive import fixture as previous_fixture
from tests.unit.test_narrative_prompts import without_descriptions
from tests.unit.test_role_context import configured


def fixture(**changes):
    spec, snapshot, plan = previous_fixture(**changes)
    return spec.model_copy(update={"narrative_policy": "plot-led-v3"}), snapshot, plan


def test_sixteen_prior_contracts_and_160_renders_remain_exact():
    cases = json.loads(Path("tests/fixtures/generation-pre-event-units.json").read_text())
    for case in cases:
        spec, snap, plan = configured()
        spec = spec.model_copy(
            update={
                "context_policy": case["context"],
                "narrative_policy": case["narrative"],
            }
        )
        assert event_units.contract_for(spec) == case["contract"]
        for action, sha in case["renders"].items():
            assert (
                fingerprint(event_units.render_for(spec, snap, action, plan, "source paragraph"))
                == sha
            ), (case["context"], case["narrative"], action)


@pytest.mark.parametrize("context", POLICIES)
@pytest.mark.parametrize("stage", ["single-unit-v1", "longform-v1"])
def test_event_guidance_preserves_cards_facts_schema_and_author_edits(context, stage):
    spec, snap, plan = fixture(context_policy=context, stage_mode=stage)
    original = deepcopy(snap)
    for action in ("plan", "write" if stage == "single-unit-v1" else "write:2"):
        before = narrative_drive.render_for(event_units.previous_spec(spec), snap, action, plan)
        system, raw = event_units.render_for(spec, snap, action, plan)
        old, new = json.loads(before[1]), json.loads(raw)
        assert system.startswith(event_units.CHIEF if action == "plan" else event_units.WRITER)
        assert "unit_scope_guidance" in new
        new.pop("unit_scope_guidance")
        assert without_descriptions(new) == without_descriptions(old)
    assert snap == original


@pytest.mark.parametrize("action", ["rewrite", "amend", "memory:1", "checker", "reader", "title"])
def test_new_pacing_does_not_expand_independent_revision_or_evidence_roles(action):
    spec, snap, plan = fixture()
    reports = {"edit_scope": {"instruction": "只改称谓，保持原情节", "paragraph_ids": []}}
    args = (snap, action, plan, "原始正文", None, reports)
    assert event_units.render_for(spec, *args) == narrative_drive.render_for(
        event_units.previous_spec(spec), *args
    )


@pytest.mark.parametrize("support", [True, False])
@pytest.mark.parametrize("action", ["write", "write:1", "write:5", "rewrite"])
def test_writer_reasoning_disabled_without_changing_limits_or_other_roles(support, action):
    spec, _, _ = fixture()
    capable = profile()
    capable.supports_reasoning_effort = support
    capable.models[0].max_output_tokens = 100000
    spec = spec.model_copy(update={"writer_model": "test", "chief_model": "test", "roles": {}})
    old = budget.request_for(event_units.previous_spec(spec), capable, action, "s", "u")
    new = budget.request_for(spec, capable, action, "s", "u")
    assert old.reasoning_effort == ("medium" if support else None)
    assert new.reasoning_effort == ("none" if support else None)
    assert new.model_dump(exclude={"reasoning_effort"}) == old.model_dump(
        exclude={"reasoning_effort"}
    )
    for role in ("plan", "checker", "memory:1", "amend", "title"):
        assert budget.request_for(spec, capable, role, "s", "u") == budget.request_for(
            event_units.previous_spec(spec), capable, role, "s", "u"
        )


@pytest.mark.asyncio
async def test_actual_deepseek_wire_disables_writer_thinking_and_keeps_chief_enabled():
    spec, _, _ = fixture()
    capable = profile()
    capable.supports_reasoning_effort = True
    capable.models[0].max_output_tokens = 100000
    spec = spec.model_copy(update={"writer_model": "test", "chief_model": "test", "roles": {}})
    sent = []

    async def transport(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "offline",
                "choices": [
                    {"message": {"content": "现场行动已经结束。"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = DeepSeekChatProvider(client=client)
        for action in ("plan", "write:1", "rewrite"):
            await provider.generate(budget.request_for(spec, capable, action, "s", "u"), "fixture")
    assert sent[0]["thinking"] == {"type": "enabled"}
    assert sent[0]["reasoning_effort"] == "medium"
    for payload in sent[1:]:
        assert payload["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in payload
        assert payload["max_tokens"] == spec.writer_output_limit
