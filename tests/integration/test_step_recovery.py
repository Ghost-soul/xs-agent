# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest

from novel_writer.db.models import GenerationBatchRecord, GenerationCallRecord
from novel_writer.generation import step_recovery
from novel_writer.generation.runtime import GenerationRuntime
from novel_writer.generation.service import GenerationService
from novel_writer.providers.base import ProviderResponseError, ProviderTerminalMetadata
from tests.integration.support import headers
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import continue_payload, settle
from tests.integration.test_generation_longform import longform, stage_create  # noqa: F401
from tests.integration.test_genre_generation import (  # noqa: F401
    REAL_DISPATCH,
    generation,
    post,
    read,
    start,
)
from tests.integration.test_novel_run_rebuild import current


@pytest.fixture
def recovery(longform, monkeypatch):
    client, control = longform
    previous = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        result = await previous(self, call_id, request, profile, spec, counting, api_key)
        action = control["calls"][-1]
        if action == control.get("retry_failure_action"):
            mode = control.get("failure_mode", "504")
            if mode == "timeout":
                raise TimeoutError("fixture disconnected")
            raise ProviderResponseError(
                "fixture gateway timeout" if mode == "504" else "fixture length",
                "<h1>504 Gateway Time-out</h1>" if mode == "504" else result.text,
                code="outcome_uncertain" if mode == "504" else "token_limit_exceeded",
                extracted_content=None if mode == "504" else result.text,
                usage=None if mode == "504" else result.usage,
                terminal=ProviderTerminalMetadata(
                    protocol="fixture",
                    terminal_event_seen=True,
                    terminal_status="http_504" if mode == "504" else None,
                    finish_reason=None if mode == "504" else "length",
                ),
            )
        return result

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    return client, control


def fail(recovery, action="write:1", mode="504", **changes):
    client, control = recovery
    control.update(retry_failure_action=action, failure_mode=mode)
    base, draft = stage_create(client, **changes)
    batch = start(client, base, draft)
    assert batch["step_recovery_available"], batch["state"]
    return client, control, base, f"{base}/{batch['id']}", batch


def authorize(client, target, preview, key=None, uncertain=True):
    return post(
        client,
        target + "/step-recovery-authorize",
        {
            "confirmed": True,
            "uncertain_confirmed": uncertain,
            "max_cost_cny": preview["max_cost_cny"],
            "preview_sha256": preview["preview_sha256"],
        },
        key,
    )


@pytest.mark.parametrize(
    "action", ["plan", "write:1", "memory:2", "chief:1", "reader_early", "checker", "reader"]
)
def test_resume_only_failed_action_preserves_history_and_completes(recovery, action):
    client, control, base, target, before = fail(recovery, action)
    original = {c["id"]: read(client, target + f"/calls/{c['id']}") for c in before["calls"]}
    preview = read(client, target + "/step-recovery-preview")
    assert read(client, target) == before
    assert preview["action"] == action and preview["additional_calls"] == 1
    assert preview["requires_uncertain_confirmation"] and not preview["blockers"]
    assert authorize(client, target, preview, uncertain=False).status_code == 409
    count = len(control["calls"])
    del control["retry_failure_action"]
    key = str(uuid4())
    result = authorize(client, target, preview, key)
    assert result.status_code == 200, result.text
    done = settle(client, base, before["id"])
    assert control["calls"][count] == action
    assert control["calls"].count(action) == 2
    assert all(
        control["calls"].count(c["action"]) == 1
        for c in before["calls"]
        if c["status"] == "completed"
    )
    assert current(done, "review"), done["state"]
    assert done["status"] == "needs_attention" and not done["step_recovery_available"]
    assert any(c["replaced_by_recovery"] for c in done["calls"])
    for k in ("spec", "snapshot", "preview_sha256"):
        assert done[k] == before[k]
    for call_id, prior in original.items():
        after = read(client, target + f"/calls/{call_id}")
        assert after["response"] == prior["response"]
        assert after["request"] == prior["request"]
    assert authorize(client, target, preview, key).status_code == 200
    assert authorize(client, target, preview).status_code == 409
    assert control["calls"].count(action) == 2


def test_partial_writer_replacement_keeps_complete_prefix_without_duplicate_text(recovery):
    client, control, base, target, before = fail(recovery, "write:2", "length")
    old_units = current(before, "units")["payload"]["items"]
    assert len(old_units) == 2 and old_units[-1]["complete"] is False
    partial = current(before, "candidate")
    preview = read(client, target + "/step-recovery-preview")
    assert preview["partial_response_characters"] > 0
    assert not preview["requires_uncertain_confirmation"]
    del control["retry_failure_action"]
    control["pause_action"] = "write:2"
    assert authorize(client, target, preview, uncertain=False).status_code == 200
    done = settle(client, base, before["id"])
    new_units = current(done, "units")["payload"]["items"]
    assert done["status"] == "paused" and len(new_units) == 2
    assert new_units[0] == old_units[0] and new_units[-1]["complete"]
    assert current(done, "candidate")["payload"]["body"] == partial["payload"]["body"]
    assert any(a["id"] == partial["id"] for a in done["artifacts"])
    del control["pause_action"]
    response = post(client, target + "/continue-stage", continue_payload(done))
    assert response.status_code == 200, response.text
    assert current(settle(client, base, before["id"]), "review")


@pytest.mark.parametrize("mode", ["504", "timeout"])
def test_failed_replay_stays_paused_and_needs_a_new_explicit_authorization(recovery, mode):
    client, control, base, target, before = fail(recovery, mode=mode)
    preview = read(client, target + "/step-recovery-preview")
    assert authorize(client, target, preview).status_code == 200
    failed = settle(client, base, before["id"])
    assert len(failed["calls"]) == len(before["calls"]) + 1
    assert failed["step_recovery_available"] and failed["status"] == "outcome_uncertain"
    next_preview = read(client, target + "/step-recovery-preview")
    assert len(next_preview["unknown_calls"]) == 2
    assert next_preview["preview_sha256"] != preview["preview_sha256"]
    assert authorize(client, target, preview).status_code == 409
    del control["retry_failure_action"]
    assert authorize(client, target, next_preview).status_code == 200
    done = settle(client, base, before["id"])
    assert current(done, "review"), done["state"]
    assert sum(c["replaced_by_recovery"] for c in done["calls"]) == 2


def test_pause_before_dispatch_resumes_existing_authorization(recovery, monkeypatch):
    client, control, base, target, before = fail(recovery)
    runtime = client.app.state.generation
    original_start = runtime.start
    monkeypatch.setattr(runtime, "start", lambda *_: None)
    preview = read(client, target + "/step-recovery-preview")
    assert authorize(client, target, preview).status_code == 200
    post(client, target + "/pause", {"confirmed": True})
    client.portal.call(runtime.run, UUID(before["id"]))
    paused = read(client, target)
    assert paused["status"] == "paused" and len(paused["calls"]) == len(before["calls"])
    monkeypatch.setattr(runtime, "start", original_start)
    del control["retry_failure_action"]
    done = start(client, base, paused)
    assert current(done, "review") and control["calls"].count("write:1") == 2


def test_stale_preview_budget_and_post_failure_edits_prevent_dispatch(recovery, monkeypatch):
    client, control, base, target, before = fail(recovery, "write:2")
    preview = read(client, target + "/step-recovery-preview")
    assert authorize(client, target, {**preview, "preview_sha256": "0" * 64}).status_code == 409
    with monkeypatch.context() as m:
        m.setattr(step_recovery, "cost_for", lambda *args: Decimal(100))
        expensive = read(client, target + "/step-recovery-preview")
        assert expensive["blockers"] and authorize(client, target, expensive).status_code == 409

    async def edit():
        async with client.app.state.database.session() as session, session.begin():
            b = await session.get(GenerationBatchRecord, UUID(before["id"]))
            s = GenerationService(session, client.app.state.provider_profile_store)
            await s.append(b, "plan_author_note", {"note": "作者新增说明"})

    client.portal.call(edit)
    assert client.get(target + "/step-recovery-preview", headers=headers()).status_code == 409
    assert len(control["calls"]) == len(before["calls"])


def test_unknown_cost_is_reserved_and_manual_recovery_cannot_lower_budget(recovery):
    client, control = recovery
    store = client.app.state.provider_profile_store
    profile = store.get("fixture")
    profile.models[0].input_price_cny_per_million = Decimal(1)
    profile.models[0].output_price_cny_per_million = Decimal(9)
    store.save(profile)
    client, control, base, target, before = fail(recovery, max_cost_cny="20")
    preview = read(client, target + "/step-recovery-preview")
    assert Decimal(preview["unknown_cost_reserve_cny"]) == Decimal("0.208")
    assert Decimal(preview["retry_cost_upper_cny"]) == Decimal("0.208")
    assert Decimal(preview["total_cost_upper_cny"]) == sum(
        Decimal(preview[k])
        for k in (
            "known_cost_cny",
            "unknown_cost_reserve_cny",
            "remaining_cost_upper_cny",
        )
    )
    assert (
        client.get(target + "/step-recovery-preview?max_cost_cny=1", headers=headers()).status_code
        == 409
    )
    assert read(client, target) == before


def test_concurrent_confirmations_create_only_one_pending_recovery(recovery, monkeypatch):
    client, control, base, target, before = fail(recovery)
    monkeypatch.setattr(client.app.state.generation, "start", lambda *_: None)
    preview = read(client, target + "/step-recovery-preview")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: authorize(client, target, preview).status_code, range(2)))
    assert sorted(results) == [200, 409]
    after = read(client, target)
    assert len([a for a in after["artifacts"] if a["kind"] == step_recovery.KIND]) == 1
    assert len(control["calls"]) == len(before["calls"])


@pytest.mark.parametrize("earlier_failure", [False, True])
def test_amendment_recovery_stays_within_its_own_steps(recovery, earlier_failure):
    from tests.integration.test_generation_amendment_limits import amendment, approve_amendment

    client, control = recovery
    if earlier_failure:
        control["incomplete_action"] = "memory:2"
    base, draft = stage_create(client)
    done = start(client, base, draft)
    control.pop("incomplete_action", None)
    target = f"{base}/{done['id']}"
    preview = amendment(client, target, done, input_limit=100000, output_limit=6000)
    assert preview.status_code == 200, preview.text
    control["retry_failure_action"] = "memory_amend"
    approve_amendment(client, target, preview.json()["preview_sha256"])
    failed = settle(client, base, done["id"])
    candidate = current(failed, "candidate")
    recovery_preview = read(client, target + "/step-recovery-preview")
    assert recovery_preview["remaining_slots"] == ["memory_amend", "checker_amend", "reader_amend"]
    count = len(control["calls"])
    del control["retry_failure_action"]
    assert authorize(client, target, recovery_preview).status_code == 200
    recovered = settle(client, base, done["id"])
    assert control["calls"][count:] == ["memory_amend", "checker_amend", "reader_amend"]
    assert current(recovered, "candidate") == candidate
    assert current(recovered, "review")


def test_replay_uses_identical_wire_body_with_mock_transport(longform, monkeypatch):
    client, control = longform
    previous = GenerationRuntime.dispatch
    sent = []

    def handle(request):
        sent.append(request.content)
        if len(sent) == 1:
            return httpx.Response(504, text="<h1>504 Gateway Time-out</h1>")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": control["writer_body"]}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 200, "completion_tokens": 100},
            },
        )

    original_init = httpx.AsyncClient.__init__

    def init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handle)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", init)

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        result = await previous(self, call_id, request, profile, spec, counting, api_key)
        if control["calls"][-1] == "write:1":
            control["writer_body"] = result.text
            return await REAL_DISPATCH(self, call_id, request, profile, spec, counting, api_key)
        return result

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    base, draft = stage_create(client)
    failed = start(client, base, draft)
    target = f"{base}/{failed['id']}"
    preview = read(client, target + "/step-recovery-preview")
    assert authorize(client, target, preview).status_code == 200
    done = settle(client, base, failed["id"])
    assert current(done, "review"), done["state"]
    assert len(sent) == 2 and sent[0] == sent[1]


def test_replay_report_parse_failure_pauses_without_launching_independent_reader(recovery):
    client, control, base, target, before = fail(recovery, "memory:2")
    preview = read(client, target + "/step-recovery-preview")
    count = len(control["calls"])
    del control["retry_failure_action"]
    control["fail_action"] = "memory:2"
    assert authorize(client, target, preview).status_code == 200
    failed = settle(client, base, before["id"])
    assert control["calls"][count:] == ["memory:2"]
    assert failed["status"] == "needs_attention" and failed["step_recovery_available"]
    assert read(client, target + "/step-recovery-preview")["action"] == "memory:2"


def test_legacy_504_without_checkpoint_is_readable_and_recoverable(recovery):
    client, control, base, target, before = fail(recovery)

    async def legacy():
        async with client.app.state.database.session() as session, session.begin():
            b = await session.get(GenerationBatchRecord, UUID(before["id"]))
            c = await session.get(GenerationCallRecord, UUID(before["calls"][-1]["id"]))
            c.request = {
                k: v for k, v in c.request.items() if not k.startswith("resume_checkpoint")
            }
            c.status, b.status = "local_failure", "needs_attention"

    client.portal.call(legacy)
    preview = read(client, target + "/step-recovery-preview")
    assert preview["requires_uncertain_confirmation"]
    del control["retry_failure_action"]
    assert authorize(client, target, preview).status_code == 200
    assert current(settle(client, base, before["id"]), "review")
