# ruff: noqa: F401 F811
"""Balanced unit targets reach actual requests without upgrading saved calls."""

import json

from novel_writer.generation import progression_contract, progression_rules, unit_delivery
from novel_writer.generation.budget import input_tokens, request_preview
from novel_writer.generation.content import json_text
from novel_writer.generation.runtime import GenerationRuntime
from novel_writer.providers.base import ModelRequest
from tests.integration.support import headers
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated, settle
from tests.integration.test_generation_longform import longform
from tests.integration.test_genre_generation import generation, post, read, start
from tests.integration.test_novel_run_rebuild import current
from tests.integration.test_stage_craft import craft, create_craft
from tests.integration.test_step_recovery import authorize, recovery


def test_uneven_model_weights_produce_equal_actual_targets_and_no_extra_calls(craft, monkeypatch):
    client, control = craft
    original = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        response = await original(self, call_id, request, profile, spec, counting, api_key)
        if control["calls"][-1] == "plan":
            plan = json.loads(response.text)
            for scene, weight in zip(plan["scenes"], (1, 2, 3, 2, 1), strict=True):
                scene["size_weight"] = weight
            response = response.model_copy(update={"text": json_text(plan)})
        return response

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    base, draft = create_craft(client)
    assert draft["snapshot"][unit_delivery.KEY] == unit_delivery.binding()
    chief_preview = post(client, "/api/prompt-templates/preview", {
        "project_id": base.split("/")[3], "batch_id": draft["id"], "variant": "chief",
    })
    assert chief_preview.status_code == 200, chief_preview.text
    assert progression_rules.CHIEF_SCOPE in chief_preview.json()["system_prompt"]
    assert "equal-units-v1" in chief_preview.json()["task_prompt"]
    done = start(client, base, draft)
    assert done["state"]["units_finished"], done["state"]
    assert len(control["calls"]) == 11  # No expansion or additional paid slots.
    plan = current(done, "plan")["payload"]
    assert [s["size_weight"] for s in plan["scenes"]] == [1, 2, 3, 2, 1]
    for call in done["calls"]:
        receipt = read(client, f"{base}/{done['id']}/calls/{call['id']}")
        saved = receipt["request"]
        wire = ModelRequest.model_validate(saved["model_request"])
        assert saved[unit_delivery.KEY] == draft["snapshot"][unit_delivery.KEY]
        assert saved["input_tokens"] == input_tokens(request_preview(wire), saved["counting"])
        if call["action"].startswith("write:"):
            target = {"min_characters": 3000, "max_characters": 4000}
            assert saved["writer_scale"]["current_unit_reference"] == target
            source_scale = saved["prompt_template_source"]["stage_scale"]
            assert source_scale["current_unit_reference"] == target
            assert call["writer_output"]["target"] == target
            assert "当前单元目标 3,000–4,000 字" in wire.system_prompt
            assert wire.system_prompt.count(progression_rules.WRITER_SCOPE) == 1
    templates = read(client, "/api/prompt-templates")
    writer = next(e for e in templates["entries"] if e["variant"] == "writer")
    assert writer["default_program_settings"]["texts"]["writer_scope"] == (
        progression_rules.WRITER_SCOPE
    )
    writer_preview = post(client, "/api/prompt-templates/preview", {
        "project_id": base.split("/")[3], "batch_id": draft["id"], "variant": "writer",
        "template": writer["text"],
    })
    assert writer_preview.status_code == 200, writer_preview.text
    assert "当前单元目标 3,000–4,000 字" in writer_preview.json()["system_prompt"]
    assert writer_preview.json()["source_bindings"]["unit_delivery"] == unit_delivery.binding()
    assert done["snapshot"] == draft["snapshot"]


def test_balanced_failure_recovery_preserves_request_and_template_after_default_change(
    craft, recovery,
):
    client, control = recovery
    control["retry_failure_action"] = "write:1"
    base, draft = create_craft(client)
    failed = start(client, base, draft)
    route = f"{base}/{failed['id']}"
    failed_call = next(c for c in failed["calls"] if c["action"] == "write:1")
    before = read(client, route + f"/calls/{failed_call['id']}")["request"]
    assert before[unit_delivery.KEY] == unit_delivery.binding()
    from tests.integration.test_editable_rules import save_rules

    save_rules(client, "writer", {"writer_scope": "后续新默认，不进入本次恢复"})
    preview = read(client, route + "/step-recovery-preview")
    del control["retry_failure_action"]
    control["pause_action"] = "write:1"
    authorized = authorize(client, route, preview)
    assert authorized.status_code == 200, authorized.text
    done = settle(client, base, failed["id"])
    after = read(client, route + f"/calls/{done['calls'][-1]['id']}")["request"]
    for key in (
        "model_request", "writer_scale", "prompt_program_settings", unit_delivery.KEY,
        progression_contract.KEY,
    ):
        assert after[key] == before[key]
