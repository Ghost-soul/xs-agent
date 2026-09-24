"""Persistence primitives for formal manuscript management."""

import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import (
    ChapterRecord,
    ChapterRevisionRecord,
    ProjectRecord,
    StateDeltaRecord,
    StateVersionRecord,
)
from novel_writer.services.errors import (
    NotFoundError,
    WorkflowError,
)
from novel_writer.services.formal_version_sync import invalidate_formal_dependents
from novel_writer.services.idempotency import (
    load_idempotent_response,
    save_idempotent_response,
)


class WorkflowPersistenceMixin:
    session: AsyncSession

    async def _root_revision(self, revision: ChapterRevisionRecord) -> ChapterRevisionRecord:
        seen: set[UUID] = set()
        current = revision
        while current.supersedes_id is not None:
            if current.id in seen:
                raise WorkflowError("chapter revision lineage contains a cycle")
            seen.add(current.id)
            current = await self._revision(current.supersedes_id)
        return current

    async def _synchronize_formal_dependents(
        self,
        project_id: UUID,
        revision_map: dict[str, str],
        pause_reason: str,
    ) -> dict[str, Any]:
        """Invalidate every unfinished workspace that was frozen from the old formal state."""

        synchronized = await invalidate_formal_dependents(
            self.session,
            project_id,
            pause_reason,
        )
        return {**synchronized, "formal_revision_ids": list(revision_map.values())}

    async def _renumber_formal_chapters(
        self,
        project_id: UUID,
        revision_map: dict[str, str],
        title_map: dict[str, str],
    ) -> None:
        chapters = list(
            await self.session.scalars(
                select(ChapterRecord).where(ChapterRecord.project_id == project_id)
            )
        )
        for chapter in chapters:
            chapter.display_ordinal = None
            chapter.current_revision_id = None
        await self.session.flush()
        formal = [item for item in chapters if str(item.id) in revision_map]
        formal.sort(key=lambda item: item.ordinal)
        for display_ordinal, chapter in enumerate(formal, 1):
            restored_title = title_map.get(str(chapter.id), "").strip()
            if restored_title:
                chapter.title = restored_title
            elif re.fullmatch(r"第\d+章", chapter.title):
                chapter.title = f"第{display_ordinal}章"
            chapter.display_ordinal = display_ordinal
            chapter.current_revision_id = UUID(revision_map[str(chapter.id)])
            await self.session.flush()

    async def _idempotent(
        self,
        command: str,
        key: str,
        request_payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        return await load_idempotent_response(
            self.session,
            command,
            key,
            request_payload,
            resource_scope=command,
        )

    async def _save_idempotent(
        self,
        command: str,
        key: str,
        response: dict[str, Any],
        request_payload: dict[str, Any],
    ) -> None:
        await save_idempotent_response(
            self.session,
            command,
            key,
            response,
            request_payload,
            resource_scope=command,
        )

    async def _project(self, project_id: UUID) -> ProjectRecord:
        record = await self.session.get(ProjectRecord, project_id)
        if record is None:
            raise NotFoundError("project not found")
        return record

    async def _locked_project(self, project_id: UUID) -> ProjectRecord:
        record = await self.session.scalar(
            select(ProjectRecord).where(ProjectRecord.id == project_id).with_for_update()
        )
        if record is None:
            raise NotFoundError("project not found")
        return record

    async def _chapter(self, chapter_id: UUID) -> ChapterRecord:
        record = await self.session.get(ChapterRecord, chapter_id)
        if record is None:
            raise NotFoundError("chapter not found")
        return record


    async def _revision(self, revision_id: UUID) -> ChapterRevisionRecord:
        record = await self.session.get(ChapterRevisionRecord, revision_id)
        if record is None:
            raise NotFoundError("revision not found")
        return record


    async def _version(self, version_id: UUID | None) -> StateVersionRecord:
        record = await self.session.get(StateVersionRecord, version_id)
        if record is None:
            raise NotFoundError("state version not found")
        return record

    async def _project_version(self, project_id: UUID, number: int) -> StateVersionRecord:
        record = await self.session.scalar(
            select(StateVersionRecord).where(
                StateVersionRecord.project_id == project_id,
                StateVersionRecord.number == number,
            )
        )
        if record is None:
            raise NotFoundError("state version not found")
        return record

    async def _delta_for_revision(self, revision_id: UUID) -> StateDeltaRecord:
        record = await self.session.scalar(
            select(StateDeltaRecord).where(StateDeltaRecord.revision_id == revision_id)
        )
        if record is None:
            raise NotFoundError("state delta not found")
        return record
