# ruff: noqa: F401 F811
import pytest

from tests.integration import test_role_context as previous
from tests.integration.support import pytestmark as pytestmark
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
    test_failed_writer_replays_exact_new_request_without_search,
    test_five_units_actual_requests_opening_world_queries_and_handoffs,
    test_independent_amendment_uses_new_default_without_changing_old_batch,
    test_input_capacity_preview_and_resume_keep_validated_handoff,
    test_memory_capacity_recovery_preserves_new_opening_and_receipt,
    test_single_unit_actual_requests_complete_all_planned_scenes,
)
from tests.integration.test_step_recovery import authorize


@pytest.fixture(autouse=True)
def new_context_policy(monkeypatch):
    monkeypatch.setitem(previous.OPTIONS, "context_policy", "chief-focus-v4")


def test_saved_chief_selection_and_actual_request_replay_keep_v4_contract(recovery, monkeypatch):
    client, control = recovery
    control["retry_failure_action"] = "plan"
    base, draft = stage_create(client, unit_limit=2, **OPTIONS)
    before = start(client, base, draft)
    assert before["status"] == "outcome_uncertain"
    assert before["step_recovery_available"]
    assert before["spec"]["context_policy"] == "chief-focus-v4"
    assert before["snapshot"]["plan_context_selection"]["policy"] == "chief-focus-v4"
    target = f"{base}/{before['id']}"
    call = before["calls"][-1]
    saved = read(client, target + f"/calls/{call['id']}")["request"]
    assert saved["key_context_selection"]["policy"] == "chief-focus-v4"
    assert "protected_continuity" in saved["key_context_selection"]
    preview = read(client, target + "/step-recovery-preview")
    del control["retry_failure_action"]
    control["pause_action"] = "plan"

    async def unexpected(*args, **kwargs):
        raise AssertionError("Retry must replay the original request without retrieval")

    monkeypatch.setattr(previous.KnowledgeService, "retrieve", unexpected)
    assert authorize(client, target, preview).status_code == 200
    done = settle(client, base, before["id"])
    replacement = read(client, target + f"/calls/{done['calls'][-1]['id']}")["request"]
    assert replacement["model_request"] == saved["model_request"]
    assert replacement["knowledge_retrieval"] == saved["knowledge_retrieval"]
