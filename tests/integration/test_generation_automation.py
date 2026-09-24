# ruff: noqa: F811
import asyncio
import json
from uuid import UUID, uuid4

import pytest

from novel_writer.generation.content import json_text
from novel_writer.generation.runtime import GenerationRuntime
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_longform import longform, stage_create  # noqa: F401
from tests.integration.test_genre_generation import (  # noqa: F401
    create,
    generation,
    post,
    read,
    start,
)
from tests.integration.test_novel_run_rebuild import current


@pytest.fixture
def automated(longform, monkeypatch):
    client, control = longform
    previous = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        response = await previous(self, call_id, request, profile, spec, counting, api_key)
        action = control["calls"][-1]
        if action == "plan" or action.startswith("chief:"):
            data = json.loads(response.text)
            plan = data if action == "plan" else data["plan"]
            question = "正式档案中人物身份不一致，请确认"
            if control.get("author_question") and action == "plan":
                plan["questions"] = [question]
                plan["question_scopes"] = {question: "current_unit"}
            if spec.automation_policy == "stage-auto-v1":
                plan["story_questions"] = ["谁先表达在意？"]
                plan["author_question_reasons"] = {
                    q: {
                        "kind": "missing_canonical_fact",
                        "source": "formal_reference.characters",
                        "why_blocked": "已有身份冲突，不能自行改写",
                    }
                    for q in plan["questions"]
                }
            elif control.get("legacy_question") and action == "plan":
                plan["questions"] = ["谁先表达在意？"]
                plan["question_scopes"] = {plan["questions"][0]: "current_unit"}
            response = response.model_copy(update={"text": json_text(data)})
        return response

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    return client, control


def continue_payload(batch, **changes):
    return {
        "confirmed": True,
        "preview_sha256": batch["preview_sha256"],
        "expected_plan_sha256": current(batch, "plan")["sha256"],
        "expected_questions_sha256": current(batch, "questions")["sha256"],
        **changes,
    }


def settle(client, base, batch_id):
    async def wait():
        task = client.app.state.generation.tasks.get(UUID(batch_id))
        if task:
            await asyncio.wait_for(asyncio.shield(task), 10)

    client.portal.call(wait)
    return read(client, f"{base}/{batch_id}")


def test_one_authorization_runs_all_internal_steps_and_keeps_story_questions_internal(automated):
    client, control = automated
    base, draft = stage_create(client, automation_policy="stage-auto-v1")
    done = start(client, base, draft)
    assert control["calls"][-1] == "reader", done["state"]
    assert control["calls"].count("plan") == 1
    assert len(current(done, "units")["payload"]["items"]) == 3
    assert current(done, "questions")["payload"]["items"] == []
    assert current(done, "plan")["payload"]["story_questions"] == ["谁先表达在意？"]
    assert "execution_policy" not in control["requests"]["reader"]
    assert done["status"] != "adopted"


def test_author_blocker_requires_answer_then_same_action_continues_without_second_authorization(
    automated,
):
    client, control = automated
    control["author_question"] = True
    base, draft = stage_create(client, automation_policy="stage-auto-v1")
    paused = start(client, base, draft)
    assert paused["status"] == "awaiting_plan" and control["calls"] == ["plan"]
    question = current(paused, "questions")["payload"]["items"][0]["question"]
    rejected = post(
        client,
        f"{base}/{paused['id']}/continue-stage",
        continue_payload(paused, delegated_questions=[question]),
    )
    assert rejected.status_code == 409 and control["calls"] == ["plan"]
    key = str(uuid4())
    payload = continue_payload(
        paused, question_answers={question: "以人物档案所列身份为准，保持既定边界"}
    )
    result = post(client, f"{base}/{paused['id']}/continue-stage", payload, key)
    assert result.status_code == 200, result.text
    done = settle(client, base, paused["id"])
    assert control["calls"][-1] == "reader", done["state"]
    count = len(control["calls"])
    assert post(client, f"{base}/{paused['id']}/continue-stage", payload, key).status_code == 200
    assert len(control["calls"]) == count
    assert done["preview_sha256"] == paused["preview_sha256"] and done["spec"] == paused["spec"]


def test_legacy_plot_question_can_be_explicitly_delegated_without_repeating_chief(automated):
    client, control = automated
    control["legacy_question"] = True
    base, draft = stage_create(client, pause_after_plan=True, automation_policy="legacy-v1")
    paused = start(client, base, draft)
    question = current(paused, "questions")["payload"]["items"][0]["question"]
    stale = post(
        client,
        f"{base}/{paused['id']}/continue-stage",
        continue_payload(
            paused, expected_questions_sha256="0" * 64, delegated_questions=[question]
        ),
    )
    assert stale.status_code == 409 and control["calls"] == ["plan"]
    result = post(
        client,
        f"{base}/{paused['id']}/continue-stage",
        continue_payload(paused, delegated_questions=[question]),
    )
    assert result.status_code == 200, result.text
    done = settle(client, base, paused["id"])
    assert control["calls"].count("plan") == 1 and control["calls"][-1] == "reader", done["state"]
    answer = current(done, "questions")["payload"]["items"][0]["author_answer"]
    assert "委托" in answer and "不是新增正式事实" in answer


@pytest.mark.parametrize(
    "change", [{}, {"preview_sha256": "0" * 64}, {"delegated_questions": ["不存在的问题"]}]
)
def test_unanswered_or_wrong_binding_never_dispatches_or_changes_plan(automated, change):
    client, control = automated
    control["legacy_question"] = True
    base, draft = stage_create(client, automation_policy="legacy-v1")
    paused = start(client, base, draft)
    result = post(
        client, f"{base}/{paused['id']}/continue-stage", continue_payload(paused, **change)
    )
    assert result.status_code == 409
    unchanged = read(client, f"{base}/{paused['id']}")
    assert unchanged["state"] == paused["state"] and unchanged["artifacts"] == paused["artifacts"]
    assert control["calls"] == ["plan"]
