# ruff: noqa: F401 F811
"""Current prompts and repetition observations; PostgreSQL test DB, fake providers."""

from novel_writer.generation import progression_contract as contract
from novel_writer.generation import progression_rules as rules
from novel_writer.generation import unit_delivery_rules
from novel_writer.generation.content import digest
from novel_writer.generation.runtime import GenerationRuntime
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated, settle
from tests.integration.test_generation_longform import longform
from tests.integration.test_genre_generation import generation, post, read, start
from tests.integration.test_novel_run_rebuild import current
from tests.integration.test_stage_craft import craft, create_craft
from tests.unit.test_repetition import PARAGRAPH


def test_repetition_stays_read_only_and_chain_keeps_actual_previous_prose(craft, monkeypatch):
    client, control = craft
    original = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        response = await original(self, call_id, request, profile, spec, counting, api_key)
        if control["calls"][-1].startswith("write:"):
            return response.model_copy(update={
                "text": response.text + "\n\n" + PARAGRAPH + "\n\n" + PARAGRAPH,
            })
        return response

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    base, draft = create_craft(client)
    done = start(client, base, draft)
    assert done["snapshot"][contract.KEY] == contract.binding()
    assert done["state"]["units_finished"] and len(control["calls"]) == 11
    assert all(c["status"] == "completed" for c in done["calls"])
    body = current(done, "candidate")["payload"]["body"]
    assert body.count(PARAGRAPH) == 10
    stage_report = done["state"]["prose_repetition"]
    assert stage_report["body_sha256"] == digest(body)
    assert stage_report["repeated_characters"] >= len(PARAGRAPH) * 9
    local_total = sum(u["repetition"]["repeated_characters"]
                      for u in done["state"]["writer_unit_status"])
    assert stage_report["repeated_characters"] > local_total  # Includes cross-unit repeats.
    previous_body = None
    for call in done["calls"]:
        receipt = read(client, f"{base}/{done['id']}/calls/{call['id']}")
        saved = receipt["request"]
        assert saved[contract.KEY] == draft["snapshot"][contract.KEY]
        if call["action"].startswith("write:"):
            assert rules.WRITER_SCOPE in saved["model_request"]["system_prompt"]
            assert receipt["response"]["text"] in body
            if previous_body:
                continuity = saved["prompt_template_source"]["continuity"]
                assert continuity["recent_prose"]["text"] == previous_body
            previous_body = receipt["response"]["text"]
    again = read(client, f"{base}/{done['id']}")
    assert again["artifacts"] == done["artifacts"] and len(control["calls"]) == 11


def test_new_chief_preview_uses_same_guidance_as_dispatch_and_catalog(craft):
    client, control = craft
    base, draft = create_craft(client, pause_after_plan=True)
    preview = post(client, "/api/prompt-templates/preview", {
        "project_id": base.split("/")[3], "batch_id": draft["id"], "variant": "chief",
    })
    assert preview.status_code == 200, preview.text
    assert preview.json()["source_bindings"]["event_progression"] == contract.binding()
    assert preview.json()["system_prompt"].count(rules.CHIEF_SCOPE) == 1
    done = start(client, base, draft)
    saved = read(client, f"{base}/{done['id']}/calls/{done['calls'][0]['id']}")["request"]
    assert saved["model_request"]["system_prompt"].count(rules.CHIEF_SCOPE) == 1
    catalog = read(client, "/api/prompt-templates")
    chief = next(e for e in catalog["entries"] if e["variant"] == "chief")
    assert chief["default_program_settings"]["texts"]["chief_scope"] == rules.CHIEF_SCOPE
    assert control["calls"] == ["plan"]


def test_saved_stage_and_its_template_preview_keep_old_guidance(craft, monkeypatch):
    client, control = craft
    with monkeypatch.context() as legacy:
        legacy.setattr(contract, "bind_snapshot", lambda *args: None)
        base, draft = create_craft(client, pause_after_plan=True)
    assert contract.KEY not in draft["snapshot"]
    done = start(client, base, draft)
    receipt = read(client, f"{base}/{done['id']}/calls/{done['calls'][0]['id']}")
    saved = receipt["request"]
    assert contract.KEY not in saved
    assert unit_delivery_rules.CHIEF_SCOPE in saved["model_request"]["system_prompt"]
    assert rules.CHIEF_SCOPE not in saved["model_request"]["system_prompt"]
    preview = post(client, "/api/prompt-templates/preview", {
        "project_id": base.split("/")[3], "batch_id": draft["id"], "variant": "chief",
    })
    assert preview.status_code == 200, preview.text
    assert unit_delivery_rules.CHIEF_SCOPE in preview.json()["system_prompt"]
    assert rules.CHIEF_SCOPE not in preview.json()["system_prompt"]
    assert preview.json()["source_bindings"]["event_progression"] is None
    assert done["snapshot"] == draft["snapshot"] and control["calls"] == ["plan"]
