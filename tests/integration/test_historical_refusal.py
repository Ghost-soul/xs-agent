# ruff: noqa: F401 F811
"""Historical refusal artifacts remain readable, but cannot trigger more spending."""

from uuid import UUID

import pytest

from novel_writer.db.models import GenerationBatchRecord
from novel_writer.generation import runtime
from novel_writer.services.errors import ConflictError
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_format_trial import create_trial, formal_rows, unchecked
from tests.integration.test_generation_automation import automated, settle
from tests.integration.test_generation_longform import longform
from tests.integration.test_genre_generation import generation, post, read, start
from tests.integration.test_stage_craft import craft


def test_old_refusal_plan_remains_readable_but_cannot_be_authorized_or_dispatched(
    unchecked, monkeypatch,
):
    client, control = unchecked
    control["chief_text"] = "**I must decline this request.**"
    base, draft = create_trial(client, pause_after_plan=True)
    with monkeypatch.context() as legacy:
        legacy.setattr(runtime, "refusal_message", lambda call: None)
        old = start(client, base, draft)
    assert old["calls"][0]["status"] == "completed"
    before = formal_rows(client, base.split("/")[3])
    route = f"{base}/{old['id']}"
    detail = read(client, route)
    assert "仍引用模型拒绝" in detail["state"]["message"]
    assert not detail["input_recovery_available"] and not detail["step_recovery_available"]
    denied = post(client, route + "/authorize", {
        "confirmed": True, "preview_sha256": old["preview_sha256"],
        "expected_plan_sha256": next(a["sha256"] for a in old["artifacts"]
                                     if a["id"] == old["state"]["plan_id"]),
    })
    assert denied.status_code == 409 and "拒绝说明" in denied.text
    recovery = client.get(route + "/step-recovery-preview", headers={
        "Authorization": "Bearer integration-token",
    })
    assert recovery.status_code == 409 and "拒绝说明" in recovery.text

    async def queued_guard():
        executor = client.app.state.generation
        async with executor.database.session() as session, session.begin():
            batch = await session.get(GenerationBatchRecord, UUID(old["id"]))
            batch.status, batch.next_action = "queued", "write:1"
        with pytest.raises(ConflictError, match="拒绝说明"):
            await executor.claim(UUID(old["id"]))
        await executor.run(UUID(old["id"]))

    client.portal.call(queued_guard)
    assert control["calls"] == ["plan"]
    after = read(client, route)
    assert after["state"]["message"].count("仍引用模型拒绝说明") == 1
    assert after["calls"] == old["calls"] and after["artifacts"] == old["artifacts"]
    assert after["preview_sha256"] == old["preview_sha256"]
    assert formal_rows(client, base.split("/")[3]) == before
