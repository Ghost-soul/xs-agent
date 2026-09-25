# ruff: noqa: F811
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from novel_writer.db.engine import Database
from novel_writer.db.models import (
    ChapterRecord,
    ChapterRevisionRecord,
    ProjectRecord,
    StateVersionRecord,
)
from novel_writer.domain.state import StoryState
from novel_writer.knowledge import embedding
from novel_writer.knowledge.models import KnowledgeIndexRecord, KnowledgeVectorRecord
from novel_writer.knowledge.service import KnowledgeService
from novel_writer.knowledge.worker import KnowledgeWorker
from novel_writer.services.errors import NotFoundError
from tests.integration.support import DATABASE_URL, headers
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated, settle  # noqa: F401
from tests.integration.test_generation_feedback import feedback_run  # noqa: F401
from tests.integration.test_generation_longform import longform, stage_create  # noqa: F401
from tests.integration.test_genre_generation import generation, post, read, start  # noqa: F401
from tests.integration.test_step_recovery import (  # noqa: F401
    authorize as authorize_step,
)
from tests.integration.test_step_recovery import (
    fail,
)
from tests.integration.test_step_recovery import recovery as recovery


async def seed(db, *, name="甲书", body="她将玄铁剑交给师父。", project=None, previous=None):
    async with db.session() as session, session.begin():
        if project is None:
            project = ProjectRecord(title=name)
            session.add(project)
            await session.flush()
            chapter = ChapterRecord(project_id=project.id, ordinal=1, title="起点")
            session.add(chapter)
            await session.flush()
        else:
            project = await session.get(ProjectRecord, project.id)
            chapter = await session.scalar(
                select(ChapterRecord).where(ChapterRecord.project_id == project.id)
            )
        version = StateVersionRecord(
            project_id=project.id,
            number=2 if previous else 1,
            parent_id=previous.id if previous else None,
            chapter_revisions={},
            state=StoryState().model_dump(mode="json"),
        )
        session.add(version)
        await session.flush()
        revision = ChapterRevisionRecord(
            chapter_id=chapter.id, base_version_id=version.id, body=body, status="accepted"
        )
        session.add(revision)
        await session.flush()
        version.chapter_revisions = {str(chapter.id): str(revision.id)}
        project.current_version_id = version.id
        chapter.current_revision_id = revision.id
    return project, version, revision


@pytest.mark.asyncio
async def test_cross_novel_versions_revision_changes_and_transactional_queue(clean_test_database):
    db = Database(DATABASE_URL)
    try:
        p, old, _ = await seed(db)
        other, foreign, _ = await seed(db, name="乙书", body="玄铁剑藏在密室。")
        _, new, _ = await seed(db, project=p, previous=old, body="师父退回玄铁剑，她将它收入剑鞘。")
        worker = KnowledgeWorker(db, Path("missing-model"))
        while await worker.tick():
            pass
        async with db.session() as session:
            service = KnowledgeService(session)
            for version, expected, absent in [(old, "交给", "退回"), (new, "退回", "交给")]:
                sources = await service.sources(p.id, version.id)
                receipt = await service.retrieve(p.id, version.id, sources, ["玄铁剑"])
                combined = str(receipt["hits"])
                assert expected in combined and absent not in combined and "密室" not in combined
                assert (await session.get(KnowledgeIndexRecord, version.id)).status == "lexical"
            with pytest.raises(NotFoundError):
                await service.sources(p.id, foreign.id)
            assert other.id != p.id
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_local_vectors_reused_on_new_revision_and_fallback_on_missing_model(
    clean_test_database, monkeypatch
):
    db = Database(DATABASE_URL)
    calls = []
    monkeypatch.setattr(embedding, "identity", lambda *args: "a" * 64)

    def fake(texts, **kwargs):
        if not kwargs.get("query"):
            calls.extend(texts)
        return [[1.0, *([0.0] * 511)] for _ in texts]

    monkeypatch.setattr(embedding, "embed", fake)
    try:
        p, old, _ = await seed(db)
        worker = KnowledgeWorker(db, Path("fixture"))
        while await worker.tick():
            pass
        count = len(calls)
        _, new, _ = await seed(db, project=p, previous=old)
        while await worker.tick():
            pass
        assert len(calls) == count
        async with db.session() as session:
            service = KnowledgeService(session)
            sources = await service.sources(p.id, new.id)
            receipt = await service.retrieve(
                p.id, new.id, sources, ["兵器移交"], model_key="a" * 64
            )
            assert receipt["mode"] == "hybrid" and receipt["hits"]
            assert (
                await session.scalar(select(func.count()).select_from(KnowledgeVectorRecord))
                == count
            )

            def broken(*args, **kwargs):
                raise OSError("fixture unavailable")

            monkeypatch.setattr(embedding, "embed", broken)
            fallback = await service.retrieve(p.id, new.id, sources, ["玄铁剑"], model_key="a" * 64)
            assert fallback["mode"] == "lexical" and fallback["hits"]
            # A vector-index SQL error must roll back its savepoint, not poison the caller.
            monkeypatch.setattr(embedding, "embed", lambda texts, **kw: [[1.0, 0.0]])
            fallback = await service.retrieve(p.id, new.id, sources, ["玄铁剑"], model_key="a" * 64)
            assert fallback["reason"] == "index_unavailable" and fallback["hits"]
            assert await session.scalar(text("SELECT 1")) == 1
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_future_cutoff_excludes_later_prose_and_later_state(clean_test_database):
    db = Database(DATABASE_URL)
    try:
        p, version, _ = await seed(db)
        async with db.session() as session, session.begin():
            value = await session.get(StateVersionRecord, version.id)
            chapter = ChapterRecord(project_id=p.id, ordinal=2, title="后来")
            session.add(chapter)
            await session.flush()
            revision = ChapterRevisionRecord(
                chapter_id=chapter.id,
                base_version_id=version.id,
                body="玄铁剑后来被毁。",
                status="accepted",
            )
            session.add(revision)
            await session.flush()
            value.chapter_revisions = {**value.chapter_revisions, str(chapter.id): str(revision.id)}
            value.state = {
                **value.state,
                "world_rules": [{"id": str(uuid4()), "statement": "玄铁剑已毁"}],
            }
        async with db.session() as session:
            service = KnowledgeService(session)
            result = await service.retrieve(
                p.id, version.id, await service.sources(p.id, version.id), ["玄铁剑"], cutoff=1
            )
            assert result["hits"] and "毁" not in str(result["hits"])
    finally:
        await db.dispose()


def test_actual_role_requests_freeze_retrieval_and_keep_unit_handoffs(feedback_run):
    client, control = feedback_run
    base, draft = stage_create(
        client,
        context_policy="knowledge-rag-v1",
        input_limit=200000,
        writing_policy="guided-v1",
        feedback_policy="logic-v1",
        automation_policy="stage-auto-v1",
    )
    done = start(client, base, draft)
    assert control["calls"] == [
        "plan",
        "write:1",
        "memory:1",
        "write:2",
        "memory:2",
        "write:3",
        "memory:3",
        "checker",
    ], done["state"]
    assert done["snapshot"] == draft["snapshot"]
    assert "knowledge_sources" not in done["snapshot"]
    assert done["snapshot"]["knowledge_source_summary"]["full_sources_saved_locally"]
    for call in done["calls"]:
        receipt = read(client, f"{base}/{done['id']}/calls/{call['id']}")["request"]
        assert (
            receipt["knowledge_retrieval"]["project_id"]
            == draft["snapshot"]["knowledge_project_id"]
        )
        packet = control["requests"][call["action"]]["knowledge_context"]
        assert packet["history_count"] <= packet["history_limit"]
    assert control["requests"]["write:2"]["knowledge_context"]["working_chain_sha256"]


def test_failed_step_replays_saved_request_even_when_retrieval_changes(recovery, monkeypatch):
    client, control, _, target, before = fail(
        recovery, context_policy="knowledge-rag-v1", input_limit=200000,
    )
    failed = next(c for c in before["calls"] if c["action"] == "write:1")
    saved = read(client, target + f"/calls/{failed['id']}")["request"]
    preview = read(client, target + "/step-recovery-preview")
    del control["retry_failure_action"]
    control["pause_action"] = "write:1"

    async def unexpected(*args, **kwargs):
        raise AssertionError("Recovery must not perform another retrieval")

    monkeypatch.setattr(KnowledgeService, "retrieve", unexpected)
    assert authorize_step(client, target, preview).status_code == 200
    done = settle(client, target.rsplit("/", 1)[0], before["id"])
    replacement = read(client, target + f"/calls/{done['calls'][-1]['id']}")["request"]
    assert replacement["model_request"] == saved["model_request"]
    assert replacement["knowledge_retrieval"] == saved["knowledge_retrieval"]


def test_memory_capacity_replacement_preserves_frozen_retrieval(longform, monkeypatch):
    from tests.integration.test_generation_memory_recovery import authorize

    client, control = longform
    control["incomplete_action"] = "memory:2"
    base, draft = stage_create(
        client, context_policy="knowledge-rag-v1", input_limit=200000,
        auxiliary_output_limit=6000,
    )
    before = start(client, base, draft)
    target = f"{base}/{before['id']}"
    failed = next(c for c in before["calls"] if c["action"] == "memory:2")
    saved = read(client, target + f"/calls/{failed['id']}")["request"]
    preview = read(client, target + "/memory-recovery-preview?output_limit=24000&all_roles=false")
    assert not preview["blockers"]
    del control["incomplete_action"]
    control["pause_action"] = "memory:2"

    async def unexpected(*args, **kwargs):
        raise AssertionError("Replacement must reuse original retrieval")

    monkeypatch.setattr(KnowledgeService, "retrieve", unexpected)
    result = authorize(client, target, preview)
    assert result.status_code == 200, result.text
    done = settle(client, base, before["id"])
    request = read(client, target + f"/calls/{done['calls'][-1]['id']}")["request"]
    assert request["knowledge_retrieval"] == saved["knowledge_retrieval"]
    assert request["model_request"]["user_prompt"] == saved["model_request"]["user_prompt"]


@pytest.mark.asyncio
async def test_missing_schema_uses_same_version_lexical_sources(clean_test_database):
    db = Database(DATABASE_URL)
    try:
        p, version, _ = await seed(db)
        async with db.session() as session:
            # Transactional DDL is rolled back; the dedicated test schema is restored.
            await session.execute(text(
                "DROP TABLE knowledge_indexes, knowledge_chunks, knowledge_vectors",
            ))
            service = KnowledgeService(session)
            assert (await service.status(p.id, version.id))["status"] == "migration_required"
            receipt = await service.retrieve(
                p.id, version.id, await service.sources(p.id, version.id),
                ["玄铁剑"], model_key="a" * 64,
            )
            assert receipt["mode"] == "lexical" and receipt["hits"]
            await session.rollback()
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_index_restart_native_failure_and_model_change(clean_test_database, monkeypatch):
    db = Database(DATABASE_URL)
    calls = []
    monkeypatch.setattr(embedding, "identity", lambda *args: "a" * 64)

    def fake(texts, **kwargs):
        calls.extend(texts)
        return [[1.0, *([0.0] * 511)] for _ in texts]

    monkeypatch.setattr(embedding, "embed", fake)
    try:
        _, version, _ = await seed(db, body="".join(f"{i:05}她走向城门。" for i in range(1000)))
        worker = KnowledgeWorker(db, Path("fixture"))
        assert await worker.tick() and len(calls) == 16
        restarted = KnowledgeWorker(db, Path("fixture"))
        while await restarted.tick():
            pass
        count = len(calls)
        assert len(set(calls)) == count and count > 16
        monkeypatch.setattr(embedding, "identity", lambda *args: "b" * 64)

        class NativeModelFailure(Exception):
            pass

        def broken(*args, **kwargs):
            raise NativeModelFailure("synthetic native ONNX error")

        monkeypatch.setattr(embedding, "embed", broken)
        assert await restarted.tick()
        async with db.session() as session, session.begin():
            job = await session.get(KnowledgeIndexRecord, version.id)
            assert job.status == "lexical" and job.error_code == "local_model_unavailable"
            job.status, job.error_code = "queued", None
        monkeypatch.setattr(embedding, "embed", fake)
        while await restarted.tick():
            pass
        assert len(calls) == count * 2
        async with db.session() as session:
            job = await session.get(KnowledgeIndexRecord, version.id)
            assert job.status == "ready" and job.model_key == "b" * 64
    finally:
        await db.dispose()


def test_api_isolation_rebuild_rollback_and_book_deletion(generation, monkeypatch):
    client, _ = generation
    db = client.app.state.database
    monkeypatch.setattr(embedding, "identity", lambda *args: "a" * 64)
    monkeypatch.setattr(
        embedding, "embed", lambda texts, **kw: [[1.0, *([0.0] * 511)] for _ in texts],
    )

    async def initialize():
        p, old, _ = await seed(db)
        other, foreign, _ = await seed(db, name="乙书", body="另一部小说的剑。")
        _, new, _ = await seed(db, project=p, previous=old, body="她已归还玄铁剑。")
        worker = KnowledgeWorker(db, Path("fixture"))
        while await worker.tick():
            pass
        return p, old, other, foreign, new

    p, old, other, foreign, new = client.portal.call(initialize)
    target = f"/api/projects/{p.id}"
    assert read(client, target + "/knowledge")["status"] == "ready"
    forbidden = post(
        client, target + "/knowledge/search", {"query": "剑", "version_id": str(foreign.id)},
    )
    assert forbidden.status_code == 404
    own = post(client, target + "/knowledge/search", {"query": "玄铁剑"}).json()
    assert "归还" in str(own["hits"]) and "另一部" not in str(own["hits"])
    assert post(client, target + "/knowledge/rebuild", {}).status_code == 202

    async def rollback():
        async with db.session() as session, session.begin():
            project = await session.get(ProjectRecord, p.id)
            project.current_version_id = old.id

    client.portal.call(rollback)
    result = post(client, target + "/knowledge/search", {"query": "玄铁剑"}).json()
    assert result["version_id"] == str(old.id) and "归还" not in str(result["hits"])
    preview = read(client, target + "/delete-preview")
    response = client.post(target + "/delete", headers=headers(str(uuid4())), json={
        "confirmed": True, "confirmed_title": preview["title"],
        "binding_sha256": preview["binding_sha256"],
    })
    assert response.status_code == 200, response.text

    async def verify():
        async with db.session() as session:
            assert await session.get(KnowledgeIndexRecord, old.id) is None
            assert await session.get(KnowledgeIndexRecord, new.id) is None
            assert not await session.scalar(select(KnowledgeVectorRecord).where(
                KnowledgeVectorRecord.project_id == p.id,
            ))
            assert await session.get(KnowledgeIndexRecord, foreign.id) is not None

    client.portal.call(verify)
