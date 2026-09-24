"""Offline single-user burst and recovery probes; no provider traffic.

Storage and ownership recovery never authorizes another provider request.
Run serially against a dedicated *_test database.
"""

# ruff: noqa: F811
import asyncio
import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from novel_writer.api.app import create_app
from novel_writer.core.config import Settings
from novel_writer.core.credentials import FileCredentialStore
from novel_writer.db.models import GenerationBatchRecord, GenerationCallRecord
from novel_writer.generation.runtime import GenerationRuntime
from tests.integration.support import headers
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated, settle  # noqa: F401
from tests.integration.test_generation_feedback import feedback_run  # noqa: F401
from tests.integration.test_generation_longform import longform, stage_create  # noqa: F401
from tests.integration.test_genre_generation import generation, post, read, start  # noqa: F401
from tests.integration.test_narrative_prompts import options
from tests.integration.test_novel_run_rebuild import current


def modern(**changes):
    return options(plan_policy="bounded-v1", length_policy="unit-v1", **changes)


def evidence(name, value):
    if root := os.environ.get("NOVEL_WRITER_STRESS_REPORT_DIR"):
        folder = Path(root)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def timings(samples):
    ordered = sorted(samples)
    return {
        "requests": len(samples),
        "p95_ms": round(ordered[math.ceil(len(samples) * 0.95) - 1], 2),
        "max_ms": round(max(samples), 2),
    }


def test_twelve_sequential_stages_auto_normalize_memory_and_keep_all_units(
    feedback_run, monkeypatch
):
    client, control = feedback_run
    previous = GenerationRuntime.dispatch

    async def annotated(self, call_id, request, profile, spec, counting, api_key):
        response = await previous(self, call_id, request, profile, spec, counting, api_key)
        if control["calls"][-1].startswith("memory:"):
            value = json.loads(response.text)
            value.update(base_version=110, evidence=[])
            response = response.model_copy(update={"text": json.dumps(value)})
        return response

    monkeypatch.setattr(GenerationRuntime, "dispatch", annotated)
    started = time.perf_counter()
    for _ in range(12):
        base, draft = stage_create(client, **modern())
        before = len(control["calls"])
        done = start(client, base, draft)
        assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
        assert len(control["calls"]) - before == 8
        units = current(done, "units")["payload"]["items"]
        assert len(units) == 3 and all(u["complete"] for u in units)
        assert current(done, "handoff")["payload"]["format_notes"]
        assert done["status"] not in {"queued", "running", "adopted"}
        assert read(client, base.replace("/generation-batches", "/chapters")) == []
    evidence(
        "sequential",
        {
            "stages": 12,
            "units": 36,
            "dispatches": len(control["calls"]),
            "seconds": round(time.perf_counter() - started, 2),
            "automatic_memory_format_normalization": True,
        },
    )


@pytest.mark.parametrize("same_key", [True, False])
def test_burst_authorize_is_single_dispatch_and_reads_remain_responsive(
    feedback_run,
    monkeypatch,
    same_key,
):
    client, control = feedback_run
    base, draft = stage_create(client, **modern())
    target = f"{base}/{draft['id']}"
    previous = GenerationRuntime.dispatch
    gate = client.portal.call(asyncio.Event)
    entered = threading.Event()

    async def hold(self, *args):
        response = await previous(self, *args)
        if control["calls"][-1] == "plan":
            entered.set()
            await gate.wait()
        return response

    monkeypatch.setattr(GenerationRuntime, "dispatch", hold)
    key = str(uuid4())
    payload = {"confirmed": True, "preview_sha256": draft["preview_sha256"]}
    assert post(client, target + "/authorize", payload, key).status_code == 200
    assert entered.wait(5)

    def authorize(_):
        begin = time.perf_counter()
        response = post(client, target + "/authorize", payload, key if same_key else str(uuid4()))
        return response.status_code, (time.perf_counter() - begin) * 1000

    try:
        with ThreadPoolExecutor(max_workers=10) as workers:
            results = list(workers.map(authorize, range(30)))
        assert all(code in ({200} if same_key else {200, 409}) for code, _ in results), results
        assert control["calls"] == ["plan"]

        def probe(i):
            begin = time.perf_counter()
            response = client.get(target if i % 2 else "/health/live", headers=headers())
            return response.status_code, (time.perf_counter() - begin) * 1000

        with ThreadPoolExecutor(max_workers=10) as workers:
            reads = list(workers.map(probe, range(120)))
        assert all(code == 200 for code, _ in reads)
        assert max(ms for _, ms in reads) < 5000
    finally:
        client.portal.call(gate.set)
    done = settle(client, base, draft["id"])
    assert len(control["calls"]) == 8 and all(c["status"] == "completed" for c in done["calls"])
    evidence(
        f"burst-{'same' if same_key else 'different'}-key",
        {
            "authorizations": timings([ms for _, ms in results]),
            "read_requests": timings([ms for _, ms in reads]),
            "provider_dispatches": 8,
            "authorization_http_statuses": [code for code, _ in results],
        },
    )


def test_burst_preview_same_key_freezes_one_batch(feedback_run):
    client, control = feedback_run
    base, draft = stage_create(client, **modern())
    key = str(uuid4())

    def preview(_):
        return post(client, base, draft["spec"], key)

    with ThreadPoolExecutor(max_workers=10) as workers:
        responses = list(workers.map(preview, range(20)))
    assert all(r.status_code == 200 for r in responses), [
        (r.status_code, r.text[:200]) for r in responses
    ]
    assert len({r.json()["id"] for r in responses}) == 1
    assert len({r.json()["preview_sha256"] for r in responses}) == 1
    assert not control["calls"]
    evidence("burst-preview", {"requests": 20, "frozen_previews": 1, "dispatches": 0})


def test_saved_response_survives_compile_crash_and_revalidates_without_dispatch(
    feedback_run, monkeypatch
):
    client, control = feedback_run
    base, draft = stage_create(client, **modern())
    original = GenerationRuntime.compile_response

    async def crash(self, batch_id, call_id):
        raise OperationalError("local compile test", {}, Exception("transient database failure"))

    with monkeypatch.context() as patch:
        patch.setattr(GenerationRuntime, "compile_response", crash)
        paused = start(client, base, draft)
    target = f"{base}/{draft['id']}"
    call = paused["calls"][0]
    assert call["status"] == "response_saved" and paused["status"] == "needs_attention"
    path = target + f"/calls/{call['id']}"
    before = read(client, path)
    assert before["response"]["text"]
    result = post(client, path + "/revalidate", {"confirmed": True})
    assert result.status_code == 200 and result.json()["provider_requests"] == "0", result.text
    after = read(client, target)
    assert after["calls"][0]["status"] == "completed"
    assert read(client, path)["response"] == before["response"]
    assert control["calls"] == ["plan"]
    assert GenerationRuntime.compile_response is original
    assert after["status"] not in {"queued", "running"}
    evidence(
        "compile-failure",
        {
            "response_preserved": True,
            "local_revalidation": True,
            "automatically_retried": False,
            "dispatches": 1,
        },
    )


def test_restart_with_claimed_call_marks_unknown_and_preserves_written_prefix(
    feedback_run, monkeypatch
):
    client, control = feedback_run
    base, draft = stage_create(client, **modern())
    original = GenerationRuntime.claim

    async def stop_after_claim(self, batch_id):
        prepared = await original(self, batch_id)
        if prepared and prepared[1] and control["calls"] == ["plan", "write:1", "memory:1"]:
            return None  # A process disappears after committing the next claim.
        return prepared

    with monkeypatch.context() as patch:
        patch.setattr(GenerationRuntime, "claim", stop_after_claim)
        before = start(client, base, draft)
    assert before["calls"][-1]["status"] == "executing"
    runtime = client.app.state.generation

    async def restart():
        await runtime.close()
        replacement = GenerationRuntime(
            runtime.database,
            runtime.profiles,
            runtime.credentials,
            response_root=runtime.journal.root.parent,
        )
        await replacement.initialize()
        client.app.state.generation = replacement

    client.portal.call(restart)
    after = read(client, f"{base}/{draft['id']}")
    assert after["status"] == "outcome_uncertain" and after["step_recovery_available"]
    assert after["calls"][-1]["error_code"] == "process_interrupted"
    assert current(after, "candidate") == current(before, "candidate")
    assert control["calls"] == ["plan", "write:1", "memory:1"]
    evidence(
        "restart",
        {
            "status": after["status"],
            "written_prefix_preserved": True,
            "automatic_dispatches_after_restart": 0,
            "manual_recovery_available": True,
        },
    )


@pytest.mark.parametrize("acknowledgement_lost", [False, True])
def test_transient_response_write_retries_without_repeating_model(
    feedback_run, monkeypatch, acknowledgement_lost
):
    client, control = feedback_run
    base, draft = stage_create(client, **modern())
    original = GenerationRuntime.persist_response
    failed = []

    async def fail_once(self, entry, **kwargs):
        if control["calls"][-1] == "memory:1" and not failed:
            failed.append(entry)
            if acknowledgement_lost:
                await original(self, entry, **kwargs)
            raise OperationalError("response write", {}, Exception("temporary database failure"))
        return await original(self, entry, **kwargs)

    monkeypatch.setattr(GenerationRuntime, "persist_response", fail_once)
    done = start(client, base, draft)
    assert failed and len(control["calls"]) == 8
    assert all(c["status"] == "completed" for c in done["calls"])
    assert done["state"]["units_finished"]
    assert not client.app.state.generation.journal.pending()
    target = f"{base}/{draft['id']}/calls/{failed[0]['call_id']}"
    assert read(client, target)["response"] == failed[0]["response"]


@pytest.mark.parametrize("restart", [False, True])
def test_persistent_response_failure_is_saved_then_locally_recovered(feedback_run, restart):
    client, control = feedback_run
    base, draft = stage_create(client, **modern())
    failures = []

    def unavailable(session):
        for item in session.dirty:
            if (
                isinstance(item, GenerationCallRecord)
                and item.action == "memory:1"
                and item.status == "response_saved"
            ):
                failures.append(item.response)
                raise OperationalError("response commit", {}, Exception("database unavailable"))

    event.listen(Session, "before_commit", unavailable)
    try:
        before = start(client, base, draft)
    finally:
        event.remove(Session, "before_commit", unavailable)
    assert len(failures) == 3 and before["status"] == "needs_attention"
    runtime = client.app.state.generation
    assert len(runtime.journal.pending()) == 1
    saved = runtime.journal.read(runtime.journal.pending()[0])
    assert saved["response"] == failures[0]

    async def recover():
        if restart:
            await runtime.close()
            replacement = GenerationRuntime(
                runtime.database,
                runtime.profiles,
                runtime.credentials,
                response_root=runtime.journal.root.parent,
            )
            client.app.state.generation = replacement
            await replacement.initialize()
        else:
            await runtime.maintain_once()

    client.portal.call(recover)
    after = read(client, f"{base}/{draft['id']}")
    assert after["status"] == "needs_attention" and after["calls"][-1]["status"] == "response_saved"
    assert current(after, "candidate") == current(before, "candidate")
    assert len(control["calls"]) == 3
    path = f"{base}/{draft['id']}/calls/{saved['call_id']}"
    response = read(client, path)
    assert response["response"] == saved["response"]
    assert after["calls"][-1]["actual_cost_cny"] is not None
    assert not client.app.state.generation.journal.pending()
    assert post(client, path + "/revalidate", {"confirmed": True}).status_code == 200
    assert control["calls"] == ["plan", "write:1", "memory:1"]
    assert read(client, f"{base}/{draft['id']}")["status"] not in {"running", "queued"}


def test_corrupt_buffer_cannot_override_saved_call(feedback_run, monkeypatch):
    client, control = feedback_run
    base, draft = stage_create(client, **modern())

    async def unavailable(self, *args, **kwargs):
        raise OperationalError("response commit", {}, Exception("temporary failure"))

    with monkeypatch.context() as patch:
        patch.setattr(GenerationRuntime, "persist_response", unavailable)
        before = start(client, base, draft)
    runtime = client.app.state.generation
    path = runtime.journal.pending()[0]
    value = json.loads(path.read_text(encoding="utf-8"))
    value["response"]["text"] = "tampered response"
    path.write_text(json.dumps(value), encoding="utf-8")
    client.portal.call(runtime.maintain_once)
    assert read(client, f"{base}/{draft['id']}") == before
    assert path.exists() and len(control["calls"]) == 1


def test_buffer_recovery_preserves_later_author_edit(feedback_run, monkeypatch):
    from novel_writer.generation.content import digest

    client, control = feedback_run
    base, draft = stage_create(client, **modern())
    original = GenerationRuntime.persist_response

    async def unavailable(self, entry, **kwargs):
        if control["calls"][-1] == "memory:1":
            raise OperationalError("response commit", {}, Exception("temporary failure"))
        return await original(self, entry, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(GenerationRuntime, "persist_response", unavailable)
        before = start(client, base, draft)
    target = f"{base}/{draft['id']}"
    body = current(before, "candidate")["payload"]["body"]
    edited = client.put(
        target + "/candidate",
        json={
            "body": body + "\n\n作者后来补写的内容。",
            "expected_body_sha256": digest(body),
        },
        headers=headers(str(uuid4())),
    )
    assert edited.status_code == 200, edited.text
    candidate = current(read(client, target), "candidate")
    client.portal.call(client.app.state.generation.maintain_once)
    after = read(client, target)
    assert current(after, "candidate") == candidate
    call = after["calls"][-1]
    assert call["status"] == "response_saved" and not call["can_revalidate"]
    assert (
        post(client, target + f"/calls/{call['id']}/revalidate", {"confirmed": True}).status_code
        == 409
    )
    assert len(control["calls"]) == 3


def test_owner_lock_recovers_automatically_without_dispatch(feedback_run):
    client, control = feedback_run
    runtime = client.app.state.generation
    base, draft = stage_create(client, **modern())
    Path(client.app.state.content_store_root).mkdir(parents=True, exist_ok=True)

    async def lose_lock():
        await runtime.owner.execute(text("SELECT pg_advisory_unlock(724918320)"))
        await runtime.owner.commit()

    client.portal.call(lose_lock)
    assert client.get("/health/ready", headers=headers()).status_code == 503
    assert read(client, f"{base}/{draft['id']}")["status"] == draft["status"]
    assert (
        post(
            client,
            f"{base}/{draft['id']}/authorize",
            {
                "confirmed": True,
                "preview_sha256": draft["preview_sha256"],
            },
        ).status_code
        == 400
    )

    async def wait_for_recovery():
        async with asyncio.timeout(7):
            while not runtime.available:
                await asyncio.sleep(0.02)

    client.portal.call(wait_for_recovery)
    assert client.get("/health/ready", headers=headers()).status_code == 200
    assert not control["calls"]
    done = start(client, base, draft)
    assert all(c["status"] == "completed" for c in done["calls"])


def test_lost_database_connection_turns_readiness_red_then_recovers(feedback_run):
    client, control = feedback_run
    runtime = client.app.state.generation
    Path(client.app.state.content_store_root).mkdir(parents=True, exist_ok=True)
    base, draft = stage_create(client, **modern())

    async def terminate_test_owner():
        assert runtime.database.engine.url.database.endswith("_test")
        owner_pid = await runtime.owner.scalar(text("SELECT pg_backend_pid()"))
        await runtime.owner.commit()
        async with runtime.database.session() as session:
            name = await session.scalar(
                text("SELECT datname FROM pg_stat_activity WHERE pid=:pid"), {"pid": owner_pid}
            )
            assert name == runtime.database.engine.url.database and name.endswith("_test")
            assert await session.scalar(
                text("SELECT pg_terminate_backend(:pid)"), {"pid": owner_pid}
            )

    client.portal.call(terminate_test_owner)
    assert client.get("/health/ready", headers=headers()).status_code == 503
    assert not runtime.available and runtime.owner is None
    assert read(client, f"{base}/{draft['id']}")["status"] == draft["status"]
    client.portal.call(runtime.maintain_once)
    assert client.get("/health/ready", headers=headers()).status_code == 200
    assert not control["calls"]


def test_second_worker_cannot_take_live_lock_and_takes_over_idle_after_close(feedback_run):
    client, control = feedback_run
    first = client.app.state.generation

    async def compete():
        second = GenerationRuntime(
            first.database,
            first.profiles,
            first.credentials,
            response_root=first.journal.root.parent,
        )
        await second.initialize()
        assert not second.available and await first.check_owner()
        await first.close()
        await second.maintain_once()
        assert await second.check_owner()
        client.app.state.generation = second

    client.portal.call(compete)
    assert not control["calls"]


def test_startup_database_failure_recovers_in_background(feedback_run, monkeypatch):
    client, control = feedback_run
    original = client.app.state.generation

    async def unavailable(*args, **kwargs):
        raise OSError("startup database unavailable")

    async def restart_unavailable():
        await original.close()
        replacement = GenerationRuntime(
            original.database,
            original.profiles,
            original.credentials,
            response_root=original.journal.root.parent,
            recovery_poll_seconds=0.05,
        )
        client.app.state.generation = replacement
        with monkeypatch.context() as patch:
            patch.setattr(type(original.database.engine), "connect", unavailable)
            await replacement.initialize()
        assert not replacement.available
        async with asyncio.timeout(2):
            while not replacement.available:
                await asyncio.sleep(0.01)
        assert await replacement.check_owner()

    client.portal.call(restart_unavailable)
    assert not control["calls"]


def test_lock_loss_during_provider_call_preserves_response_without_restarting_it(
    feedback_run, monkeypatch
):
    client, control = feedback_run
    runtime = client.app.state.generation
    base, draft = stage_create(client, **modern())
    previous = GenerationRuntime.dispatch

    async def lose_while_receiving(self, *args):
        result = await previous(self, *args)
        if control["calls"][-1] == "memory:1":
            await self.owner.execute(text("SELECT pg_advisory_unlock(724918320)"))
            await self.owner.commit()
            assert not await self.check_owner()
            await self.maintain_once()
            assert not self.available  # Never reconcile our own in-flight call.
        return result

    monkeypatch.setattr(GenerationRuntime, "dispatch", lose_while_receiving)
    done = start(client, base, draft)
    assert done["calls"][-1]["status"] == "response_saved"
    assert done["status"] == "needs_attention"
    assert len(control["calls"]) == 3
    client.portal.call(runtime.maintain_once)
    assert runtime.available and len(control["calls"]) == 3
    assert current(read(client, f"{base}/{draft['id']}"), "candidate") == current(done, "candidate")


def test_progress_write_failure_does_not_abort_complete_transport(generation, monkeypatch):
    import httpx

    from tests.integration.test_genre_generation import (
        test_actual_protocol_mapping_budget_and_receipts,
    )

    failures = []
    original_transport = httpx.MockTransport

    class Chunked(httpx.AsyncByteStream):
        def __init__(self, value):
            self.value = value

        async def __aiter__(self):
            yield self.value[:20]
            yield self.value[20:]

    def unbuffered(handler):
        async def respond(request):
            result = handler(request)
            return httpx.Response(
                result.status_code, headers=result.headers, stream=Chunked(result.content)
            )

        return original_transport(respond)

    # A preloaded Response(json=...) bypasses aiter_bytes and would never
    # exercise the progress callback. Return actual unread transport chunks.
    monkeypatch.setattr(httpx, "MockTransport", unbuffered)

    def break_progress(session):
        for item in session.dirty:
            if (
                isinstance(item, GenerationBatchRecord)
                and item.state.get("transport")
                and not failures
            ):
                failures.append(item.id)
                raise OperationalError(
                    "progress write", {}, Exception("temporary database failure")
                )

    event.listen(Session, "before_commit", break_progress)
    try:
        test_actual_protocol_mapping_budget_and_receipts(
            generation, monkeypatch, "openai_chat_completions"
        )
    finally:
        event.remove(Session, "before_commit", break_progress)
    assert failures


def test_file_credentials_survive_application_recreation(tmp_path):
    settings = Settings(
        provider_profiles_path=tmp_path / "profiles.json",
        log_dir=tmp_path / "logs",
        credential_backend="file",
        _env_file=None,
    )
    first = create_app(settings)
    assert isinstance(first.state.credential_store, FileCredentialStore)
    first.state.credential_store.set_api_key("fixture", "synthetic-test-key")
    second = create_app(settings)
    assert second.state.credential_store.get_api_key("fixture") == "synthetic-test-key"
    second.state.credential_store.delete_api_key("fixture")
    assert first.state.credential_store.get_api_key("fixture") is None
