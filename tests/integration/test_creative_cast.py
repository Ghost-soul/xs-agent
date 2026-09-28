# ruff: noqa: F401 F811
import json

import pytest

from novel_writer.generation import creative_contract
from novel_writer.generation.budget import input_tokens, request_preview
from novel_writer.generation.content import json_text
from novel_writer.generation.runtime import GenerationRuntime
from novel_writer.providers.base import ModelRequest
from tests.integration.support import headers
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated, continue_payload, settle
from tests.integration.test_generation_longform import approval_data, longform
from tests.integration.test_genre_generation import generation, post, read, start
from tests.integration.test_novel_run_rebuild import current
from tests.integration.test_prompt_templates import adapt_fixture_provider, save
from tests.integration.test_stage_craft import craft, create_craft


@pytest.fixture
def expanded(craft, monkeypatch):
    client, control = craft
    control.update(plan_count=2)
    original = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        response = await original(self, call_id, request, profile, spec, counting, api_key)
        action = control["calls"][-1]
        prompt = control["requests"][action]
        if action == "plan":
            plan = json.loads(response.text)
            identifier = prompt["creative_autonomy"]["new_character_slots"][0]
            control["new_id"] = identifier
            plan["new_characters"] = [
                {
                    "id": identifier,
                    "name": "林岑",
                    "description": "渡口向导",
                    "independent_goal": "找回灯笼",
                    "voice": "言简意赅",
                    "entry_reason": "闻声带绳赶到",
                }
            ]
            for scene in plan["scenes"]:
                scene["character_ids"].append(identifier)
            if control.get("gap"):
                q = "尚未设计向导的来意，如何安排？"
                plan.update(
                    questions=[q],
                    question_scopes={q: "current_unit"},
                    author_question_reasons={
                        q: {
                            "kind": "missing_canonical_fact",
                            "source": "未设定内容",
                            "why_blocked": "原报告提出创作疑问",
                        }
                    },
                )
            return response.model_copy(update={"text": json_text(plan)})
        if action == "write" or action.startswith("write:"):
            return response.model_copy(update={"text": "林岑提灯走到渡口。\n\n林岑递出了绳索。"})
        if action in {"memory", "memory_amend"} or action.startswith("memory:"):
            value = json.loads(response.text)
            value["changes"].insert(
                0,
                {
                    "collection": "characters",
                    "object_id": control["new_id"],
                    "values": {"name": "林岑", "current_state": action},
                    "paragraph_ids": [prompt["candidate"][0]["id"]],
                    "observation": "提灯来到渡口",
                },
            )
            return response.model_copy(update={"text": json_text(value)})
        return response

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    return client, control


def test_new_character_and_gap_run_without_approval_then_handoff_uses_same_identity(expanded):
    client, control = expanded
    control["gap"] = True
    base, draft = create_craft(client, unit_limit=3)
    done = start(client, base, draft)
    assert control["calls"] == ["plan", "write:1", "memory:1", "write:2", "memory:2"], done["state"]
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    assert done["state"]["units_finished"] and not current(done, "questions")["payload"]["items"]
    assert current(done, "plan")["payload"]["creative_notes"]
    identifier = control["new_id"]
    first = control["requests"]["write:1"]
    assert first["character_proposals"][0]["id"] == identifier
    assert identifier not in json_text(first["formal_reference"])
    second = control["requests"]["write:2"]
    assert identifier in json_text(second["formal_reference"])
    handoffs = [a for a in done["artifacts"] if a["kind"] == "handoff"]
    assert len(handoffs) == 2
    for handoff in handoffs:
        people = handoff["payload"]["working_state"]["characters"]
        assert len([p for p in people if p["id"] == identifier]) == 1
    assert identifier not in json_text(draft["snapshot"]["context"])
    assert done["snapshot"] == draft["snapshot"]  # No new formal or frozen records.
    for call in done["calls"]:
        receipt = read(client, f"{base}/{done['id']}/calls/{call['id']}")
        req = receipt["request"]
        model = ModelRequest.model_validate(req["model_request"])
        assert req[creative_contract.KEY] == creative_contract.binding()
        assert req["input_tokens"] == input_tokens(request_preview(model), req["counting"])
    assert current(done, "memory")["payload"]["status"] == "complete"


def test_new_proposals_roundtrip_through_author_edit_and_remain_visible(expanded):
    client, control = expanded
    base, draft = create_craft(client, unit_limit=3, pause_after_plan=True)
    paused = start(client, base, draft)
    plan = current(paused, "plan")
    result = client.put(
        f"{base}/{paused['id']}/plan",
        json={
            "plan": plan["payload"],
            "expected_plan_sha256": plan["sha256"],
            "author_note": "展开渡口事件",
        },
        headers=headers("save-expanded"),
    )
    assert result.status_code == 200, result.text
    edited = result.json()
    assert current(edited, "plan")["payload"]["new_characters"] == plan["payload"]["new_characters"]
    assert current(edited, "plan_adjustment")["payload"]["blockers"] == []
    assert control["calls"] == ["plan"]


def test_custom_templates_preview_and_actual_requests_keep_creative_fields(expanded, monkeypatch):
    client, control = expanded
    adapt_fixture_provider(client, monkeypatch)
    chief_template = save(client, "chief", "CUSTOM_CREATIVE_CHIEF")
    save(client, "writer", "CUSTOM_CREATIVE_WRITER")
    base, draft = create_craft(client, unit_limit=3)
    version = read(client, f"/api/prompt-templates/versions/{chief_template['revision']}/chief")
    preview = post(client, "/api/prompt-templates/preview", {
        "project_id": base.split("/")[3], "batch_id": draft["id"],
        "variant": "chief", "template": version["text"],
    })
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert data["system_prompt"].startswith("CUSTOM_CREATIVE_CHIEF")
    from novel_writer.generation.editable_rules import effective

    assert effective("chief").texts["creative_guidance"] in data["system_prompt"]
    assert creative_contract.QUESTION_GUIDANCE in data["system_prompt"]
    assert "真实缺口或边界冲突须保留" not in data["system_prompt"]
    assert "new_characters" in data["engine_contract"]["output_schema"]["properties"]
    assert not data["blockers"] and control["calls"] == []
    done = start(client, base, draft)
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    call = next(c for c in done["calls"] if c["action"] == "write:1")
    saved = read(client, f"{base}/{done['id']}/calls/{call['id']}")["request"]
    model = ModelRequest.model_validate(saved["model_request"])
    assert model.system_prompt.startswith("CUSTOM_CREATIVE_WRITER")
    assert creative_contract.WRITER in model.system_prompt
    assert control["new_id"] in model.user_prompt and "林岑" in model.user_prompt
    assert saved["input_tokens"] == input_tokens(request_preview(model), saved["counting"])


def test_saved_craft_gap_continues_without_answer_and_keeps_original_call(craft, monkeypatch):
    client, control = craft
    # Freeze a pre-autonomy craft batch to exercise the existing paused-stage UI route.
    monkeypatch.setattr(creative_contract, "bind_snapshot", lambda *args: None)
    control.update(plan_count=2, author_question=True)
    base, draft = create_craft(client, unit_limit=3)
    paused = start(client, base, draft)
    assert paused["status"] == "awaiting_plan" and control["calls"] == ["plan"]
    call = paused["calls"][0]
    receipt_path = f"{base}/{paused['id']}/calls/{call['id']}"
    receipt = read(client, receipt_path)
    result = post(client, f"{base}/{paused['id']}/continue-stage", continue_payload(paused))
    assert result.status_code == 200, result.text
    done = settle(client, base, paused["id"])
    assert control["calls"] == ["plan", "write:1", "memory:1", "write:2", "memory:2"]
    assert read(client, receipt_path) == receipt
    assert done["snapshot"] == paused["snapshot"] and done["spec"] == paused["spec"]
    question = current(done, "questions")["payload"]["items"][0]
    assert question["status"] == "answered" and "无需再次" in question["author_answer"]
    assert current(done, "author_continuation")["payload"]["automatically_delegated_gaps"]


def test_single_unit_creates_evidenced_candidate_person(expanded):
    client, control = expanded
    base, draft = create_craft(client, stage_mode="single-unit-v1", unit_limit=1)
    done = start(client, base, draft)
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    assert control["calls"] == ["plan", "write", "memory"]
    people = current(done, "memory")["payload"]["factual_changes"]["add_characters"]
    assert people[0]["id"] == control["new_id"]


def test_test_database_adoption_preserves_new_identity_for_next_stage(expanded):
    client, control = expanded
    base, draft = create_craft(client, unit_limit=3)
    done = start(client, base, draft)
    route = f"{base}/{done['id']}"
    before = read(client, base.replace("/generation-batches", "/backup"))
    assert control["new_id"] not in json_text(before["formal_version"]["state"])
    suggestions = read(client, route + "/stage-chapters")
    data = approval_data(suggestions)
    preview = post(client, route + "/stage-adoption-preview", data)
    assert preview.status_code == 200, preview.text
    adopted = post(
        client,
        route + "/stage-adopt",
        {
            **data,
            "confirmed": True,
            "accept_genre_deviation": True,
            "preview_sha256": preview.json()["preview_sha256"],
        },
    )
    assert adopted.status_code == 200, adopted.text
    after = read(client, base.replace("/generation-batches", "/backup"))
    people = after["formal_version"]["state"]["characters"]
    assert len([p for p in people if p["id"] == control["new_id"]]) == 1
    next_draft = post(
        client, base, {**draft["spec"], "base_version_id": adopted.json()["version_id"]}
    )
    assert next_draft.status_code == 200, next_draft.text
    registry = next_draft.json()["snapshot"]["character_identity_registry"]
    assert any(p["id"] == control["new_id"] for p in registry)


def test_independent_fact_rebuild_retains_proposal_identity_and_its_binding(expanded):
    client, control = expanded
    base, draft = create_craft(client, unit_limit=3)
    before = start(client, base, draft)
    route = f"{base}/{before['id']}"
    preview = post(client, route + "/amendment-preview", {
        "candidate_sha256": current(before, "candidate")["sha256"], "mode": "verify",
        "instruction": "重新核对正文事实", "output_limit": 12000,
        "max_cost_cny": "1", "enable_checker": False,
    })
    assert preview.status_code == 200, preview.text
    assert preview.json()[creative_contract.KEY] == draft["snapshot"][creative_contract.KEY]
    authorized = post(client, route + "/amendment-authorize", {
        "confirmed": True, "preview_sha256": preview.json()["preview_sha256"],
    })
    assert authorized.status_code == 200, authorized.text
    done = settle(client, base, before["id"])
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    people = current(done, "memory")["payload"]["factual_changes"]["add_characters"]
    assert len([p for p in people if p["id"] == control["new_id"]]) == 1
    assert control["calls"].count("plan") == 1


def test_explicit_boundary_does_not_auto_continue_but_author_can_delegate_a_misclassification(
    craft,
    monkeypatch,
):
    client, control = craft
    control.update(plan_count=2, author_question=True)
    original = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        response = await original(self, call_id, request, profile, spec, counting, api_key)
        if control["calls"][-1] == "plan":
            data = json.loads(response.text)
            for reason in data["author_question_reasons"].values():
                reason.update(kind="author_boundary_conflict", source="作者指定的相互冲突方向")
            return response.model_copy(update={"text": json_text(data)})
        return response

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    base, draft = create_craft(client, unit_limit=3)
    paused = start(client, base, draft)
    assert paused["status"] == "awaiting_plan"
    q = current(paused, "questions")["payload"]["items"][0]["question"]
    route = f"{base}/{paused['id']}/continue-stage"
    assert post(client, route, continue_payload(paused)).status_code == 409
    assert control["calls"] == ["plan"]
    response = post(client, route, continue_payload(paused, delegated_questions=[q]))
    assert response.status_code == 200, response.text
    done = settle(client, base, paused["id"])
    answer = current(done, "questions")["payload"]["items"][0]["author_answer"]
    assert "明确作者边界" in answer and "不是新增正式事实" in answer
    assert control["calls"].count("plan") == 1
