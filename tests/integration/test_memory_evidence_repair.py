# ruff: noqa: F811
import json
from uuid import UUID

import pytest

from novel_writer.generation import diagnostics, reports, runtime
from novel_writer.generation.content import json_text
from novel_writer.generation.novel import parser_for
from novel_writer.generation.runtime import GenerationRuntime
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated, settle  # noqa: F401
from tests.integration.test_generation_longform import longform, stage_create  # noqa: F401
from tests.integration.test_genre_generation import (  # noqa: F401
    create,
    generation,
    post,
    read,
    start,
)
from tests.integration.test_novel_run_rebuild import current, novel_generation  # noqa: F401


@pytest.fixture(params=["automated", "novel_generation"])
def repaired(request, monkeypatch):
    client, control = request.getfixturevalue(request.param)
    control["longform"] = request.param == "automated"
    previous = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        response = await previous(self, call_id, request, profile, spec, counting, api_key)
        action = control["calls"][-1]
        if action.startswith("write"):
            body = "\n\n".join(f"{action} 第{i}段。" + "她接受了邀请。" * 9 for i in range(30))
            return response.model_copy(update={"text": body})
        if action.startswith("memory"):
            data = json.loads(response.text)
            prompt = json.loads(request.user_prompt)
            ids = [p["id"] for p in prompt["candidate"]]
            data["position_paragraph_ids"] = ids[:30]
            data["changes"][0]["paragraph_ids"] = ids[:27]
            data["changes"] += [
                {
                    "collection": "reader_promises",
                    "promise_action": "established",
                    "values": {"kind": "relationship", "summary": "下一次同行"},
                    "observation": "邀请的后续期待",
                    "paragraph_ids": ids[:30],
                },
                {
                    "collection": "scenes",
                    "values": {"summary": "相邀", "ordinal": 1, "event_ids": ["$change:0"]},
                    "observation": "邀请完成",
                    "paragraph_ids": ids[:30],
                },
            ]
            if control.get("foreign_position"):
                data["position_paragraph_ids"][0] = "foreign:1"
            return response.model_copy(update={"text": json_text(data)})
        return response

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    return client, control


def draft_for(client, control):
    options = {
        "writing_policy": "guided-v1",
        "feedback_policy": "logic-v1",
        "enable_checker": False,
        "enable_reader": False,
    }
    if control["longform"]:
        return stage_create(
            client, unit_limit=2, chapter_count=1, automation_policy="stage-auto-v1", **options
        )
    return create(client, workflow="novel-run-v1", **options)


def test_long_references_and_scene_dependency_work_in_creation_and_amendment(repaired):
    client, control = repaired
    base, draft = draft_for(client, control)
    batch = start(client, base, draft)
    assert all(c["status"] == "completed" for c in batch["calls"]), batch["state"]
    assert current(batch, "memory")["payload"]["status"] == "complete"
    target = f"{base}/{batch['id']}"
    calls = [c for c in batch["calls"] if c["action"].startswith("memory")]
    assert len(calls) == (2 if control["longform"] else 1)
    for call in calls:
        saved = read(client, target + f"/calls/{call['id']}")
        data = json.loads(saved["response"]["text"])
        assert len(data["position_paragraph_ids"]) == 30
        assert len(data["changes"][0]["paragraph_ids"]) == 27
    if control["longform"]:
        handoffs = [a["payload"] for a in batch["artifacts"] if a["kind"] == "handoff"]
        assert len(handoffs) == 2
        assert all(len(h["position_evidence"]) == 30 for h in handoffs)
        assert len(handoffs[-1]["working_state"]["reader_promises"]) == 2
        assert control["calls"] == ["plan", "write:1", "memory:1", "write:2", "memory:2"]
    preview = post(
        client,
        target + "/amendment-preview",
        {
            "candidate_sha256": current(batch, "candidate")["sha256"],
            "mode": "verify",
            "instruction": "重新核对事实",
            "output_limit": 24000,
            "max_cost_cny": "1",
            "enable_checker": False,
            "enable_reader": False,
        },
    )
    assert preview.status_code == 200, preview.text
    response = post(
        client,
        target + "/amendment-authorize",
        {
            "preview_sha256": preview.json()["preview_sha256"],
            "confirmed": True,
        },
    )
    assert response.status_code == 200, response.text
    after = settle(client, base, batch["id"])
    assert after["calls"][-1]["action"] == "memory_amend"
    assert after["calls"][-1]["status"] == "completed", after["state"]
    assert current(after, "memory")["payload"]["status"] == "complete"
    assert current(after, "candidate") == current(batch, "candidate")


@pytest.mark.parametrize("invalid_evidence", [False, True])
def test_saved_old_failure_has_one_local_attempt_without_resend(
    repaired, monkeypatch, invalid_evidence
):
    client, control = repaired
    control["foreign_position"] = invalid_evidence
    base, draft = draft_for(client, control)
    # Reproduce the old parser and its compilation binding without altering the
    # saved provider response or request when the new parser becomes available.
    with monkeypatch.context() as old:
        old.setattr(reports, "_LocalMemoryReport", reports.MemoryReport)
        old.setattr(runtime, "parser_for", lambda revision, action=None: parser_for(revision))
        old.setattr(diagnostics, "parser_for", lambda revision, action=None: parser_for(revision))
        failed = start(client, base, draft)
    target = f"{base}/{failed['id']}"
    call = next(c for c in failed["calls"] if c["action"].startswith("memory"))
    assert call["status"] == "local_failure"
    assert "at most 24" in failed["state"]["message"]
    old_binding = f"{call['id']}:{parser_for(failed['revision'])}"
    assert old_binding in failed["state"]["compiled"]
    before = read(client, target + f"/calls/{call['id']}")
    assert next(c for c in read(client, target)["calls"] if c["id"] == call["id"])["can_revalidate"]
    count = len(control["calls"])
    result = post(client, target + f"/calls/{call['id']}/revalidate", {"confirmed": True})
    assert result.status_code == 200 and result.json()["provider_requests"] == "0", result.text
    after = read(client, target)
    saved = read(client, target + f"/calls/{call['id']}")
    for key in ("request", "response"):
        assert saved[key] == before[key]
    after_call = next(c for c in after["calls"] if c["id"] == call["id"])
    assert after_call["actual_cost_cny"] == call["actual_cost_cny"]
    assert old_binding in after["state"]["compiled"]
    assert f"{call['id']}:memory-evidence-v4" in after["state"]["compiled"]
    assert len(control["calls"]) == count
    assert current(after, "candidate") == current(failed, "candidate")
    assert set(a["id"] for a in failed["artifacts"]) <= set(a["id"] for a in after["artifacts"])
    if invalid_evidence:
        assert after_call["status"] == "local_failure"
        assert "证据" in after["state"]["message"]
    else:
        assert after_call["status"] == "completed"
        assert after["status"] == ("paused" if control["longform"] else "needs_attention")
        assert current(after, "memory")["payload"]["status"] == "complete"
        # An internal replay cannot rewrite a completed handoff after an upgrade.
        with monkeypatch.context() as newer:
            newer.setattr(runtime, "parser_for", lambda *_: "future-parser-fixture")
            client.portal.call(
                client.app.state.generation.compile_response, UUID(after["id"]), UUID(call["id"])
            )
        assert read(client, target) == after
    again = post(client, target + f"/calls/{call['id']}/revalidate", {"confirmed": True})
    assert again.status_code == 409
    assert len(control["calls"]) == count
