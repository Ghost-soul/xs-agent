# ruff: noqa: F811
import json

import pytest

from novel_writer.generation import request_preparation
from novel_writer.generation.content import json_text, paragraphs
from novel_writer.generation.runtime import GenerationRuntime
from novel_writer.knowledge.service import KnowledgeService
from novel_writer.services.errors import WorkflowError
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated, settle  # noqa: F401
from tests.integration.test_generation_longform import longform, stage_create  # noqa: F401
from tests.integration.test_generation_memory_recovery import authorize as authorize_memory
from tests.integration.test_genre_generation import (  # noqa: F401
    create,
    generation,
    post,
    read,
    start,
)
from tests.integration.test_novel_run_rebuild import current, novel_generation  # noqa: F401
from tests.integration.test_step_recovery import (
    authorize as authorize_step,
)
from tests.integration.test_step_recovery import (
    fail,
    recovery,  # noqa: F401
)

OPTIONS = {
    "context_policy": "role-rag-v2",
    "input_limit": 200000,
    "writing_policy": "guided-v1",
    "feedback_policy": "logic-v1",
    "automation_policy": "stage-auto-v1",
    "length_policy": "unit-v1",
    "plan_policy": "bounded-v1",
    "card_selection_policy": "separate-v1",
    "focus_card_id": "western_fantasy_dnd",
    "narrative_card_ids": ["girls_love_gl"],
    "narrative_policy": "causal-v1",
    "relationship_scope": "genre-led",
    "relationship_character_ids": [],
    "chapter_count": None,
}


def test_five_units_actual_requests_opening_world_queries_and_handoffs(automated, monkeypatch):
    client, control = automated
    previous = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        response = await previous(self, call_id, request, profile, spec, counting, api_key)
        if control["calls"][-1] == "memory:1":
            value = json.loads(response.text)
            value["changes"].append(
                {
                    "collection": "world_lore",
                    "values": {
                        "name": "邀请",
                        "category": "society",
                        "summary": "邀请后的新条件",
                        "details": ["CANDIDATE_WORLD_AFTER_UNIT_ONE"],
                    },
                    "observation": "正文中建立了邀请的条件",
                    "paragraph_ids": [control["requests"]["memory:1"]["candidate"][0]["id"]],
                }
            )
            response = response.model_copy(update={"text": json_text(value)})
        return response

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    base, draft = stage_create(client, unit_limit=5, **OPTIONS)
    done = start(client, base, draft)
    assert done["status"] == "needs_attention" and done["next_action"] is None, done["state"]
    assert control["calls"] == [
        "plan",
        *[a for i in range(1, 6) for a in (f"write:{i}", f"memory:{i}")],
        "checker",
    ]
    second = control["requests"]["write:2"]
    assert "CANDIDATE_WORLD_AFTER_UNIT_ONE" in json_text(second["formal_reference"])
    assert "CANDIDATE_WORLD_AFTER_UNIT_ONE" not in json_text(
        control["requests"]["checker"]["formal_start"]
    )
    fifth = control["requests"]["write:5"]
    assert "already_written" not in fifth
    assert len(fifth["continuity"]["unit_outcomes"]) == 4
    assert "第4次选择" in fifth["continuity"]["recent_prose"]["text"]
    assert "第1次选择" not in fifth["continuity"]["recent_prose"]["text"]
    memory_call = next(c for c in done["calls"] if c["action"] == "memory:5")
    saved = read(client, f"{base}/{done['id']}/calls/{memory_call['id']}")["request"]
    assert "第5次选择" in str(saved["knowledge_retrieval"]["queries"])
    assert saved["knowledge_retrieval"]["project_id"] == draft["snapshot"]["knowledge_project_id"]
    assert "第5次选择" in json_text(control["requests"]["memory:5"]["candidate"])
    assert done["snapshot"] == draft["snapshot"]


def test_failed_writer_replays_exact_new_request_without_search(recovery, monkeypatch):
    client, control, _, target, before = fail(recovery, **OPTIONS)
    failed = next(c for c in before["calls"] if c["action"] == "write:1")
    saved = read(client, target + f"/calls/{failed['id']}")["request"]
    preview = read(client, target + "/step-recovery-preview")
    del control["retry_failure_action"]
    control["pause_action"] = "write:1"

    async def unexpected(*args, **kwargs):
        raise AssertionError("Saved request recovery must not search again")

    monkeypatch.setattr(KnowledgeService, "retrieve", unexpected)
    assert authorize_step(client, target, preview).status_code == 200
    done = settle(client, target.rsplit("/", 1)[0], before["id"])
    replacement = read(client, target + f"/calls/{done['calls'][-1]['id']}")["request"]
    assert replacement["model_request"] == saved["model_request"]
    assert replacement["knowledge_retrieval"] == saved["knowledge_retrieval"]


def test_memory_capacity_recovery_preserves_new_opening_and_receipt(automated, monkeypatch):
    client, control = automated
    control["incomplete_action"] = "memory:2"
    base, draft = stage_create(client, auxiliary_output_limit=6000, **OPTIONS)
    before = start(client, base, draft)
    target = f"{base}/{before['id']}"
    failed = next(c for c in before["calls"] if c["action"] == "memory:2")
    saved = read(client, target + f"/calls/{failed['id']}")["request"]
    preview = read(client, target + "/memory-recovery-preview?output_limit=24000&all_roles=false")
    assert not preview["blockers"], preview
    del control["incomplete_action"]
    control["pause_action"] = "memory:2"

    async def unexpected(*args, **kwargs):
        raise AssertionError("Memory capacity recovery must reuse frozen retrieval")

    monkeypatch.setattr(KnowledgeService, "retrieve", unexpected)
    response = authorize_memory(client, target, preview)
    assert response.status_code == 200, response.text
    done = settle(client, base, before["id"])
    replacement = read(client, target + f"/calls/{done['calls'][-1]['id']}")["request"]
    assert replacement["knowledge_retrieval"] == saved["knowledge_retrieval"]
    assert replacement["model_request"]["user_prompt"] == saved["model_request"]["user_prompt"]
    assert replacement["model_request"]["system_prompt"] == saved["model_request"]["system_prompt"]


def test_single_unit_actual_requests_complete_all_planned_scenes(novel_generation):
    client, control = novel_generation
    base, draft = create(client, workflow="novel-run-v1", **OPTIONS)
    done = start(client, base, draft)
    assert control["calls"] == ["plan", "write", "memory", "checker"], done["state"]
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    chief = json.loads(control["requests"]["plan"])
    assert chief["output_schema"]["properties"]["scenes"]["minItems"] == 2
    assert chief["output_schema"]["properties"]["scenes"]["maxItems"] == 4
    writer = json.loads(control["requests"]["write"])
    assert writer["plot_execution"]["current_task"] == current(done, "plan")["payload"]["scenes"]
    assert writer["stage_context"]["unit_position"] == {"current": 1, "total": 1}
    assert current(done, "memory")["payload"]["status"] == "complete"


def test_input_capacity_preview_and_resume_keep_validated_handoff(automated, monkeypatch):
    client, control = automated
    control["pause_action"] = "memory:1"
    validate = request_preparation.validate_capacity
    observed = []

    def capacity(text, request, spec, profile, counting):
        payload = json.loads(request.user_prompt)
        if payload.get("stage_context", {}).get("unit_position", {}).get("current") == 2:
            observed.append(payload)
            if spec.input_limit < 68000:
                raise WorkflowError("测试本地输入上限，尚未发送请求")
        return validate(text, request, spec, profile, counting)

    monkeypatch.setattr(request_preparation, "validate_capacity", capacity)
    base, draft = stage_create(client, unit_limit=2, **{**OPTIONS, "input_limit": 58000})
    before = start(client, base, draft)
    assert control["calls"] == ["plan", "write:1", "memory:1"], before["state"]
    assert all(c["status"] == "completed" for c in before["calls"]), before["state"]
    assert before["next_action"] == "write:2", before["state"]
    target = f"{base}/{before['id']}"
    assert read(client, target + "/input-preview?input_limit=58000&all_roles=false")["blockers"]
    preview = read(client, target + "/input-preview?input_limit=68000&all_roles=false")
    assert not preview["blockers"], preview
    assert observed[-1]["continuity"]["unit_outcomes"][0]["unit"] == 1
    assert "第1次选择" in observed[-1]["continuity"]["recent_prose"]["text"]
    assert read(client, target)["calls"] == before["calls"]
    response = post(
        client,
        target + "/input-authorize",
        {
            "input_limit": 68000,
            "all_roles": False,
            "preview_sha256": preview["preview_sha256"],
            "confirmed": True,
        },
    )
    assert response.status_code == 200, response.text
    done = settle(client, base, before["id"])
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    assert control["calls"] == ["plan", "write:1", "memory:1", "write:2", "memory:2", "checker"]
    assert done["spec"] == before["spec"] and done["snapshot"] == before["snapshot"]
    assert observed[-1]["formal_reference"] == observed[-2]["formal_reference"]


@pytest.mark.parametrize("mode", ["local", "rewrite", "verify"])
def test_independent_amendment_uses_new_default_without_changing_old_batch(novel_generation, mode):
    client, control = novel_generation
    old_options = {**OPTIONS, "context_policy": "knowledge-rag-v1"}
    base, draft = create(client, workflow="novel-run-v1", **old_options)
    before = start(client, base, draft)
    target = f"{base}/{before['id']}"
    original = current(before, "candidate")["payload"]["body"]
    entries = paragraphs(original)
    instruction = "保留邀请与回应，在授权范围内补充语气。"
    preview = post(
        client,
        target + "/amendment-preview",
        {
            "candidate_sha256": current(before, "candidate")["sha256"],
            "mode": mode,
            "instruction": instruction,
            "paragraph_ids": [entries[0]["id"]] if mode == "local" else [],
            "protected_paragraph_ids": [entries[-1]["id"]] if mode == "local" else [],
            "output_limit": 12000,
            "max_cost_cny": "1",
            "enable_checker": True,
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["request"]["context_policy"] == "chief-focus-v4"
    result = post(
        client,
        target + "/amendment-authorize",
        {
            "confirmed": True,
            "preview_sha256": preview.json()["preview_sha256"],
        },
    )
    assert result.status_code == 200, result.text
    done = settle(client, base, before["id"])
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    assert done["spec"] == before["spec"] and done["snapshot"] == before["snapshot"]
    action = {"local": "amend", "rewrite": "rewrite", "verify": "memory_amend"}[mode]
    packet = json.loads(control["requests"][action])
    if mode == "local":
        assert packet["scope"]["instruction"] == instruction
        assert [p["id"] for p in packet["authorized_paragraphs"]] == [entries[0]["id"]]
        assert entries[-1]["text"] in current(done, "candidate")["payload"]["body"]
        assert "knowledge_context" not in packet
    elif mode == "rewrite":
        assert packet["original_draft"] == original
        assert packet["story_task"]["revision_instruction"] == instruction
        assert "continuity" not in packet
        call = next(c for c in done["calls"] if c["action"] == action)
        saved = read(client, target + f"/calls/{call['id']}")["request"]
        assert instruction in saved["knowledge_retrieval"]["queries"]
    memory = json.loads(control["requests"]["memory_amend"])
    assert "opening_reference" in memory and "writable_fields" in memory
    checker = json.loads(control["requests"]["checker_amend"])
    assert "formal_start" in checker
