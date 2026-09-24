# ruff: noqa: F811
import json

import pytest

from novel_writer.generation.budget import input_tokens, request_preview
from novel_writer.generation.narrative_prompts import contract_for, render_for
from novel_writer.generation.schemas import FrozenGenerationSpec
from novel_writer.providers.base import ModelRequest
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated, settle  # noqa: F401
from tests.integration.test_generation_feedback import feedback_run  # noqa: F401
from tests.integration.test_generation_longform import longform, stage_create  # noqa: F401
from tests.integration.test_genre_generation import generation, post, read, start  # noqa: F401
from tests.integration.test_novel_run_rebuild import current

NARRATIVES = ["hidden_identity", "farming_infrastructure"]


def options(**changes):
    return {
        "writing_policy": "guided-v1", "card_selection_policy": "separate-v1",
        "narrative_policy": "causal-v1", "focus_card_id": "western_fantasy_dnd",
        "narrative_card_ids": NARRATIVES, "context_policy": "focused-v1",
        "feedback_policy": "logic-v1", "automation_policy": "stage-auto-v1", **changes,
    }


@pytest.mark.parametrize("milestone", [None, 2])
def test_actual_requests_carry_causal_design_and_memory_handoff_with_exact_counts(
    feedback_run, milestone,
):
    client, control = feedback_run
    base, draft = stage_create(client, **options(milestone_unit=milestone))
    spec = FrozenGenerationSpec.model_validate(draft["spec"])
    assert draft["snapshot"]["prompt_contract_sha256"] == contract_for(spec)
    batch = start(client, base, draft)
    assert batch["state"]["units_finished"], batch["state"]
    assert all(c["status"] == "completed" for c in batch["calls"])
    assert "reader" not in control["calls"] and "editor" not in control["calls"]
    chief = control["requests"]["plan"]
    assert [c["id"] for c in chief["narrative_design"]["selected_cards"]] == NARRATIVES
    assert chief["story_task"]["author_direction"] == draft["spec"]["direction"]
    plan = current(batch, "plan")["payload"]
    for n in (1, 2, 3):
        writer = control["requests"][f"write:{n}"]
        assert writer["plot_execution"]["current_task"] == plan["scenes"][n - 1]
        assert writer["plot_execution"]["stage_turn"] == plan["major_turn"]
        assert not {"narrative_design", "narrative_cards", "world_cards"} & writer.keys()
        if n > 1:
            assert f"第{n - 1}次选择" in writer["already_written"]
            assert (
                writer["candidate_handoff"]["position"]["recent_major_event"]
                == f"完成memory:{n - 1}"
            )
            assert writer["formal_reference"]["candidate_state_is_formal"] is False
    if milestone:
        checkpoint = control["requests"]["chief:1"]
        assert checkpoint["completed_units"] == 2
        assert checkpoint["narrative_design"] == chief["narrative_design"]
        assert checkpoint["written_candidate"]
    for call in batch["calls"]:
        saved = read(client, f"{base}/{batch['id']}/calls/{call['id']}")["request"]
        request = ModelRequest.model_validate(saved["model_request"])
        assert saved["feedback_options"]["narrative_policy"] == "causal-v1"
        assert saved["input_tokens"] == input_tokens(request_preview(request), saved["counting"])
        if call["action"] == "plan":
            assert saved["input_tokens"] == draft["snapshot"]["plan_input_tokens"]
    assert batch["snapshot"] == draft["snapshot"]


def test_new_amendment_uses_causal_prompts_without_upgrading_original_batch(feedback_run):
    client, control = feedback_run
    profile = client.app.state.provider_profile_store.get("fixture")
    profile.models[0].max_output_tokens = 100000
    profile.models[0].context_window = 300000
    client.app.state.provider_profile_store.save(profile)
    base, draft = stage_create(client, **options(narrative_policy="legacy-v1"))
    batch = start(client, base, draft)
    route = f"{base}/{batch['id']}"
    before_calls = len(control["calls"])
    response = post(client, route + "/amendment-preview", {
        "candidate_sha256": current(batch, "candidate")["sha256"],
        "mode": "rewrite", "instruction": "保留事件与结果，展开现场回应", "max_cost_cny": "10",
        "enable_checker": True, "enable_reader": False,
    })
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["request"]["narrative_policy"] == "causal-v1"
    assert len(control["calls"]) == before_calls
    assert preview["prompt_contract_sha256"] != draft["snapshot"]["prompt_contract_sha256"]
    approved = post(client, route + "/amendment-authorize", {
        "preview_sha256": preview["preview_sha256"], "confirmed": True,
    })
    assert approved.status_code == 200, approved.text
    done = settle(client, base, batch["id"])
    assert control["calls"][before_calls:] == [
        "rewrite", "memory_amend", "checker_amend",
    ], done["state"]
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    assert "改写" in control["requests"]["rewrite"]["plot_execution"]["current_task"]
    assert "reader_amend" not in control["requests"]
    for field in ("spec", "snapshot", "preview_sha256"):
        assert done[field] == batch[field]


def test_new_preview_counts_all_selected_card_text_and_blocks_before_dispatch(generation):
    client, control = generation
    base, old = stage_create(client, **options(narrative_policy="legacy-v1"))
    all_narratives = [c["id"] for c in read(client, "/api/genre-quality-cards")
                      if c["layer"] == "narrative"]
    request = {**old["spec"], "narrative_card_ids": all_narratives}
    request.pop("narrative_policy")
    response = post(client, base, request)
    assert response.status_code == 200, response.text
    batch = response.json()
    assert batch["spec"]["narrative_policy"] == "causal-v1"
    assert batch["snapshot"]["blockers"]
    value = json.loads(render_for(
        FrozenGenerationSpec.model_validate(batch["spec"]), batch["snapshot"], "plan"
    )[1])
    assert len(value["narrative_design"]["selected_cards"]) == len(all_narratives)
    assert not control["calls"]
