"""Maintenance and cross-address recovery; dedicated *_test DB, fake providers only."""

# ruff: noqa: F811
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from novel_writer.core.maintenance import ACTIVE_WORK_QUERY, offline_database
from novel_writer.db.models import GenerationBatchRecord, GenerationCallRecord, LocalTaskRecord
from novel_writer.generation.content import fingerprint
from novel_writer.generation.response_journal import ResponseJournal
from novel_writer.generation.response_migration import apply_migration, preview_migration
from novel_writer.generation.runtime import GenerationRuntime
from novel_writer.services.errors import WorkflowError
from tests.integration.support import DATABASE_URL
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated  # noqa: F401
from tests.integration.test_generation_feedback import feedback_run  # noqa: F401
from tests.integration.test_generation_longform import longform, stage_create  # noqa: F401
from tests.integration.test_generation_resilience_stress import modern
from tests.integration.test_genre_generation import generation, post, read, start  # noqa: F401
from tests.integration.test_novel_run_rebuild import current


@pytest.mark.parametrize("timeout", [False, True])
def test_shutdown_finishes_current_response_or_marks_unknown_without_next_call(
    feedback_run, monkeypatch, timeout,
):
    client, control = feedback_run
    base, draft = stage_create(client, **modern())
    original = GenerationRuntime.dispatch
    gate = client.portal.call(asyncio.Event)
    entered = threading.Event()

    async def hold(self, *args, **kwargs):
        if len(control["calls"]) == 3:  # second Writer, after one durable unit + Memory
            entered.set()
            await gate.wait()
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(GenerationRuntime, "dispatch", hold)
    assert post(client, f"{base}/{draft['id']}/authorize", {
        "preview_sha256": draft["preview_sha256"], "confirmed": True,
    }).status_code == 200
    assert entered.wait(10)
    before = read(client, f"{base}/{draft['id']}")
    runtime = client.app.state.generation

    async def stop():
        await runtime.close(grace_seconds=0.01 if timeout else 5)

    future = client.portal.start_task_soon(stop)

    async def wait_drain():
        async with asyncio.timeout(5):
            while not runtime.draining:
                await asyncio.sleep(0.01)

    client.portal.call(wait_drain)
    assert client.get("/health/ready").status_code == 503
    rejected = post(client, f"{base}/{draft['id']}/authorize", {
        "preview_sha256": draft["preview_sha256"], "confirmed": True,
    })
    assert rejected.status_code != 200
    if not timeout:
        assert not future.done()
        client.portal.call(gate.set)
    future.result(timeout=15)
    after = read(client, f"{base}/{draft['id']}")
    assert after["status"] not in {"running", "queued"}
    assert current(after, "handoff") == current(before, "handoff")
    if timeout:
        assert after["calls"][-1]["status"] == "outcome_uncertain"
        assert after["calls"][-1]["actual_cost_cny"] is None
        assert current(after, "candidate") == current(before, "candidate")
        assert len(control["calls"]) == 3
    else:
        assert after["calls"][-1]["status"] == "completed"
        assert len(current(after, "units")["payload"]["items"]) == 2
        assert len(control["calls"]) == 4  # no Memory 2 / Writer 3
        saved = read(client, f"{base}/{draft['id']}/calls/{after['calls'][-1]['id']}")
        assert saved["response"] is not None
        assert after["calls"][-1]["actual_cost_cny"] is not None


def test_shutdown_after_claim_never_dispatches_new_request(feedback_run, monkeypatch):
    client, control = feedback_run
    base, draft = stage_create(client, **modern())
    original = GenerationRuntime.claim

    async def claimed(self, batch_id):
        prepared = await original(self, batch_id)
        self.draining = True
        return prepared

    monkeypatch.setattr(GenerationRuntime, "claim", claimed)
    after = start(client, base, draft)
    assert control["calls"] == []
    assert after["calls"][0]["status"] == "not_dispatched"


def test_killed_process_is_reconciled_without_repeating_call(feedback_run, tmp_path):
    client, control = feedback_run
    base, draft = stage_create(client, **modern())
    original = client.app.state.generation
    client.portal.call(original.close)
    marker = tmp_path / "child-entered"
    script = tmp_path / "child.py"
    script.write_text('''
import asyncio, os, sys, time
from pathlib import Path
from fastapi.testclient import TestClient
from novel_writer.api.app import create_app
from novel_writer.core.config import Settings
from novel_writer.core.credentials import MemoryCredentialStore
from novel_writer.generation.runtime import GenerationRuntime
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
async def held(*args, **kwargs):
    Path(sys.argv[1]).write_text("inflight")
    await asyncio.Event().wait()
GenerationRuntime.dispatch = held
app = create_app(Settings(
    database_url=os.environ["NOVEL_WRITER_TEST_DATABASE_URL"],
    local_token="integration-token", local_task_worker_enabled=False,
    content_store_root=Path(sys.argv[1]).parent/"child"/"content",
    provider_profiles_path=Path(sys.argv[2]),
    log_dir=Path(sys.argv[1]).parent/"child"/"logs", _env_file=None,
))
app.state.credential_store=MemoryCredentialStore({})
with TestClient(app) as client:
    response=client.post(sys.argv[3], json={"confirmed":True,"preview_sha256":sys.argv[4]},
        headers={"Authorization":"Bearer integration-token","Origin":"http://127.0.0.1:5173",
        "X-CSRF-Token":"integration-token","Idempotency-Key":"killed-test"})
    assert response.status_code==200
    time.sleep(30)
''', encoding="utf-8")
    with (tmp_path / "child.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen([
            sys.executable, "-B", str(script), str(marker), str(original.profiles.path),
            f"{base}/{draft['id']}/authorize", draft["preview_sha256"],
        ], stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert marker.exists()
            before = read(client, f"{base}/{draft['id']}")
            assert before["calls"][0]["status"] == "executing"
        finally:
            child.kill()
            child.wait(timeout=5)

    async def restart():
        replacement = GenerationRuntime(
            original.database, original.profiles, original.credentials,
            response_root=original.journal.root.parent,
        )
        client.app.state.generation = replacement
        await replacement.initialize()

    client.portal.call(restart)
    after = read(client, f"{base}/{draft['id']}")
    assert after["status"] == "outcome_uncertain"
    assert after["calls"][0]["error_code"] == "process_interrupted"
    assert after["calls"][0]["request_sha256"] == before["calls"][0]["request_sha256"]
    assert after["calls"][0]["actual_cost_cny"] is None
    assert control["calls"] == []


def test_backup_rejects_live_owner_then_blocks_concurrent_writes(feedback_run):
    client, _ = feedback_run
    base, draft = stage_create(client, **modern())
    with pytest.raises(RuntimeError, match="执行器在线"), offline_database(DATABASE_URL):
        pytest.fail("live owner must be rejected even when idle")
    client.portal.call(client.app.state.generation.close)
    engine = create_engine(DATABASE_URL)
    try:
        with offline_database(DATABASE_URL) as snapshot:
            assert snapshot.scalar(text(ACTIVE_WORK_QUERY)) == 0
            with engine.connect() as writer, writer.begin():
                writer.execute(text("SET LOCAL lock_timeout = '100ms'"))
                with pytest.raises(DBAPIError):
                    writer.execute(text(
                        "UPDATE generation_batches SET status='queued' WHERE id=:id"
                    ), {"id": draft["id"]})
        with engine.connect() as connection:
            assert connection.scalar(text(
                "SELECT status FROM generation_batches WHERE id=:id"
            ), {"id": draft["id"]}) == draft["status"]
    finally:
        engine.dispose()


@pytest.mark.parametrize("active", ["batch", "local_task", "call"])
def test_backup_rejects_stale_active_work_even_without_owner(feedback_run, active):
    client, _ = feedback_run
    base, draft = stage_create(client, **modern())
    done = start(client, base, draft)
    client.portal.call(client.app.state.generation.close)
    engine = create_engine(DATABASE_URL)
    try:
        with Session(engine) as session, session.begin():
            if active == "batch":
                session.get(GenerationBatchRecord, UUID(draft["id"])).status = "queued"
            elif active == "call":
                call = session.get(GenerationCallRecord, UUID(done["calls"][-1]["id"]))
                call.status = "executing"
            else:
                session.add(LocalTaskRecord(kind="backup", status="queued", input_sha256="a" * 64))
        with pytest.raises(RuntimeError, match="拒绝备份"), offline_database(DATABASE_URL):
            pytest.fail("active state must block archive")
    finally:
        engine.dispose()


def interrupted_receipt(client, control, monkeypatch):
    base, draft = stage_create(client, **modern())

    async def unavailable(self, entry, **kwargs):
        raise OperationalError("fixture", {}, Exception("offline"))

    with monkeypatch.context() as patch:
        patch.setattr(GenerationRuntime, "persist_response", unavailable)
        before = start(client, base, draft)
    runtime = client.app.state.generation
    assert len(control["calls"]) == 1
    client.portal.call(runtime.close)
    entry = runtime.journal.read(runtime.journal.pending()[0])
    before = read(client, f"{base}/{draft['id']}")
    assert before["calls"][0]["status"] == "outcome_uncertain"
    assert before["calls"][0]["error_code"] == "process_interrupted"
    return base, draft, before, runtime, entry


def test_cli_relocation_is_verified_idempotent_and_recovers_without_dispatch(
    feedback_run, monkeypatch, tmp_path,
):
    client, control = feedback_run
    base, draft, before, runtime, entry = interrupted_receipt(client, control, monkeypatch)
    old_content, new_content = tmp_path / "old" / "content", tmp_path / "new" / "content"
    source_url = "postgresql+psycopg://fixture@old-server:5432/original_test"
    source = ResponseJournal(old_content / "generation-response-buffer", source_url)
    source.root.mkdir(parents=True)
    source_path = source.root / f"{entry['call_id']}.json"
    source_path.write_text(json.dumps(entry), encoding="utf-8")
    original_bytes = source_path.read_bytes()
    target = ResponseJournal(new_content / "generation-response-buffer", DATABASE_URL)
    environment = {
        **os.environ, "FIXTURE_SOURCE_URL": source_url, "FIXTURE_TARGET_URL": DATABASE_URL,
    }
    plan_path = tmp_path / "plan.json"
    common = [
        "--source-database-url-env", "FIXTURE_SOURCE_URL",
        "--target-database-url-env", "FIXTURE_TARGET_URL",
        "--source-content-root", str(old_content), "--content-root", str(new_content),
        "--plan", str(plan_path),
    ]

    def command(action, *extra):
        result = subprocess.run(
            [sys.executable, "-B", "scripts/migrate_response_buffer.py", action, *common, *extra],
            env=environment, capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    plan = command("preview")
    assert not target.pending()  # preview is read-only
    for _ in range(2):
        assert command("apply", "--approve-plan-sha256", plan["sha256"])["status"] == "copied"
    assert source_path.read_bytes() == original_bytes
    assert target.read(target.pending()[0]) == entry
    assert read(client, f"{base}/{draft['id']}") == before  # copy did not mutate DB/costs

    async def recover():
        replacement = GenerationRuntime(
            runtime.database, runtime.profiles, runtime.credentials,
            response_root=new_content / "generation-response-buffer",
        )
        client.app.state.generation = replacement
        await replacement.initialize()
        await replacement.maintain_once()

    client.portal.call(recover)
    after = read(client, f"{base}/{draft['id']}")
    assert len(control["calls"]) == 1
    assert after["calls"][0]["status"] == "response_saved"
    assert after["calls"][0]["actual_cost_cny"] is not None
    saved = read(client, f"{base}/{draft['id']}/calls/{entry['call_id']}")
    assert saved["response"] == entry["response"]
    assert not target.pending() and source_path.read_bytes() == original_bytes
    stable = after["calls"][0]
    client.portal.call(client.app.state.generation.maintain_once)
    assert read(client, f"{base}/{draft['id']}")["calls"][0] == stable


@pytest.mark.parametrize(
    "conflict", ["request", "missing_call", "saved_response", "target", "plan"],
)
def test_migration_rejects_incompatible_receipts_and_keeps_originals(
    feedback_run, monkeypatch, tmp_path, conflict,
):
    client, control = feedback_run
    _, _, _, runtime, entry = interrupted_receipt(client, control, monkeypatch)
    source = runtime.journal
    target = ResponseJournal(tmp_path / "target-buffer", "postgresql://fixture@new/db_test")
    original = source.pending()[0].read_bytes()
    engine = create_engine(DATABASE_URL)
    try:
        with Session(engine) as session:
            plan = preview_migration(session, source, target)
        with Session(engine) as session, session.begin():
            call = session.get(GenerationCallRecord, UUID(entry["call_id"]))
            if conflict == "request":
                call.request = {**call.request, "model_request": {"different": True}}
            elif conflict == "saved_response":
                call.response = {"different": True}
        if conflict == "missing_call":
            changed = {**entry, "call_id": str(uuid4())}
            changed["sha256"] = fingerprint({k: v for k, v in changed.items() if k != "sha256"})
            (source.root / f"{changed['call_id']}.json").write_text(json.dumps(changed))
        if conflict == "target":
            target.save(UUID(entry["batch_id"]), UUID(entry["call_id"]), "bad", entry["response"])
        if conflict == "plan":
            plan["target_root"] += "different"
        with Session(engine) as session, pytest.raises(WorkflowError):
            apply_migration(session, source, target, plan, plan["sha256"], tmp_path / "audit")
        assert (source.root / f"{entry['call_id']}.json").read_bytes() == original
        if conflict != "target":
            assert not target.pending()
    finally:
        engine.dispose()
