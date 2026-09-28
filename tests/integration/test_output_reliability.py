# ruff: noqa: F401 F811
"""PostgreSQL recovery and immutable-request checks using only a stub provider."""

import json

import pytest

from novel_writer.generation import craft_plans, creative_contract, runtime
from novel_writer.generation.budget import input_tokens, request_preview
from novel_writer.generation.output_contract import KEY, QUESTION_GUIDANCE
from novel_writer.generation.output_contract_v2 import CHIEF_LANGUAGE
from novel_writer.generation.output_contract_v3 import REVISION
from novel_writer.generation.runtime import GenerationRuntime
from novel_writer.providers.base import ModelRequest
from tests.integration.support import headers
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated, settle
from tests.integration.test_generation_longform import longform, stage_create
from tests.integration.test_genre_generation import generation, post, read, start
from tests.integration.test_novel_run_rebuild import current
from tests.integration.test_stage_craft import craft, create_craft


@pytest.fixture
def outputs(craft, monkeypatch):
    client, control = craft
    original = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        response = await original(self, call_id, request, profile, spec, counting, api_key)
        if control["calls"][-1] == "plan":
            if control.get("refuse"):
                return response.model_copy(update={"text": "我不能按这个要求规划。请调整请求。"})
            if control.get("flat"):
                plan = json.loads(response.text)
                q = plan["questions"][0]
                plan["author_question_reasons"] = plan["author_question_reasons"][q]
                plan["question_scopes"] = "current_unit"
                if control.get("ambiguous"):
                    plan["questions"].append("另一个身份也需要确认？")
                response = response.model_copy(
                    update={"text": json.dumps(plan, ensure_ascii=False)}
                )
        return response

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    return client, control


def test_refusal_does_not_dispatch_retries_or_offer_local_reparse(outputs):
    client, control = outputs
    control["refuse"] = True
    base, draft = create_craft(client)
    done = start(client, base, draft)
    assert control["calls"] == ["plan"]
    assert done["calls"][0]["error_code"] == "provider_refusal"
    assert done["calls"][0]["diagnostic"]["code"] == "provider_refusal"
    assert not done["calls"][0]["can_revalidate"]
    preview = read(client, f"{base}/{done['id']}/step-recovery-preview")
    assert preview["blockers"] and "拒绝" in preview["blockers"][0]
    response = post(client, f"{base}/{done['id']}/step-recovery-authorize", {
        "confirmed": True, "uncertain_confirmed": True,
        "preview_sha256": preview["preview_sha256"], "max_cost_cny": preview["max_cost_cny"],
    })
    assert response.status_code == 409
    assert control["calls"] == ["plan"]


def test_ambiguous_question_report_is_saved_and_explained_without_guessing(outputs):
    client, control = outputs
    control.update(flat=True, ambiguous=True, author_question=True)
    base, draft = create_craft(client)
    done = start(client, base, draft)
    failed = done["calls"][0]
    assert failed["error_code"] == "plan_question_format_invalid"
    assert "一份总原因" in done["state"]["message"]
    assert not failed["can_revalidate"]
    receipt = read(client, f"{base}/{done['id']}/calls/{failed['id']}")
    assert len(json.loads(receipt["response"]["text"])["questions"]) == 2
    assert not any(a["kind"] == "plan" for a in done["artifacts"])
    assert control["calls"] == ["plan"]


def test_old_saved_response_can_be_revalidated_once_without_rewriting_request(outputs, monkeypatch):
    client, control = outputs
    # This response predates creative autonomy and must retain its original question contract.
    monkeypatch.setattr(creative_contract, "bind_snapshot", lambda *args: None)
    control.update(flat=True, author_question=True)
    normalizer = craft_plans.normalize_questions
    parser = runtime.response_parser
    monkeypatch.setattr(craft_plans, "normalize_questions", lambda value: value)
    monkeypatch.setattr(runtime, "response_parser", lambda batch, call: "old-fixture-parser")
    base, draft = create_craft(client)
    done = start(client, base, draft)
    failed = done["calls"][0]
    route = f"{base}/{done['id']}/calls/{failed['id']}"
    before = read(client, route)
    monkeypatch.setattr(craft_plans, "normalize_questions", normalizer)
    monkeypatch.setattr(runtime, "response_parser", parser)
    assert read(client, f"{base}/{done['id']}")["calls"][0]["can_revalidate"]
    response = post(client, route + "/revalidate", {"confirmed": True})
    assert response.status_code == 200, response.text
    assert response.json()["provider_requests"] == "0"
    after = read(client, route)
    assert before["request"] == after["request"]
    assert before["response"] == after["response"]
    result = read(client, f"{base}/{done['id']}")
    assert result["status"] == "awaiting_plan"
    assert result["next_action"] is None
    assert result["calls"][0]["status"] == "completed"
    assert result["calls"][0]["actual_cost_cny"] == failed["actual_cost_cny"]
    assert control["calls"] == ["plan"]
    assert current(result, "questions")["payload"]["items"][0]["status"] == "pending"
    again = post(client, route + "/revalidate", {"confirmed": True})
    assert again.status_code == 409


def test_full_workflow_preserves_report_modes_and_counts_final_requests(craft):
    client, control = craft
    base, draft = create_craft(client, enable_checker=True, generate_title=True)
    assert KEY in draft["snapshot"]
    done = start(client, base, draft)
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    assert done["next_action"] is None and done["state"]["units_finished"]
    assert control["calls"][-2:] == ["checker", "title"]
    for call in done["calls"]:
        saved = read(client, f"{base}/{done['id']}/calls/{call['id']}")["request"]
        request = ModelRequest.model_validate(saved["model_request"])
        assert saved["input_tokens"] == input_tokens(request_preview(request), saved["counting"])
        if call["action"] == "plan":
            assert saved["input_tokens"] == draft["snapshot"]["plan_input_tokens"]
            assert request.system_prompt.count(creative_contract.QUESTION_GUIDANCE) == 1
            assert request.system_prompt.count(CHIEF_LANGUAGE) == 1
            assert saved[KEY]["revision"] == REVISION
        if call["action"] == "checker":
            assert saved["output_format"]["mode"] == "prompt_only"
        if call["action"].startswith("write:"):
            assert request.skip_structured_output
            assert QUESTION_GUIDANCE not in request.system_prompt
    assert len(control["calls"]) == 13
