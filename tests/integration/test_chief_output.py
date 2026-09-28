# ruff: noqa: F401 F811
import json

import pytest

from novel_writer.generation import craft_plans, creative_contract, runtime
from novel_writer.generation.runtime import GenerationRuntime
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated
from tests.integration.test_generation_longform import longform
from tests.integration.test_genre_generation import generation, post, read, start
from tests.integration.test_novel_run_rebuild import current
from tests.integration.test_stage_craft import craft, create_craft


@pytest.fixture
def format_output(craft, monkeypatch):
    client, control = craft
    original = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        response = await original(self, call_id, request, profile, spec, counting, api_key)
        if control["calls"][-1] == "plan":
            value = json.loads(response.text)
            if control.get("short_question"):
                question = value["questions"][0] + "？"
                value["questions"] = [question + "请保留原有事实边界。"]
                value["author_question_reasons"] = {
                    question: next(iter(value["author_question_reasons"].values())),
                }
                value["question_scopes"] = {question: "current_unit"}
            else:
                value["future_proposal"] = None
            body = json.dumps(value, ensure_ascii=False)
            if control.get("truncated_json"):
                body = '{"chapter_goal":"unfinished<|eos|>'
            return response.model_copy(update={"text": body})
        return response

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    return client, control


@pytest.mark.parametrize("short_question", [False, True])
def test_saved_v2_failure_revalidates_locally_and_keeps_original_receipts(
    format_output, monkeypatch, short_question,
):
    client, control = format_output
    # Exercise recovery of an actual pre-autonomy contract, not a newly created plan.
    monkeypatch.setattr(creative_contract, "bind_snapshot", lambda *args: None)
    control.update(short_question=short_question, author_question=short_question)
    attr = "normalize_questions" if short_question else "normalize_optional_text"
    normalizer = getattr(craft_plans, attr)
    parser = runtime.response_parser
    monkeypatch.setattr(craft_plans, attr, lambda value: value)
    monkeypatch.setattr(runtime, "response_parser", lambda batch, call: "craft-plan-format-v2")
    base, draft = create_craft(client)
    done = start(client, base, draft)
    failed = done["calls"][0]
    assert failed["status"] == "local_failure"
    route = f"{base}/{done['id']}/calls/{failed['id']}"
    before = read(client, route)
    monkeypatch.setattr(craft_plans, attr, normalizer)
    monkeypatch.setattr(runtime, "response_parser", parser)
    assert read(client, f"{base}/{done['id']}")["calls"][0]["can_revalidate"]
    result = post(client, route + "/revalidate", {"confirmed": True})
    assert result.status_code == 200, result.text
    assert result.json()["provider_requests"] == "0"
    after = read(client, route)
    assert before["request"] == after["request"] and before["response"] == after["response"]
    restored = read(client, f"{base}/{done['id']}")
    assert restored["calls"][0]["status"] == "completed"
    assert restored["calls"][0]["actual_cost_cny"] == failed["actual_cost_cny"]
    assert restored["status"] in {"paused", "awaiting_plan"}
    assert control["calls"] == ["plan"]
    if short_question:
        assert current(restored, "questions")["payload"]["items"][0]["status"] == "pending"
    else:
        assert current(restored, "plan")["payload"]["future_proposal"] == ""


def test_incomplete_json_with_stop_is_not_locally_repaired_or_automatically_retried(format_output):
    client, control = format_output
    control["truncated_json"] = True
    base, draft = create_craft(client)
    done = start(client, base, draft)
    failed = done["calls"][0]
    assert failed["error_code"] == "plan_output_incomplete"
    assert not failed["can_revalidate"]
    assert "未写完整" in failed["diagnostic"]["message"]
    preview = read(client, f"{base}/{done['id']}/step-recovery-preview")
    assert preview["failure_diagnostic"]["code"] == "plan_output_incomplete"
    assert control["calls"] == ["plan"]
    assert not any(a["kind"] == "plan" for a in done["artifacts"])
