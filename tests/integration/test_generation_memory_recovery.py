# ruff: noqa: F811
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from novel_writer.db.models import GenerationCallRecord
from novel_writer.generation import memory_recovery
from novel_writer.generation.runtime import GenerationRuntime
from tests.integration.support import headers
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import continue_payload, settle
from tests.integration.test_generation_input_recovery import capacity_blocked  # noqa: F401
from tests.integration.test_generation_longform import longform, stage_create  # noqa: F401
from tests.integration.test_genre_generation import generation, post, read, start  # noqa: F401
from tests.integration.test_novel_run_rebuild import current


@pytest.fixture
def truncated(longform):
    client, control = longform
    control["incomplete_action"] = "memory:2"
    base, draft = stage_create(client, auxiliary_output_limit=6000)
    batch = start(client, base, draft)
    assert batch["status"] == "needs_attention", batch["state"]
    assert control["calls"][-1] == "memory:2"
    assert batch["memory_recovery_available"]
    return client, control, base, batch


def authorize(client, target, preview, key=None):
    return post(
        client,
        target + "/memory-recovery-authorize",
        {
            "confirmed": True,
            "output_limit": preview["output_limit"],
            "all_roles": preview["all_roles"],
            "max_cost_cny": preview["max_cost_cny"],
            "preview_sha256": preview["preview_sha256"],
        },
        key,
    )


def test_preview_then_one_memory_replacement_preserves_writer_and_original_evidence(truncated):
    client, control, base, before = truncated
    target = f"{base}/{before['id']}"
    original_calls = {c["id"]: read(client, target + f"/calls/{c['id']}") for c in before["calls"]}
    preview = read(client, target + "/memory-recovery-preview?output_limit=24000&all_roles=false")
    assert (preview["previous_output_limit"], preview["output_limit"]) == (6000, 24000)
    assert preview["additional_calls"] == 1 and not preview["blockers"]
    assert preview["input_limit"] == 100000 and preview["action"] == "memory:2"
    assert read(client, target) == before
    count = len(control["calls"])
    del control["incomplete_action"]
    control["pause_action"] = "memory:2"
    key = str(uuid4())
    result = authorize(client, target, preview, key)
    assert result.status_code == 200, result.text
    paused = settle(client, base, before["id"])
    assert control["calls"][count:] == ["memory:2"], paused["state"]
    assert paused["status"] == "paused" and paused["next_action"] == "write:3"
    for kind in ("plan", "candidate"):
        assert current(paused, kind) == current(before, kind)
    for field in ("spec", "snapshot", "preview_sha256"):
        assert paused[field] == before[field]
    for call_id, saved in original_calls.items():
        assert read(client, target + f"/calls/{call_id}") == saved
    assert any(c["replaced_by_recovery"] for c in paused["calls"])
    assert authorize(client, target, preview, key).status_code == 200
    assert authorize(client, target, preview).status_code == 409
    assert len(control["calls"]) == count + 1
    del control["pause_action"]
    response = post(client, target + "/continue-stage", continue_payload(paused))
    assert response.status_code == 200, response.text
    done = settle(client, base, before["id"])
    assert control["calls"][count:] == ["memory:2", "write:3", "memory:3", "checker", "reader"]
    assert done["status"] == "needs_attention" and done["next_action"] is None
    assert current(done, "review")
    for call in done["calls"]:
        request = read(client, target + f"/calls/{call['id']}")["request"]
        if call["id"] not in original_calls and str(call["action"]).startswith("memory:"):
            assert request["model_request"]["max_output_tokens"] == 24000
            assert request["effective_input_limit"] == 100000
            assert (
                request["memory_output_authorization_id"]
                == done["state"]["memory_output_authorization_id"]
            )
    assert not done["memory_recovery_available"]


def test_truncated_response_cannot_be_locally_revalidated(truncated):
    client, control, base, batch = truncated
    failed = next(c for c in batch["calls"] if c["status"] == "local_failure")
    assert not failed["can_revalidate"] and "截断" in failed["revalidation_blocker"]
    count = len(control["calls"])
    response = post(
        client, f"{base}/{batch['id']}/calls/{failed['id']}/revalidate", {"confirmed": True}
    )
    assert response.status_code == 409 and "截断" in response.text
    assert len(control["calls"]) == count


def test_pause_before_replacement_dispatch_resumes_the_same_authorized_slot(truncated, monkeypatch):
    client, control, base, batch = truncated
    target = f"{base}/{batch['id']}"
    runtime = client.app.state.generation
    start_runtime = runtime.start
    monkeypatch.setattr(runtime, "start", lambda *_: None)
    preview = read(client, target + "/memory-recovery-preview?output_limit=24000&all_roles=false")
    assert authorize(client, target, preview).status_code == 200
    assert post(client, target + "/pause", {"confirmed": True}).status_code == 200
    client.portal.call(runtime.run, UUID(batch["id"]))
    paused = read(client, target)
    assert paused["status"] == "paused" and paused["next_action"] == "memory:2"
    assert len(paused["calls"]) == len(batch["calls"])
    monkeypatch.setattr(runtime, "start", start_runtime)
    del control["incomplete_action"]
    result = post(
        client,
        target + "/authorize",
        {"confirmed": True, "preview_sha256": batch["preview_sha256"]},
    )
    assert result.status_code == 200, result.text
    done = settle(client, base, batch["id"])
    assert control["calls"].count("memory:2") == 2
    assert current(done, "review")


def test_stale_preview_and_original_budget_are_enforced(truncated, monkeypatch):
    client, control, base, batch = truncated
    target = f"{base}/{batch['id']}"
    preview = read(client, target + "/memory-recovery-preview?output_limit=24000&all_roles=false")
    assert authorize(client, target, {**preview, "preview_sha256": "0" * 64}).status_code == 409
    monkeypatch.setattr(memory_recovery, "cost_for", lambda *args: Decimal(100))
    over = read(client, target + "/memory-recovery-preview?output_limit=24000&all_roles=false")
    assert over["blockers"] and authorize(client, target, over).status_code == 409
    assert read(client, target) == batch
    assert control["calls"].count("memory:2") == 1


@pytest.mark.parametrize("failure", ["length", "unknown", "parse"])
def test_replacement_failure_never_loops(truncated, monkeypatch, failure):
    client, control, base, batch = truncated
    target = f"{base}/{batch['id']}"
    preview = read(client, target + "/memory-recovery-preview?output_limit=24000&all_roles=false")
    if failure == "unknown":

        async def timeout(*args):
            raise TimeoutError("fixture disconnected")

        monkeypatch.setattr(GenerationRuntime, "dispatch", timeout)
    elif failure == "parse":
        del control["incomplete_action"]
        control["fail_action"] = "memory:2"
    response = authorize(client, target, preview)
    assert response.status_code == 200, response.text
    done = settle(client, base, batch["id"])
    assert not done["memory_recovery_available"]
    assert len(done["calls"]) == len(batch["calls"]) + 1
    assert current(done, "candidate") == current(batch, "candidate")
    assert authorize(client, target, preview).status_code == 409
    assert (
        client.get(
            target + "/memory-recovery-preview?output_limit=24000&all_roles=false",
            headers=headers(),
        ).status_code
        == 409
    )


@pytest.mark.parametrize("change", ["source", "response", "unknown"])
def test_changed_source_or_response_and_unknown_results_block_recovery(truncated, change):
    client, control, base, batch = truncated
    target = f"{base}/{batch['id']}"
    failed = next(c for c in batch["calls"] if c["status"] == "local_failure")

    async def mutate_test_call():
        async with client.app.state.database.session() as session, session.begin():
            call = await session.get(GenerationCallRecord, UUID(failed["id"]))
            if change == "source":
                call.request = {**call.request, "candidate_sha256": "changed"}
            elif change == "response":
                call.response = {**call.response, "text": "changed"}
            else:
                call.status = "outcome_uncertain"

    client.portal.call(mutate_test_call)
    assert (
        client.get(
            target + "/memory-recovery-preview?output_limit=24000&all_roles=false",
            headers=headers(),
        ).status_code
        == 409
    )
    assert control["calls"].count("memory:2") == 1


def test_memory_recovery_inherits_the_existing_100k_input_authorization(capacity_blocked):
    client, control, base, batch = capacity_blocked
    target = f"{base}/{batch['id']}"
    control["incomplete_action"] = "memory:2"
    preview = read(client, target + "/input-preview?all_roles=false&input_limit=100000")
    result = post(
        client,
        target + "/input-authorize",
        {
            "confirmed": True,
            "input_limit": 100000,
            "all_roles": False,
            "preview_sha256": preview["preview_sha256"],
        },
    )
    assert result.status_code == 200, result.text
    stopped = settle(client, base, batch["id"])
    assert stopped["spec"]["input_limit"] == 58000
    assert (
        read(client, target + "/memory-recovery-preview?output_limit=24000&all_roles=false")[
            "input_limit"
        ]
        == 100000
    )
    del control["incomplete_action"]
    preview = read(client, target + "/memory-recovery-preview?output_limit=24000&all_roles=false")
    assert authorize(client, target, preview).status_code == 200
    done = settle(client, base, batch["id"])
    assert done["status"] == "needs_attention" and done["next_action"] is None
    assert current(done, "review")
    assert done["state"]["input_authorization_id"] == stopped["state"]["input_authorization_id"]


def test_all_role_100k_recovery_requires_bound_new_budget_and_preserves_history(longform):
    client, control = longform
    store = client.app.state.provider_profile_store
    profile = store.get("fixture")
    profile.models[0].max_output_tokens = 100000
    profile.models[0].context_window = 300000
    profile.models[0].input_price_cny_per_million = Decimal(1)
    profile.models[0].output_price_cny_per_million = Decimal(9)
    store.save(profile)
    control["incomplete_action"] = "memory:2"
    base, draft = stage_create(client, max_cost_cny="3")
    before = start(client, base, draft)
    assert before["memory_recovery_available"], before["state"]
    target = f"{base}/{before['id']}"
    preview = read(client, target + "/memory-recovery-preview")
    assert preview["input_limit"] == 200000 and preview["output_limit"] == 100000
    assert preview["all_roles"] is True
    assert preview["previous_input_limit"] == 100000
    assert Decimal(preview["total_cost_upper_cny"]) > 3
    assert preview["blockers"] and authorize(client, target, preview).status_code == 409
    assert read(client, target) == before
    higher = read(client, target + "/memory-recovery-preview?max_cost_cny=20")
    assert not higher["blockers"]
    assert Decimal(higher["previous_max_cost_cny"]) == 3
    assert authorize(client, target, {**higher, "max_cost_cny": "3"}).status_code == 409
    original_calls = {c["id"]: read(client, target + f"/calls/{c['id']}") for c in before["calls"]}
    del control["incomplete_action"]
    response = authorize(client, target, higher)
    assert response.status_code == 200, response.text
    done = settle(client, base, before["id"])
    assert current(done, "review")
    assert done["spec"] == before["spec"] and done["snapshot"] == before["snapshot"]
    for call in done["calls"]:
        detail = read(client, target + f"/calls/{call['id']}")
        if call["id"] in original_calls:
            assert detail == original_calls[call["id"]]
        else:
            assert detail["request"]["effective_input_limit"] == 200000
            assert detail["request"]["model_request"]["max_output_tokens"] == 100000


def test_new_api_omitted_limits_and_independent_roles_default_to_100k(longform):
    client, _ = longform
    store = client.app.state.provider_profile_store
    profile = store.get("fixture")
    profile.models[0].max_output_tokens = 100000
    profile.models[0].context_window = 300000
    store.save(profile)
    base, draft = stage_create(client)
    payload = {**draft["spec"], "feedback_policy": "logic-v1"}
    for field in (
        "input_limit",
        "chief_output_limit",
        "writer_output_limit",
        "auxiliary_output_limit",
    ):
        del payload[field]
    payload["roles"] = {
        role: {"model": "fixture-model"} for role in ("memory", "checker", "reader", "editor")
    }
    response = post(client, base, payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert not result["snapshot"]["blockers"]
    for field in (
        "input_limit",
        "chief_output_limit",
        "writer_output_limit",
        "auxiliary_output_limit",
    ):
        assert result["spec"][field] == (200000 if field == "input_limit" else 100000)
    assert all(role["output_limit"] == 100000 for role in result["spec"]["roles"].values())
    assert result["calls"] == []
