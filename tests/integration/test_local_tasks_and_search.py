from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text, update

from novel_writer.db.engine import Database
from novel_writer.db.models import (
    ChapterRecord,
    ChapterRevisionRecord,
    LocalTaskRecord,
    ProjectRecord,
    SearchDocumentRecord,
    StateVersionRecord,
)
from novel_writer.services.canonical_json import canonical_json_sha256
from novel_writer.services.local_search import LocalSearchService
from novel_writer.services.local_tasks import (
    LocalTaskExecution,
    LocalTaskRunner,
    LocalTaskService,
    local_task_artifact_path,
)
from tests.integration.support import DATABASE_URL

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="NOVEL_WRITER_TEST_DATABASE_URL is not set",
    ),
]


async def _seed_formal_project(database: Database) -> tuple[UUID, UUID, UUID, UUID]:
    async with database.session() as session, session.begin():
        project = ProjectRecord(title=f"本地任务集成测试-{uuid4()}")
        session.add(project)
        await session.flush()
        version = StateVersionRecord(
            project_id=project.id,
            number=1,
            state={},
            chapter_revisions={},
            chapter_titles={},
        )
        session.add(version)
        await session.flush()
        chapter = ChapterRecord(
            project_id=project.id,
            ordinal=1,
            display_ordinal=1,
            title="数据库当前标题",
        )
        session.add(chapter)
        await session.flush()
        revision = ChapterRevisionRecord(
            chapter_id=chapter.id,
            base_version_id=version.id,
            body="冻结版本正文：潮印在旧阵眼旁熄灭。",
            status="committed",
        )
        session.add(revision)
        await session.flush()
        chapter.current_revision_id = revision.id
        version.chapter_revisions = {str(chapter.id): str(revision.id)}
        version.chapter_titles = {str(chapter.id): "冻结版本标题"}
        project.current_version_id = version.id
        await session.flush()
        return project.id, version.id, chapter.id, revision.id


async def _quiesce_unfinished_tasks(database: Database) -> None:
    async with database.session() as session, session.begin():
        await session.execute(
            update(LocalTaskRecord)
            .where(LocalTaskRecord.status.in_(("queued", "running")))
            .values(
                status="failed",
                error_code="test_fixture_quiesced",
                lease_owner=None,
                lease_expires_at=None,
                completed_at=datetime.now(UTC),
            )
        )


async def _queue_text_export(database: Database, project_id: UUID) -> UUID:
    async with database.session() as session, session.begin():
        item = await LocalTaskService(session).queue(
            project_id,
            "export_txt",
            f"integration-export-{uuid4()}",
        )
        return item.id


async def _queue_search_rebuild(database: Database, project_id: UUID, key: str) -> UUID:
    async with database.session() as session, session.begin():
        item = await LocalTaskService(session).queue(project_id, "search_rebuild", key)
        return item.id


@pytest.mark.asyncio
async def test_export_task_uses_queued_formal_version_after_current_version_changes(
    tmp_path: Path,
) -> None:
    database = Database(str(DATABASE_URL))
    try:
        await _quiesce_unfinished_tasks(database)
        project_id, first_version_id, chapter_id, first_revision_id = (
            await _seed_formal_project(database)
        )
        task_id = await _queue_text_export(database, project_id)

        async with database.session() as session, session.begin():
            project = await session.get(ProjectRecord, project_id, with_for_update=True)
            assert project is not None
            second_version = StateVersionRecord(
                project_id=project_id,
                number=2,
                parent_id=first_version_id,
                parent_number=1,
                state={"version": 2},
                chapter_revisions={},
                chapter_titles={},
            )
            session.add(second_version)
            await session.flush()
            second_revision = ChapterRevisionRecord(
                chapter_id=chapter_id,
                base_version_id=second_version.id,
                supersedes_id=first_revision_id,
                body="新正式版本正文：不应进入旧任务工件。",
                status="committed",
            )
            session.add(second_revision)
            await session.flush()
            second_version.chapter_revisions = {str(chapter_id): str(second_revision.id)}
            second_version.chapter_titles = {str(chapter_id): "新正式版本标题"}
            project.current_version_id = second_version.id

        assert await LocalTaskRunner(database, "snapshot-worker", tmp_path).tick() is True
        async with database.session() as session:
            item = await session.get(LocalTaskRecord, task_id)
            assert item is not None
            assert item.status == "completed"
            assert item.output_sha256 is not None
            path = local_task_artifact_path(tmp_path, task_id, item.output_sha256, "txt")
            exported = path.read_text(encoding="utf-8-sig")
            assert "冻结版本正文" in exported
            assert "冻结版本标题" in exported
            assert "新正式版本正文" not in exported
            assert item.payload["formal_snapshot"]["version_id"] == str(first_version_id)
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_two_workers_only_claim_one_local_task() -> None:
    database = Database(str(DATABASE_URL))
    try:
        await _quiesce_unfinished_tasks(database)
        project_id, _, _, _ = await _seed_formal_project(database)
        task_id = await _queue_text_export(database, project_id)

        async def claim(owner: str) -> UUID | None:
            async with database.session() as session, session.begin():
                item = await LocalTaskService(session).claim(owner)
                return item.id if item is not None else None

        claims = await asyncio.gather(claim("worker-a"), claim("worker-b"))
        assert claims.count(task_id) == 1
        assert claims.count(None) == 1
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_expired_lease_is_taken_over_by_a_new_owner() -> None:
    database = Database(str(DATABASE_URL))
    try:
        await _quiesce_unfinished_tasks(database)
        project_id, _, _, _ = await _seed_formal_project(database)
        task_id = await _queue_text_export(database, project_id)
        async with database.session() as session, session.begin():
            first = await LocalTaskService(session).claim("worker-a", lease_seconds=-1)
            assert first is not None and first.id == task_id
        async with database.session() as session, session.begin():
            second = await LocalTaskService(session).claim("worker-b")
            assert second is not None and second.id == task_id
            assert second.lease_owner == "worker-b"
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_old_owner_cannot_overwrite_new_owner_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novel_writer.services import local_tasks

    database = Database(str(DATABASE_URL))
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls = 0

    async def controlled_execution(
        _session: Any,
        _item: LocalTaskRecord,
    ) -> LocalTaskExecution:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            await release_first.wait()
            return LocalTaskExecution(output_sha256="a" * 64, artifact=None)
        return LocalTaskExecution(output_sha256="b" * 64, artifact=None)

    monkeypatch.setattr(local_tasks, "_execute_task", controlled_execution)
    try:
        await _quiesce_unfinished_tasks(database)
        project_id, version_id, _, _ = await _seed_formal_project(database)
        payload = {
            "schema_version": "local-task-v1",
            "project_id": str(project_id),
            "kind": "search_rebuild",
            "requested_current_version_id": str(version_id),
        }
        async with database.session() as session, session.begin():
            item = LocalTaskRecord(
                project_id=project_id,
                kind="search_rebuild",
                input_sha256=canonical_json_sha256(payload),
                payload=payload,
            )
            session.add(item)
            await session.flush()
            task_id = item.id

        first_tick = asyncio.create_task(
            LocalTaskRunner(database, "worker-a", tmp_path).tick()
        )
        await asyncio.wait_for(first_started.wait(), timeout=5)
        async with database.session() as session, session.begin():
            await session.execute(
                update(LocalTaskRecord)
                .where(LocalTaskRecord.id == task_id)
                .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        assert await LocalTaskRunner(database, "worker-b", tmp_path).tick() is True
        release_first.set()
        assert await asyncio.wait_for(first_tick, timeout=5) is True

        async with database.session() as session:
            completed = await session.get(LocalTaskRecord, task_id)
            assert completed is not None
            assert completed.status == "completed"
            assert completed.output_sha256 == "b" * 64
            assert completed.lease_owner is None
    finally:
        release_first.set()
        await database.dispose()


@pytest.mark.asyncio
async def test_search_uses_pg_trgm_gin_indexes_and_stable_pagination() -> None:
    database = Database(str(DATABASE_URL))
    try:
        project_id, version_id, _, _ = await _seed_formal_project(database)
        async with database.session() as session, session.begin():
            extension = await session.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'pg_trgm'")
            )
            index_rows = list(
                await session.scalars(
                    text(
                        "SELECT indexdef FROM pg_indexes "
                        "WHERE tablename = 'search_documents' AND indexdef ILIKE '%gin_trgm_ops%'"
                    )
                )
            )
            assert extension
            assert len(index_rows) == 2
            for ordinal in range(125):
                body = f"潮印分页证据 {ordinal:03d}"
                session.add(
                    SearchDocumentRecord(
                        project_id=project_id,
                        document_key=f"{ordinal:064x}",
                        source_kind="chapter",
                        source_id=f"revision-{ordinal:03d}",
                        version_id=version_id,
                        version_number=1,
                        revision_id=None,
                        title=f"第{ordinal + 1}章 潮声",
                        normalized_text=body,
                        text_sha256=canonical_json_sha256(body),
                        locator_metadata={
                            "route": f"/projects/{project_id}/read?ordinal={ordinal + 1}",
                            "ordinal": ordinal + 1,
                        },
                        is_current=True,
                    )
                )

        async with database.session() as session:
            service = LocalSearchService(session)
            first = await service.search(project_id, "潮印", limit=50)
            second = await service.search(
                project_id,
                "潮印",
                limit=50,
                cursor=int(first["next_cursor"]),
            )
            third = await service.search(
                project_id,
                "潮印",
                limit=50,
                cursor=int(second["next_cursor"]),
            )
            assert first["total"] == 125
            assert [len(first["results"]), len(second["results"]), len(third["results"])] == [
                50,
                50,
                25,
            ]
            assert third["next_cursor"] is None
            identities = {
                item["source_id"]
                for page in (first, second, third)
                for item in page["results"]
            }
            assert len(identities) == 125
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_concurrent_search_rebuild_requests_share_one_task() -> None:
    database = Database(str(DATABASE_URL))
    try:
        await _quiesce_unfinished_tasks(database)
        project_id, _, _, _ = await _seed_formal_project(database)
        first_id, second_id = await asyncio.gather(
            _queue_search_rebuild(database, project_id, f"search-a-{uuid4()}"),
            _queue_search_rebuild(database, project_id, f"search-b-{uuid4()}"),
        )
        assert first_id == second_id
        async with database.session() as session:
            count = await session.scalar(
                select(func.count(LocalTaskRecord.id)).where(
                    LocalTaskRecord.project_id == project_id,
                    LocalTaskRecord.kind == "search_rebuild",
                    LocalTaskRecord.status.in_(("queued", "running")),
                )
            )
            assert count == 1
    finally:
        await database.dispose()
