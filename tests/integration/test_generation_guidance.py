# ruff: noqa: F811
import json

from novel_writer.generation import guidance
from novel_writer.generation.runtime import GenerationRuntime
from novel_writer.generation.schemas import FrozenGenerationSpec
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated  # noqa: F401
from tests.integration.test_generation_feedback import feedback_run  # noqa: F401
from tests.integration.test_generation_longform import longform, stage_create  # noqa: F401
from tests.integration.test_genre_generation import generation, read, start  # noqa: F401
from tests.integration.test_novel_run_rebuild import current


def test_guided_relationship_handoff_reaches_next_writer_without_becoming_formal(
    feedback_run, monkeypatch
):
    client, control = feedback_run
    original = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        response = await original(self, call_id, request, profile, spec, counting, api_key)
        if control["calls"][-1] == "memory:1":
            payload = control["requests"]["memory:1"]
            data = json.loads(response.text)
            data["changes"].append(
                {
                    "collection": "relationships",
                    "values": {
                        "source_character_id": spec.character_ids[0],
                        "target_character_id": spec.character_ids[1],
                        "relation_type": "同意邀请",
                        "description": "她同意了邀请，尚未确认爱情",
                    },
                    "observation": "她同意了邀请",
                    "paragraph_ids": [payload["candidate"][0]["id"]],
                }
            )
            response = response.model_copy(update={"text": json.dumps(data)})
        return response

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    base, draft = stage_create(
        client,
        writing_policy="guided-v1",
        feedback_policy="logic-v1",
        automation_policy="stage-auto-v1",
        unit_limit=3,
    )
    assert draft["snapshot"]["prompt_contract_sha256"] == guidance.contract_for(
        FrozenGenerationSpec.model_validate(draft["spec"])
    )
    batch = start(client, base, draft)
    assert batch["state"]["units_finished"], batch["state"]
    assert all(c["status"] == "completed" for c in batch["calls"]), batch["state"]
    assert control["calls"] == [
        "plan",
        "write:1",
        "memory:1",
        "write:2",
        "memory:2",
        "write:3",
        "memory:3",
        "checker",
    ]
    assert control["requests"]["plan"]["genre_direction"]["focus"]["id"] == "girls_love_gl"
    for n in (2, 3):
        writer = control["requests"][f"write:{n}"]
        assert "cards" not in writer and "background_cards" not in writer
        relations = writer["formal_reference"]["relationships"]
        assert any(r["description"] == "她同意了邀请，尚未确认爱情" for r in relations)
        assert writer["formal_reference"]["candidate_state_is_formal"] is False
        assert writer["effective_plan"] == current(batch, "plan")["payload"]
    assert (
        batch["snapshot"]["context"]["relationships"]
        == draft["snapshot"]["context"]["relationships"]
    )
    for call in batch["calls"]:
        saved = read(client, f"{base}/{batch['id']}/calls/{call['id']}")["request"]
        assert saved["feedback_options"]["writing_policy"] == "guided-v1"
        if call["action"].startswith("memory:"):
            assert "不从题材或计划推断爱情" in saved["model_request"]["system_prompt"]
