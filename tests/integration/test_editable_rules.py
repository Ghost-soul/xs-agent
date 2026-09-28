# ruff: noqa: F401 F811
"""Editable instructions are frozen, rendered and recovered against a test database."""

import json

import pytest

from novel_writer.generation import editable_contract, editable_rules
from novel_writer.generation.budget import input_tokens, request_preview
from novel_writer.generation.content import json_text
from novel_writer.generation.runtime import GenerationRuntime
from novel_writer.providers.base import ModelRequest
from tests.integration.support import headers
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_creative_cast import expanded
from tests.integration.test_generation_automation import automated, settle
from tests.integration.test_generation_longform import longform, stage_create
from tests.integration.test_genre_generation import generation, post, read, start
from tests.integration.test_novel_run_rebuild import current
from tests.integration.test_prompt_templates import OPTIONS, adapt_fixture_provider
from tests.integration.test_stage_craft import craft, create_craft
from tests.integration.test_step_recovery import authorize, recovery


def save_rules(client, variant, texts, maximum=None):
    before = read(client, "/api/prompt-templates")
    entry = next(e for e in before["entries"] if e["variant"] == variant)
    settings = {"texts": texts}
    if maximum is not None:
        settings["maximum_new_characters"] = maximum
    response = client.put(
        "/api/prompt-templates/" + variant,
        headers=headers(),
        json={
            "expected_revision": before["revision"],
            "template": entry["text"] if entry["customized"] else None,
            "program_settings": settings,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_rules_only_defaults_freeze_new_cap_and_final_inputs(expanded):
    client, control = expanded
    save_rules(client, "chief", {"creative_guidance": "CHIEF_AUTHOR_RULE", "chief_language": ""}, 5)
    saved = save_rules(
        client, "writer", {"creative_guidance": "WRITER_AUTHOR_RULE", "writer_scope": ""}
    )
    base, draft = create_craft(client, unit_limit=3)
    assert len(draft["snapshot"]["new_character_slots"]) == 5
    assert draft["snapshot"]["new_character_limit"] == 5
    assert draft["snapshot"]["prompt_templates"]["templates"] == {}
    assert draft["snapshot"]["prompt_templates"]["revision"] == saved["revision"]
    save_rules(client, "chief", {"creative_guidance": "LATER_CHIEF"}, 0)
    save_rules(client, "writer", {"creative_guidance": "LATER_WRITER"})
    done = start(client, base, draft)
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    for call in done["calls"]:
        receipt = read(client, f"{base}/{done['id']}/calls/{call['id']}")["request"]
        wire = ModelRequest.model_validate(receipt["model_request"])
        assert receipt[editable_contract.KEY] == draft["snapshot"][editable_contract.KEY]
        assert receipt["input_tokens"] == input_tokens(request_preview(wire), receipt["counting"])
        assert "LATER_" not in wire.system_prompt
        if call["action"] == "plan":
            assert "CHIEF_AUTHOR_RULE" in wire.system_prompt
            source = receipt["prompt_template_source"]
            assert source["output_schema"]["properties"]["new_characters"]["maxItems"] == 5
            assert source["creative_autonomy"]["maximum_new_characters"] == 5
        elif call["action"].startswith("write:"):
            assert "WRITER_AUTHOR_RULE" in wire.system_prompt
            assert "【完整展开当前事件】" not in wire.system_prompt
    version = read(client, f"/api/prompt-templates/versions/{saved['revision']}/chief")
    assert version["program_settings"]["maximum_new_characters"] == 5
    assert version["program_settings"]["texts"]["creative_guidance"] == "CHIEF_AUTHOR_RULE"
    assert done["snapshot"] == draft["snapshot"]


def test_unsaved_rules_preview_changes_schema_without_mutating_batch(craft):
    client, control = craft
    base, draft = create_craft(client)
    before = read(client, f"{base}/{draft['id']}")
    payload = {
        "project_id": base.split("/")[3],
        "batch_id": draft["id"],
        "variant": "chief",
        "program_settings": {
            "texts": {"creative_guidance": "UNSAVED_RULE"},
            "maximum_new_characters": 5,
        },
    }
    result = post(client, "/api/prompt-templates/preview", payload)
    assert result.status_code == 200, result.text
    preview = result.json()
    assert "UNSAVED_RULE" in preview["system_prompt"]
    assert (
        preview["engine_contract"]["output_schema"]["properties"]["new_characters"]["maxItems"] == 5
    )
    assert "按编辑草稿模拟" in preview["source_description"]
    again = post(client, "/api/prompt-templates/preview", payload)
    assert again.json() == preview
    assert read(client, f"{base}/{draft['id']}") == before
    assert control["calls"] == []
    payload["program_settings"]["maximum_new_characters"] = 13
    assert post(client, "/api/prompt-templates/preview", payload).status_code == 422


def test_recovery_replays_original_guidance_after_default_changes(recovery):
    client, control = recovery
    save_rules(client, "writer", {"writer_scope": "WRITER_BEFORE_FAILURE"})
    control["retry_failure_action"] = "write:1"
    base, draft = stage_create(client, unit_limit=1, **OPTIONS)
    failed = start(client, base, draft)
    route = f"{base}/{failed['id']}"
    before = read(client, route + f"/calls/{failed['calls'][-1]['id']}")["request"]
    save_rules(client, "writer", {"writer_scope": "NEW_DEFAULT_AFTER_FAILURE"})
    preview = read(client, route + "/step-recovery-preview")
    del control["retry_failure_action"]
    control["pause_action"] = "write:1"
    authorized = authorize(client, route, preview)
    assert authorized.status_code == 200, authorized.text
    done = settle(client, base, failed["id"])
    after = read(client, route + f"/calls/{done['calls'][-1]['id']}")["request"]
    assert after["model_request"] == before["model_request"]
    assert after[editable_rules.RECEIPT] == before[editable_rules.RECEIPT]
    assert "WRITER_BEFORE_FAILURE" in after["model_request"]["system_prompt"]
    assert control["calls"].count("plan") == 1


def test_independent_revision_uses_own_frozen_guidance(expanded):
    client, control = expanded
    base, draft = create_craft(client, unit_limit=3)
    before = start(client, base, draft)
    save_rules(client, "memory", {"creative_guidance": "MEMORY_AMEND_RULE"})
    route = f"{base}/{before['id']}"
    preview = post(
        client,
        route + "/amendment-preview",
        {
            "candidate_sha256": current(before, "candidate")["sha256"],
            "mode": "verify",
            "instruction": "核对正文事实",
            "output_limit": 12000,
            "max_cost_cny": "1",
            "enable_checker": False,
        },
    )
    assert preview.status_code == 200, preview.text
    save_rules(client, "memory", {"creative_guidance": "LATER_MEMORY_RULE"})
    authorized = post(
        client,
        route + "/amendment-authorize",
        {
            "confirmed": True,
            "preview_sha256": preview.json()["preview_sha256"],
        },
    )
    assert authorized.status_code == 200, authorized.text
    done = settle(client, base, before["id"])
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    call = next(c for c in done["calls"] if c["action"] == "memory_amend")
    saved = read(client, route + f"/calls/{call['id']}")["request"]
    assert "MEMORY_AMEND_RULE" in saved["model_request"]["system_prompt"]
    assert "LATER_MEMORY_RULE" not in saved["model_request"]["system_prompt"]
    assert saved[editable_contract.KEY] == preview.json()[editable_contract.KEY]
    assert done["snapshot"] == before["snapshot"]


def test_five_proposals_survive_plan_output_and_author_edit_api(expanded, monkeypatch):
    client, control = expanded
    save_rules(client, "chief", {}, 5)
    previous = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        response = await previous(self, call_id, request, profile, spec, counting, api_key)
        if control["calls"][-1] == "plan":
            value = json.loads(response.text)
            person = value["new_characters"][0]
            slots = control["requests"]["plan"]["creative_autonomy"]["new_character_slots"]
            value["new_characters"] = [
                {**person, "id": i, "name": f"向导{n}"} for n, i in enumerate(slots)
            ]
            for scene in value["scenes"]:
                scene["character_ids"] += slots[1:]
            return response.model_copy(update={"text": json_text(value)})
        return response

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    base, draft = create_craft(client)  # Two proposed units pause for the five-unit scale.
    paused = start(client, base, draft)
    assert paused["status"] == "awaiting_plan", paused["state"]
    plan = current(paused, "plan")
    assert len(plan["payload"]["new_characters"]) == 5
    edited = client.put(
        f"{base}/{paused['id']}/plan",
        headers=headers("five-people-plan-edit"),
        json={
            "plan": plan["payload"],
            "expected_plan_sha256": plan["sha256"],
            "author_note": "沿用五位新人物，接受当前单元安排。",
        },
    )
    assert edited.status_code == 200, edited.text
    assert len(current(edited.json(), "plan")["payload"]["new_characters"]) == 5
    assert control["calls"] == ["plan"]
