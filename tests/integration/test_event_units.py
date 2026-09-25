# ruff: noqa: F401 F811
import json

import pytest

from novel_writer.generation.budget import input_tokens, request_preview
from novel_writer.generation.event_units import CHIEF, WRITER, contract_for
from novel_writer.generation.schemas import FrozenGenerationSpec
from novel_writer.providers.base import ModelRequest
from tests.integration import test_role_context as previous
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_chief_focus import (
    test_saved_chief_selection_and_actual_request_replay_keep_v4_contract,
)
from tests.integration.test_role_context import (
    OPTIONS,
    automated,
    generation,
    longform,
    novel_generation,
    post,
    read,
    recovery,
    settle,
    stage_create,
    start,
    test_five_units_actual_requests_opening_world_queries_and_handoffs,
    test_independent_amendment_uses_new_default_without_changing_old_batch,
    test_input_capacity_preview_and_resume_keep_validated_handoff,
    test_memory_capacity_recovery_preserves_new_opening_and_receipt,
    test_single_unit_actual_requests_complete_all_planned_scenes,
)
from tests.integration.test_step_recovery import authorize, fail


@pytest.fixture(autouse=True)
def new_policy(monkeypatch):
    monkeypatch.setitem(previous.OPTIONS, "context_policy", "chief-focus-v4")
    monkeypatch.setitem(previous.OPTIONS, "narrative_policy", "plot-led-v3")


def enable_reasoning_capability(client):
    profile = client.app.state.provider_profile_store.get("fixture")
    profile.supports_reasoning_effort = True
    client.app.state.provider_profile_store.save(profile)


def test_actual_preview_and_dispatch_use_event_scope_and_writer_without_reasoning(automated):
    client, control = automated
    enable_reasoning_capability(client)
    base, draft = stage_create(client, unit_limit=2, **OPTIONS)
    assert draft["snapshot"]["prompt_contract_sha256"] == contract_for(
        FrozenGenerationSpec.model_validate(draft["spec"])
    )
    done = start(client, base, draft)
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    assert control["calls"] == ["plan", "write:1", "memory:1", "write:2", "memory:2", "checker"]
    for call in done["calls"]:
        saved = read(client, f"{base}/{done['id']}/calls/{call['id']}")["request"]
        request = ModelRequest.model_validate(saved["model_request"])
        packet = json.loads(request.user_prompt)
        assert saved["input_tokens"] == input_tokens(request_preview(request), saved["counting"])
        if call["action"] == "plan":
            assert request.system_prompt.startswith(CHIEF)
            assert request.reasoning_effort == "medium"
            assert saved["input_tokens"] == draft["snapshot"]["plan_input_tokens"]
        elif call["action"].startswith("write:"):
            assert request.system_prompt.startswith(WRITER)
            assert request.reasoning_effort == "none"
            assert "unit_scope_guidance" in packet
    assert done["snapshot"] == draft["snapshot"]


@pytest.mark.parametrize("policy,effort", [("plot-led-v2", "medium"), ("plot-led-v3", "none")])
def test_retry_replays_original_reasoning_policy_and_request(recovery, monkeypatch, policy, effort):
    client, control = recovery
    enable_reasoning_capability(client)
    _, _, base, target, before = fail(recovery, **{**OPTIONS, "narrative_policy": policy})
    failed = next(c for c in before["calls"] if c["action"] == "write:1")
    saved = read(client, target + f"/calls/{failed['id']}")["request"]
    assert saved["model_request"]["reasoning_effort"] == effort
    preview = read(client, target + "/step-recovery-preview")
    del control["retry_failure_action"]
    control["pause_action"] = "write:1"

    async def unexpected(*args, **kwargs):
        raise AssertionError("Retry must preserve its original request without retrieval")

    monkeypatch.setattr(previous.KnowledgeService, "retrieve", unexpected)
    assert authorize(client, target, preview).status_code == 200
    done = settle(client, base, before["id"])
    replacement = read(client, target + f"/calls/{done['calls'][-1]['id']}")["request"]
    assert replacement["model_request"] == saved["model_request"]
    assert replacement["knowledge_retrieval"] == saved["knowledge_retrieval"]
