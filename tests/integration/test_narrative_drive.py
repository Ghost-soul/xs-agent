# ruff: noqa: F401 F811
import json

import pytest

from novel_writer.generation.budget import input_tokens, request_preview
from novel_writer.generation.narrative_drive import CHIEF, WRITER, contract_for
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
    stage_create,
    start,
    test_failed_writer_replays_exact_new_request_without_search,
    test_five_units_actual_requests_opening_world_queries_and_handoffs,
    test_independent_amendment_uses_new_default_without_changing_old_batch,
    test_input_capacity_preview_and_resume_keep_validated_handoff,
    test_memory_capacity_recovery_preserves_new_opening_and_receipt,
    test_single_unit_actual_requests_complete_all_planned_scenes,
)


@pytest.fixture(autouse=True)
def new_narrative_policy(monkeypatch):
    monkeypatch.setitem(previous.OPTIONS, "context_policy", "chief-focus-v4")
    monkeypatch.setitem(previous.OPTIONS, "narrative_policy", "plot-led-v2")


def test_actual_chief_and_writer_share_frozen_narratives_and_count_complete_prompts(automated):
    client, control = automated
    selected = ["hidden_identity", "farming_infrastructure"]
    base, draft = stage_create(client, unit_limit=2, **{**OPTIONS, "narrative_card_ids": selected})
    frozen = FrozenGenerationSpec.model_validate(draft["spec"])
    assert draft["snapshot"]["prompt_contract_sha256"] == contract_for(frozen)
    done = start(client, base, draft)
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    assert control["calls"] == ["plan", "write:1", "memory:1", "write:2", "memory:2", "checker"]
    cards = {c["id"]: c for c in draft["snapshot"]["cards"]}
    for call in done["calls"]:
        saved = read(client, f"{base}/{done['id']}/calls/{call['id']}")["request"]
        request = ModelRequest.model_validate(saved["model_request"])
        packet = json.loads(request.user_prompt)
        assert saved["input_tokens"] == input_tokens(request_preview(request), saved["counting"])
        assert saved["feedback_options"]["narrative_policy"] == "plot-led-v2"
        if call["action"] == "plan":
            assert request.system_prompt.startswith(CHIEF)
            assert saved["input_tokens"] == draft["snapshot"]["plan_input_tokens"]
            assert packet["narrative_design"]["selected_cards"] == [cards[i] for i in selected]
        elif call["action"].startswith("write:"):
            assert request.system_prompt.startswith(WRITER)
            assert packet["plot_execution"]["selected_narratives"] == [
                {"id": i, "name": cards[i]["name"]} for i in selected
            ]
        else:
            assert "narrative_design" not in packet and "plot_execution" not in packet
    assert done["snapshot"] == draft["snapshot"]
