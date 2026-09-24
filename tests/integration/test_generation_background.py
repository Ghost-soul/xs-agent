# ruff: noqa: F811
import json

import pytest

from novel_writer.generation.budget import input_tokens, request_preview
from novel_writer.generation.runtime import GenerationRuntime
from novel_writer.providers.base import ModelRequest
from tests.integration.support import headers
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated  # noqa: F401
from tests.integration.test_generation_feedback import feedback_run  # noqa: F401
from tests.integration.test_generation_longform import longform, stage_create  # noqa: F401
from tests.integration.test_genre_generation import (  # noqa: F401
    create,
    generation,
    post,
    read,
    start,
)
from tests.integration.test_novel_run_rebuild import current, novel_generation  # noqa: F401


@pytest.mark.parametrize("writing", ["background-v1", "guided-v1"])
@pytest.mark.parametrize("milestone", [None, 2])
def test_background_stage_writes_all_units_without_genre_quota_gate(
    feedback_run, monkeypatch, milestone, writing
):
    client, control = feedback_run
    original = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        response = await original(self, call_id, request, profile, spec, counting, api_key)
        if control["calls"][-1] == "plan":
            data = json.loads(response.text)
            for s in data["scenes"]:
                s.update(focus_percent=35, transition_percent=15, other_percent=50)
            response = response.model_copy(update={"text": json.dumps(data)})
        return response

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    control["genre_progress"] = "missing"
    base, draft = stage_create(
        client,
        writing_policy=writing,
        feedback_policy="logic-v1",
        automation_policy="stage-auto-v1",
        unit_limit=6,
        milestone_unit=milestone,
    )
    batch = start(client, base, draft)
    assert batch["state"]["units_finished"], batch["state"]
    assert batch["next_action"] is None
    assert all(c["status"] == "completed" for c in batch["calls"])
    assert len([c for c in control["calls"] if c.startswith("write:")]) == 6
    assert "reader" not in control["calls"]
    assert control["requests"]["plan"]["background_cards"] == draft["snapshot"]["cards"]
    p = current(batch, "plan")["payload"]
    assert "focus_percent" not in json.dumps(p)
    for n in range(1, 7):
        payload = control["requests"][f"write:{n}"]
        assert "cards" not in payload and "background_cards" not in payload
        assert "focus_percent" not in json.dumps(payload)
        assert payload["current_task"] == p["scenes"][n - 1]
    for call in batch["calls"]:
        saved = read(client, f"{base}/{batch['id']}/calls/{call['id']}")["request"]
        request = ModelRequest.model_validate(saved["model_request"])
        assert saved["input_tokens"] == input_tokens(request_preview(request), saved["counting"])


@pytest.mark.parametrize("writing", ["background-v1", "guided-v1"])
def test_background_single_chapter_and_author_plan_edit_keep_no_quotas(novel_generation, writing):
    client, control = novel_generation
    base, draft = create(
        client,
        workflow="novel-run-v1",
        feedback_policy="logic-v1",
        writing_policy=writing,
        pause_after_plan=True,
    )
    batch = start(client, base, draft)
    p = current(batch, "plan")
    assert "focus_percent" not in json.dumps(p["payload"])
    assert json.loads(control["requests"]["plan"])["background_cards"]
    edited = client.put(
        f"{base}/{batch['id']}/plan",
        headers=headers("background-plan-edit"),
        json={
            "plan": {**p["payload"], "world_context": "渡口是本阶段的现场"},
            "expected_plan_sha256": p["sha256"],
            "author_note": "让人物自然相处",
        },
    )
    assert edited.status_code == 200, edited.text
    batch = start(client, base, edited.json())
    assert all(c["status"] == "completed" for c in batch["calls"]), batch["state"]
    writer = json.loads(control["requests"]["write"])
    assert "cards" not in writer and writer["world_context"] == "渡口是本阶段的现场"
