# ruff: noqa: F811
import json
from copy import deepcopy

import pytest

from novel_writer.generation import diagnostics, guidance, runtime
from novel_writer.generation import longform as stages
from novel_writer.generation.budget import input_tokens, request_preview
from novel_writer.generation.novel import parser_for
from novel_writer.generation.schemas import LONGFORM_PLAN_PARSER_REVISION
from novel_writer.providers.base import ModelRequest
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import (  # noqa: F401
    automated,
    continue_payload,
    settle,
)
from tests.integration.test_generation_feedback import feedback_run  # noqa: F401
from tests.integration.test_generation_longform import longform, stage_create  # noqa: F401
from tests.integration.test_genre_generation import generation, post, read, start  # noqa: F401
from tests.integration.test_narrative_prompts import options
from tests.integration.test_novel_run_rebuild import current


@pytest.fixture
def shorter(feedback_run, monkeypatch):
    client, control = feedback_run
    previous = runtime.GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        response = await previous(self, call_id, request, profile, spec, counting, api_key)
        action = control["calls"][-1]
        if action == "plan" or action.startswith("chief:"):
            data = json.loads(response.text)
            plan = data if action == "plan" else data["plan"]
            if action == "plan":
                plan["scenes"] = [
                    deepcopy(plan["scenes"][0]) for _ in range(control.get("count", 3))
                ]
                if control.get("foreign"):
                    plan["scenes"][-1]["character_ids"] = ["outside-cast"]
                control["plan"] = deepcopy(plan)
            elif "checkpoint_count" in control:
                plan["scenes"] = plan["scenes"][: control["checkpoint_count"]]
                data["decision"] = "revise"
            response = response.model_copy(update={"text": json.dumps(data)})
        return response

    monkeypatch.setattr(runtime.GenerationRuntime, "dispatch", dispatch)
    return client, control


def draft_for(client, **changes):
    return stage_create(
        client,
        **options(
            **{
                "plan_policy": "bounded-v1",
                "unit_limit": 5,
                "chapter_count": 2,
                "enable_checker": False,
                "enable_reader": False,
                **changes,
            }
        ),
    )


@pytest.mark.parametrize("count,milestone", [(1, None), (3, None), (5, None), (3, 2), (3, 4)])
def test_executes_actual_plan_and_never_consumes_unused_slots(shorter, count, milestone):
    client, control = shorter
    control["count"] = count
    base, draft = draft_for(client, milestone_unit=milestone)
    done = start(client, base, draft)
    assert done["state"].get("units_finished"), done["state"]
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    assert [a for a in control["calls"] if a.startswith("write:")] == [
        f"write:{n}" for n in range(1, count + 1)
    ]
    assert len(current(done, "units")["payload"]["items"]) == count
    assert ("chief:1" in control["calls"]) == bool(milestone and milestone < count)
    writer = control["requests"][f"write:{count}"]
    assert writer["unit_position"] == {"current": count, "total": count, "last_unit": True}
    assert writer["unit_target_characters"] == round(4000 / count)
    assert done["spec"] == draft["spec"] and done["snapshot"] == draft["snapshot"]
    for call in done["calls"]:
        saved = read(client, f"{base}/{done['id']}/calls/{call['id']}")["request"]
        request = ModelRequest.model_validate(saved["model_request"])
        assert saved["input_tokens"] == input_tokens(request_preview(request), saved["counting"])


@pytest.mark.parametrize("count,foreign", [(0, False), (6, False), (3, True)])
def test_invalid_plans_cannot_reach_writer(shorter, count, foreign):
    client, control = shorter
    control.update(count=count, foreign=foreign)
    base, draft = draft_for(client)
    done = start(client, base, draft)
    assert done["calls"][0]["status"] == "local_failure"
    assert control["calls"] == ["plan"] and "plan_id" not in done["state"]


def test_saved_old_failure_recovers_locally_once_then_continues_without_repeating_chief(
    shorter, monkeypatch
):
    client, control = shorter
    base, draft = draft_for(
        client, plan_policy="exact-v1", card_selection_policy="legacy-v1", narrative_card_ids=[]
    )
    with monkeypatch.context() as old:
        old.setattr(stages, "parse_stage", guidance.parse_stage)
        old.setattr(runtime, "parser_for", lambda revision, action=None: parser_for(revision))
        old.setattr(diagnostics, "parser_for", lambda revision, action=None: parser_for(revision))
        failed = start(client, base, draft)
    assert "单元设计数量与冻结上限不符" in failed["state"]["message"]
    route = f"{base}/{failed['id']}"
    call = failed["calls"][0]
    before = read(client, route + f"/calls/{call['id']}")
    assert read(client, route)["calls"][0]["can_revalidate"]
    result = post(client, route + f"/calls/{call['id']}/revalidate", {"confirmed": True})
    assert result.status_code == 200, result.text
    assert result.json()["provider_requests"] == "0"
    recovered = read(client, route)
    saved = read(client, route + f"/calls/{call['id']}")
    assert recovered["status"] == "paused" and recovered["next_action"] == "write:1"
    assert control["calls"] == ["plan"]
    assert len(current(recovered, "plan")["payload"]["scenes"]) == 3
    for key in ("spec", "snapshot", "preview_sha256"):
        assert recovered[key] == failed[key]
    assert saved["request"] == before["request"] and saved["response"] == before["response"]
    assert recovered["calls"][0]["actual_cost_cny"] == call["actual_cost_cny"]
    assert set(a["id"] for a in failed["artifacts"]) <= set(a["id"] for a in recovered["artifacts"])
    assert f"{call['id']}:{parser_for(failed['revision'])}" in recovered["state"]["compiled"]
    assert f"{call['id']}:{LONGFORM_PLAN_PARSER_REVISION}" in recovered["state"]["compiled"]
    assert (
        post(client, route + f"/calls/{call['id']}/revalidate", {"confirmed": True}).status_code
        == 409
    )
    continued = post(client, route + "/continue-stage", continue_payload(recovered))
    assert continued.status_code == 200, continued.text
    done = settle(client, base, failed["id"])
    assert done["state"].get("units_finished"), done["state"]
    assert control["calls"] == [
        "plan",
        "write:1",
        "memory:1",
        "write:2",
        "memory:2",
        "write:3",
        "memory:3",
    ]


def test_truncated_plan_stays_incomplete_and_cannot_be_locally_completed(shorter):
    client, control = shorter
    control["incomplete_action"] = "plan"
    base, draft = draft_for(client)
    done = start(client, base, draft)
    call = done["calls"][0]
    assert not call["can_revalidate"] and "plan_id" not in done["state"]
    result = post(client, f"{base}/{done['id']}/calls/{call['id']}/revalidate", {"confirmed": True})
    assert result.status_code == 409 and control["calls"] == ["plan"]


@pytest.mark.parametrize("remaining_count,valid", [(1, False), (2, True)])
def test_checkpoint_can_finish_a_stage_but_never_remove_written_units(
    shorter, remaining_count, valid
):
    client, control = shorter
    control["checkpoint_count"] = remaining_count
    base, draft = draft_for(client, milestone_unit=2)
    done = start(client, base, draft)
    assert control["calls"] == ["plan", "write:1", "memory:1", "write:2", "memory:2", "chief:1"]
    assert len(current(done, "units")["payload"]["items"]) == 2
    assert done["calls"][-1]["status"] == ("completed" if valid else "local_failure"), done["state"]
    assert len(current(done, "plan")["payload"]["scenes"]) == (2 if valid else 3)
