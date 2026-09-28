# ruff: noqa: F401 F811
"""New previews replace inactive stages, retaining evidence and explicit dispatch budgets."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from novel_writer.db.models import GenerationBatchRecord, GenerationCallRecord
from novel_writer.generation.content import fingerprint
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_longform import longform
from tests.integration.test_genre_generation import generation, post, read
from tests.integration.test_stage_craft import OPTIONS, create_craft
from tests.integration.test_step_recovery import fail, recovery


def new_spec(batch):
    return {
        **batch["spec"], **OPTIONS, "craft_policy": "stage-craft-v1", "milestone_unit": None,
    }


@pytest.mark.parametrize("endpoint", ["", "/random-preview", "/trial-preview"])
def test_default_replacement_preserves_unknown_and_drafts_without_resending(
    recovery, monkeypatch, endpoint,
):
    client, control, base, target, old = fail(recovery, "memory:2")
    original = {c["id"]: read(client, target + f"/calls/{c['id']}") for c in old["calls"]}
    _, other = create_craft(client)
    sent = len(control["calls"])
    response = post(client, base + endpoint, new_spec(old))
    assert response.status_code == 200, response.text
    new = response.json()
    history = read(client, target)
    assert new["status"] == "draft" and new["calls"] == []
    assert new["dispatch_blockers"] == []
    assert history["status"] == "archived" and history["next_action"] is None
    assert not history["step_recovery_available"] and not history["memory_recovery_available"]
    assert not history["input_recovery_available"]
    for before, after in zip(old["calls"], history["calls"], strict=True):
        for field in ("id", "status", "actual_cost_cny", "usage", "request_sha256"):
            assert after[field] == before[field]
    for field in ("snapshot", "spec", "preview_sha256"):
        assert history[field] == old[field]
    assert all(a in history["artifacts"] for a in old["artifacts"])
    for call_id, prior in original.items():
        assert read(client, target + f"/calls/{call_id}") == prior
    unknown = next(c for c in history["calls"] if c["status"] == "outcome_uncertain")
    assert unknown["actual_cost_cny"] is None
    receipt = next(a["payload"] for a in new["artifacts"] if a["kind"] == "stage_replacement")
    assert receipt["unknown_call_ids"] == receipt["unknown_cost_call_ids"] == [unknown["id"]]
    assert receipt["historical_costs_in_new_budget"] is False
    other_target = f"/api/projects/{other['project_id']}/generation-batches/{other['id']}"
    assert read(client, other_target) == other

    # Obsolete links and late executor callbacks must not resurrect the superseded stage.
    runtime = client.app.state.generation
    client.portal.call(runtime.run, UUID(old["id"]))
    client.portal.call(runtime.compile_response, UUID(old["id"]), UUID(unknown["id"]))
    client.portal.call(runtime.local_failure, UUID(old["id"]), "late callback")
    assert read(client, target) == history
    assert client.get(target + "/step-recovery-preview", headers={
        "Authorization": "Bearer integration-token",
    }).status_code == 409
    assert post(client, target + "/authorize", {
        "confirmed": True, "preview_sha256": old["preview_sha256"],
    }).status_code == 409
    assert len(control["calls"]) == sent
    monkeypatch.setattr(runtime, "start", lambda *_: None)
    assert post(client, f"{base}/{new['id']}/authorize", {
        "confirmed": True, "preview_sha256": new["preview_sha256"],
    }).status_code == 200
    assert len(control["calls"]) == sent


@pytest.mark.parametrize("status", ["executing", "response_saved"])
def test_inflight_or_pending_compilation_is_never_replaced(recovery, status):
    client, control, base, target, old = fail(recovery, "memory:2")
    unknown = next(c for c in old["calls"] if c["status"] == "outcome_uncertain")

    async def make_active():
        async with client.app.state.database.session() as session, session.begin():
            call = await session.get(GenerationCallRecord, UUID(unknown["id"]))
            call.status = status

    client.portal.call(make_active)
    before = read(client, target)
    records = read(client, base)
    response = post(client, base, new_spec(old))
    assert response.status_code == 409, response.text
    assert "旧阶段尚未修改" in response.json()["detail"]
    assert read(client, target) == before
    assert read(client, base) == records


def test_late_buffer_recovery_keeps_history_archived_and_does_not_block_new_preview(recovery):
    client, control, base, target, old = fail(recovery, "memory:2", mode="timeout")
    unknown = next(c for c in old["calls"] if c["status"] == "outcome_uncertain")
    source = read(client, target + f"/calls/{unknown['id']}")
    assert source["response"] is None

    async def interrupted():
        async with client.app.state.database.session() as session, session.begin():
            call = await session.get(GenerationCallRecord, UUID(unknown["id"]))
            call.error_code = "process_interrupted"

    client.portal.call(interrupted)
    first = post(client, base, new_spec(old))
    assert first.status_code == 200
    archived = read(client, target)
    raw = {"text": "原请求在本地缓冲中的完整响应", "usage": None}
    raw["sha256"] = fingerprint(raw)
    entry = {
        "batch_id": old["id"], "call_id": unknown["id"], "response": raw,
        "model_request_sha256": fingerprint(source["request"]["model_request"]),
        "received_at": datetime.now(UTC).isoformat(),
    }

    async def recover():
        await client.app.state.generation.persist_response(entry, recovering=True)

    client.portal.call(recover)
    saved = read(client, target)
    assert saved["status"] == "archived" and saved["state"] == archived["state"]
    assert read(client, target + f"/calls/{unknown['id']}")["response"] == raw
    assert post(client, base, new_spec(old)).status_code == 200


def test_invalid_preview_and_failed_transaction_leave_previous_usable(generation, monkeypatch):
    from novel_writer.generation import service as module
    from novel_writer.generation.service import GenerationService

    client, control = generation
    base, old = create_craft(client)
    target = f"{base}/{old['id']}"
    original = module.prepare_preview

    def blocked(*args):
        original(*args)
        args[1]["blockers"] = ["fixture input capacity blocked"]

    with monkeypatch.context() as patch:
        patch.setattr(module, "prepare_preview", blocked)
        response = post(client, base, old["spec"])
        assert response.status_code == 200
        assert response.json()["snapshot"]["blockers"]
        assert not any(a["kind"] == "stage_replacement" for a in response.json()["artifacts"])
    assert read(client, target) == old
    before = read(client, base)
    detail = GenerationService.detail

    async def fail_detail(self, batch):
        if batch.id != UUID(old["id"]):
            raise RuntimeError("fixture transaction failure after replacement")
        return await detail(self, batch)

    with monkeypatch.context() as patch:
        patch.setattr(GenerationService, "detail", fail_detail)
        with pytest.raises(RuntimeError, match="transaction failure"):
            post(client, base, old["spec"])
    assert read(client, target) == old
    assert read(client, base) == before
    assert not control["calls"]


def test_network_replays_and_concurrent_previews_cannot_revive_old_stages(generation):
    client, control = generation
    base, old = create_craft(client)
    key = str(uuid4())
    first = post(client, base, old["spec"], key).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(post, client, base, old["spec"]) for _ in range(2)]
        results = [f.result(timeout=15) for f in futures]
    assert all(r.status_code == 200 for r in results)
    history = read(client, base)
    assert sum(b["status"] == "draft" for b in history) == 1
    latest = next(b for b in history if b["status"] == "draft")
    assert latest["id"] in {r.json()["id"] for r in results}
    assert post(client, base, old["spec"], key).json() == first
    assert read(client, base) == history
    assert not control["calls"]


def test_queued_stage_is_retired_and_adopted_stage_is_preserved(generation, monkeypatch):
    client, control = generation
    base, adopted = create_craft(client)

    async def mark_adopted():
        async with client.app.state.database.session() as session, session.begin():
            batch = await session.get(GenerationBatchRecord, UUID(adopted["id"]))
            batch.status, batch.next_action = "adopted", None

    client.portal.call(mark_adopted)
    adopted = read(client, f"{base}/{adopted['id']}")
    queued = post(client, base, adopted["spec"]).json()
    runtime = client.app.state.generation
    monkeypatch.setattr(runtime, "start", lambda *_: None)
    assert post(client, f"{base}/{queued['id']}/authorize", {
        "confirmed": True, "preview_sha256": queued["preview_sha256"],
    }).status_code == 200
    replacement = post(client, base, adopted["spec"])
    assert replacement.status_code == 200
    assert read(client, f"{base}/{queued['id']}")["status"] == "archived"
    assert read(client, f"{base}/{adopted['id']}") == adopted
    client.portal.call(runtime.run, UUID(queued["id"]))
    assert not control["calls"]
