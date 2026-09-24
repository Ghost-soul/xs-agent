# ruff: noqa: F811
from copy import deepcopy
from decimal import Decimal
from uuid import uuid4

import pytest

from novel_writer.generation import budget, input_recovery, request_preparation
from novel_writer.services.errors import WorkflowError
from tests.integration.support import headers
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import settle
from tests.integration.test_generation_longform import longform, stage_create  # noqa: F401
from tests.integration.test_genre_generation import generation, post, read, start  # noqa: F401
from tests.integration.test_novel_run_rebuild import current


def test_preview_without_an_input_override_defaults_to_200000(longform):
    client, control = longform
    base, original = stage_create(client)
    payload = {**original["spec"], "feedback_policy": "logic-v1"}
    del payload["input_limit"]
    response = post(client, base, payload)
    assert response.status_code == 200, response.text
    assert response.json()["spec"]["input_limit"] == 200000
    assert response.json()["status"] == "draft" and control["calls"] == []


@pytest.fixture
def capacity_blocked(longform, monkeypatch):
    client, control = longform
    store = client.app.state.provider_profile_store
    profile = store.get("fixture")
    profile.models[0].max_output_tokens = 100000
    profile.models[0].context_window = 300000
    store.save(profile)
    original = request_preparation.validate_capacity
    # This suite tests authorization and slot continuity with a deterministic
    # local counter. Exact counting is covered by the context-budget tests.
    monkeypatch.setattr(budget, "input_tokens", lambda *_: 5000)

    def capacity(text, request, spec, profile, counting):
        if '"effective_plan"' in request.user_prompt:
            if spec.input_limit < 68000:
                raise WorkflowError("最终输入计数/上界 65000 超过允许值 58000")
            return 65000
        return original(text, request, spec, profile, counting)

    monkeypatch.setattr(request_preparation, "validate_capacity", capacity)
    base, draft = stage_create(client, input_limit=58000)
    batch = start(client, base, draft)
    assert batch["status"] == "needs_attention" and control["calls"] == ["plan"]
    return client, control, base, batch


def test_preview_preserves_chief_then_explicit_authorization_only_runs_unused_slots(
    capacity_blocked,
):
    client, control, base, before = capacity_blocked
    target = f"{base}/{before['id']}"
    preview = read(client, target + "/input-preview")
    assert preview["input_limit"] == 200000
    assert preview["writer_input_tokens"] == 65000 and not preview["blockers"]
    assert preview["remaining_slots"][0] == "write:1"
    assert read(client, target) == before and control["calls"] == ["plan"]
    assert read(client, target + "/input-preview?input_limit=58000")["blockers"]
    key = str(uuid4())
    payload = {
        "input_limit": 200000,
        "preview_sha256": preview["preview_sha256"],
        "confirmed": True,
    }
    assert (
        post(
            client, target + "/input-authorize", {**payload, "preview_sha256": "0" * 64}
        ).status_code
        == 409
    )
    response = post(client, target + "/input-authorize", payload, key)
    assert response.status_code == 200, response.text
    done = settle(client, base, before["id"])
    assert control["calls"].count("plan") == 1 and control["calls"][-1] == "reader", done["state"]
    assert done["snapshot"] == before["snapshot"] and done["spec"] == before["spec"]
    assert done["preview_sha256"] == before["preview_sha256"]
    assert done["calls"][0] == before["calls"][0]
    for call in done["calls"][1:]:
        request = read(client, target + f"/calls/{call['id']}")["request"]
        assert request["effective_input_limit"] == 200000
        assert request["model_request"]["max_output_tokens"] == 100000
        assert request["input_selection"]["target"] == 200000
        assert request["input_authorization_id"] == done["state"]["input_authorization_id"]
    count = len(control["calls"])
    assert post(client, target + "/input-authorize", payload, key).status_code == 200
    assert len(control["calls"]) == count
    assert read(client, target)["status"] != "adopted"


def test_author_can_change_unwritten_goal_retaining_original_and_invalidating_preview(
    capacity_blocked,
):
    client, control, base, before = capacity_blocked
    target = f"{base}/{before['id']}"
    original = current(before, "plan")
    preview = read(client, target + "/input-preview?input_limit=68000")
    plan = deepcopy(original["payload"])
    plan["chapter_goal"] = "一次主动告白改变共同出行的决定"
    plan["major_turn"] = "对方选择留下并明确回应"
    response = client.put(
        target + "/plan",
        json={
            "plan": plan,
            "author_note": "作者调整阶段方向",
            "expected_plan_sha256": original["sha256"],
        },
        headers=headers(str(uuid4())),
    )
    assert response.status_code == 200, response.text
    edited = response.json()
    assert original in edited["artifacts"] and current(edited, "plan")["payload"] == plan
    assert edited["snapshot"] == before["snapshot"] and edited["calls"] == before["calls"]
    assert control["calls"] == ["plan"]
    stale = post(
        client,
        target + "/input-authorize",
        {"input_limit": 68000, "preview_sha256": preview["preview_sha256"], "confirmed": True},
    )
    assert stale.status_code == 409 and control["calls"] == ["plan"]
    control["plan"] = plan
    control["pause_action"] = "write:1"
    fresh = read(client, target + "/input-preview?input_limit=68000")
    response = post(
        client,
        target + "/input-authorize",
        {"input_limit": 68000, "preview_sha256": fresh["preview_sha256"], "confirmed": True},
    )
    assert response.status_code == 200, response.text
    settle(client, base, before["id"])
    assert control["requests"]["write:1"]["effective_plan"] == plan


def test_rebudget_cannot_bypass_original_cost_cap(capacity_blocked, monkeypatch):
    client, control, base, before = capacity_blocked
    monkeypatch.setattr(input_recovery, "cost_for", lambda *args: Decimal(100))
    target = f"{base}/{before['id']}"
    preview = read(client, target + "/input-preview?input_limit=68000")
    assert any("超过原阶段预算" in b for b in preview["blockers"])
    rejected = post(
        client,
        target + "/input-authorize",
        {"input_limit": 68000, "preview_sha256": preview["preview_sha256"], "confirmed": True},
    )
    assert rejected.status_code == 409 and control["calls"] == ["plan"]
    assert read(client, target) == before


@pytest.mark.parametrize("status", ["local_failure", "outcome_uncertain", "executing"])
def test_recovery_never_replays_a_failed_or_uncertain_call(capacity_blocked, status):
    from uuid import UUID

    from novel_writer.db.models import GenerationCallRecord

    client, control, base, before = capacity_blocked

    async def change():
        async with client.app.state.database.session() as session, session.begin():
            call = await session.get(GenerationCallRecord, UUID(before["calls"][0]["id"]))
            call.status = status

    client.portal.call(change)
    target = f"{base}/{before['id']}"
    assert not read(client, target)["input_recovery_available"]
    assert client.get(target + "/input-preview", headers=headers()).status_code == 409
    assert control["calls"] == ["plan"]


def test_written_goal_and_unit_remain_immutable(longform):
    client, control = longform
    control["pause_action"] = "memory:1"
    base, draft = stage_create(client)
    paused = start(client, base, draft)
    original = current(paused, "plan")
    plan = deepcopy(original["payload"])
    plan["chapter_goal"] = "重写已经发生的方向"
    response = client.put(
        f"{base}/{paused['id']}/plan",
        json={
            "plan": plan,
            "author_note": "尝试改写已写目标",
            "expected_plan_sha256": original["sha256"],
        },
        headers=headers(str(uuid4())),
    )
    assert response.status_code == 400, response.text
    assert current(read(client, f"{base}/{paused['id']}"), "plan") == original


def test_paused_stage_upgrades_all_remaining_outputs_with_bound_budget(longform):
    client, control = longform
    store = client.app.state.provider_profile_store
    profile = store.get("fixture")
    profile.models[0].max_output_tokens = 100000
    profile.models[0].context_window = 300000
    profile.models[0].input_price_cny_per_million = Decimal(1)
    profile.models[0].output_price_cny_per_million = Decimal(9)
    store.save(profile)
    control["pause_action"] = "memory:1"
    base, draft = stage_create(client, max_cost_cny="3")
    before = start(client, base, draft)
    target = f"{base}/{before['id']}"
    assert before["input_recovery_available"] and before["next_action"] == "reader_early"
    preview = read(client, target + "/input-preview")
    assert preview["blockers"] and preview["output_limit"] == 100000
    higher = read(client, target + "/input-preview?max_cost_cny=20")
    assert not higher["blockers"] and higher["action"] == "reader_early"
    data = {"confirmed": True, "preview_sha256": higher["preview_sha256"], "max_cost_cny": "20"}
    assert (
        post(client, target + "/input-authorize", {**data, "output_limit": 24000}).status_code
        == 409
    )
    assert read(client, target) == before
    del control["pause_action"]
    response = post(client, target + "/input-authorize", data)
    assert response.status_code == 200, response.text
    done = settle(client, base, before["id"])
    assert current(done, "review")
    assert done["spec"] == before["spec"] and done["snapshot"] == before["snapshot"]
    original_ids = {c["id"] for c in before["calls"]}
    for call in done["calls"]:
        if call["id"] not in original_ids:
            request = read(client, target + f"/calls/{call['id']}")["request"]
            assert request["effective_input_limit"] == 200000
            assert request["model_request"]["max_output_tokens"] == 100000
    assert control["calls"].count("write:1") == control["calls"].count("memory:1") == 1
