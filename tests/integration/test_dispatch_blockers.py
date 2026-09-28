# ruff: noqa: F401 F811
"""Cross-batch dispatch blockers and closing unknown calls without retrying."""

from uuid import UUID, uuid4

import pytest

from novel_writer.db.models import GenerationCallRecord
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_longform import longform
from tests.integration.test_genre_generation import generation, post, read
from tests.integration.test_stage_craft import OPTIONS, create_craft
from tests.integration.test_step_recovery import fail, recovery


def trial_preview(client, base, batch):
    spec = {
        **batch["spec"], **OPTIONS, "craft_policy": "stage-craft-v1",
        "milestone_unit": None,
    }
    response = post(
        client, base + "/trial-preview?random_narratives=false&replace_previous=false", spec,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_old_unknown_blocks_trial_and_closure_preserves_evidence_without_dispatch(
    recovery, monkeypatch,
):
    client, control, base, target, failed = fail(recovery, "memory:2")
    assert failed["step_recovery_available"]
    unknown = next(c for c in failed["calls"] if c["status"] == "outcome_uncertain")
    original = read(client, target + f"/calls/{unknown['id']}")
    draft = trial_preview(client, base, failed)
    blockers = draft["dispatch_blockers"]
    assert len(blockers) == 1
    assert blockers[0]["batch_id"] == failed["id"]
    assert blockers[0]["call_id"] == unknown["id"]
    assert blockers[0]["action"] == "memory:2"
    assert blockers[0]["status"] == "outcome_uncertain"
    assert set(blockers[0]) == {
        "batch_id", "call_id", "action", "status", "model", "started_at",
    }
    count = len(control["calls"])
    new_target = f"{base}/{draft['id']}"
    authorization = {"preview_sha256": draft["preview_sha256"], "confirmed": True}
    rejected = post(client, new_target + "/authorize", authorization)
    assert rejected.status_code == 409
    assert failed["id"] in rejected.json()["detail"]
    assert "memory:2" in rejected.json()["detail"]
    # Independent projects must remain unblocked.
    _, other = create_craft(client)
    assert other["dispatch_blockers"] == []
    assert len(control["calls"]) == count

    payload = {"confirmed": True, "note": "已核对原请求不再执行，费用仍待供应商确认"}
    key = str(uuid4())
    closed = post(client, target + "/resolve-unknown", payload, key)
    assert closed.status_code == 200, closed.text
    value = closed.json()
    closed_call = next(c for c in value["calls"] if c["id"] == unknown["id"])
    assert closed_call["status"] == "uncertain_closed"
    assert closed_call["actual_cost_cny"] == unknown["actual_cost_cny"] is None
    assert value["dispatch_blockers"] == [] and value["next_action"] is None
    assert value["step_recovery_available"]  # An explicit later retry remains possible.
    for old in failed["artifacts"]:
        assert old in value["artifacts"]
    after = read(client, target + f"/calls/{unknown['id']}")
    for field in ("request", "response", "request_sha256"):
        assert after[field] == original[field]
    assert post(client, target + "/resolve-unknown", payload, key).json() == value
    assert len(control["calls"]) == count
    assert read(client, new_target)["dispatch_blockers"] == []
    # Closing alone never authorizes a new batch. Only a separate command can queue it.
    assert read(client, new_target)["status"] == "draft"
    monkeypatch.setattr(client.app.state.generation, "start", lambda *_: None)
    assert post(client, new_target + "/authorize", authorization).status_code == 200
    assert len(control["calls"]) == count


def test_executing_call_cannot_be_closed_or_bypassed(recovery):
    client, control, base, target, failed = fail(recovery, "memory:2")
    call_id = next(c["id"] for c in failed["calls"] if c["status"] == "outcome_uncertain")

    async def mark_executing():
        async with client.app.state.database.session() as session, session.begin():
            call = await session.get(GenerationCallRecord, UUID(call_id))
            call.status = "executing"

    client.portal.call(mark_executing)
    draft = trial_preview(client, base, failed)
    assert draft["dispatch_blockers"][0]["status"] == "executing"
    count = len(control["calls"])
    result = post(client, target + "/resolve-unknown", {"confirmed": True, "note": "不能关闭"})
    assert result.status_code == 409 and "仍在执行" in result.json()["detail"]
    result = post(client, f"{base}/{draft['id']}/authorize", {
        "confirmed": True, "preview_sha256": draft["preview_sha256"],
    })
    assert result.status_code == 409
    assert len(control["calls"]) == count
