from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import (
    AgentCallRecord,
    ApprovalRecord,
    ChapterRecord,
    ChapterSummaryRecord,
    ProjectRecord,
    StateVersionRecord,
    WritingSessionRecord,
)
from novel_writer.services.errors import ConflictError, NotFoundError
from novel_writer.services.formal_version_sync import rebase_title_only_dependents
from novel_writer.services.idempotency import load_idempotent_response, save_idempotent_response


class FormalTitleRenameService:
    """Version author-edited formal titles without touching prose or StoryState."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def apply_manual(
        self,
        project_id: UUID,
        base_version: int,
        titles: dict[str, str],
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        command = f"manual_formal_title_rename:{project_id}"
        request_payload = {
            "base_version": base_version,
            "titles": titles,
            "reason": reason,
        }
        cached = await load_idempotent_response(
            self.session,
            command,
            idempotency_key,
            request_payload,
            resource_scope=command,
        )
        if cached is not None:
            return cached
        response = await self._apply_titles(project_id, base_version, titles, reason)
        await save_idempotent_response(
            self.session,
            command,
            idempotency_key,
            response,
            request_payload,
            resource_scope=command,
        )
        return response

    async def _apply_titles(
        self,
        project_id: UUID,
        base_version: int,
        titles: dict[str, str],
        reason: str,
    ) -> dict[str, Any]:
        project = await self._locked_project(project_id)
        current = await self._version(project.current_version_id)
        if current.number != base_version:
            raise ConflictError("formal title rename base version is stale")
        await self._assert_no_provider_call(project.id)
        formal_ids = set(current.chapter_revisions)
        if set(titles) != formal_ids:
            raise ConflictError(
                "formal title rename must include every current chapter exactly once"
            )
        normalized = {chapter_id: _validate_title(title) for chapter_id, title in titles.items()}
        if len(set(normalized.values())) != len(normalized):
            raise ConflictError("formal chapter titles must be unique")
        chapters = list(
            await self.session.scalars(
                select(ChapterRecord)
                .where(
                    ChapterRecord.project_id == project.id,
                    ChapterRecord.id.in_([UUID(value) for value in formal_ids]),
                )
                .with_for_update()
            )
        )
        if len(chapters) != len(formal_ids):
            raise ConflictError("formal chapter title mapping is incomplete")
        new_version = StateVersionRecord(
            project_id=project.id,
            number=current.number + 1,
            parent_id=current.id,
            parent_number=current.number,
            state=current.state,
            chapter_revisions=current.chapter_revisions,
            chapter_titles=normalized,
        )
        self.session.add(new_version)
        await self.session.flush()
        for chapter in chapters:
            chapter.title = normalized[str(chapter.id)]
        summaries = list(
            await self.session.scalars(
                select(ChapterSummaryRecord).where(ChapterSummaryRecord.project_id == project.id)
            )
        )
        title_by_ordinal = {
            chapter.display_ordinal or chapter.ordinal: chapter.title for chapter in chapters
        }
        for summary in summaries:
            if summary.chapter_number in title_by_ordinal:
                summary.title = title_by_ordinal[summary.chapter_number]
        sync = await rebase_title_only_dependents(
            self.session,
            project.id,
            current.id,
            new_version.id,
            new_version.number,
        )
        project.current_version_id = new_version.id
        self.session.add(
            ApprovalRecord(
                target_type="formal_chapter_titles",
                target_id=new_version.id,
                decision="approved",
                reason=reason,
            )
        )
        return {
            "project_id": str(project.id),
            "version": new_version.number,
            "version_id": str(new_version.id),
            "base_version": current.number,
            "chapter_count": len(normalized),
            "prose_changed": False,
            "story_state_changed": False,
            **sync,
        }


    async def _assert_no_provider_call(self, project_id: UUID) -> None:
        session_ids = select(WritingSessionRecord.id).where(
            WritingSessionRecord.project_id == project_id
        )
        agent = await self.session.scalar(
            select(AgentCallRecord.id).where(
                AgentCallRecord.session_id.in_(session_ids),
                AgentCallRecord.status == "executing",
            )
        )
        if agent is not None:
            raise ConflictError("a Provider call is executing for this project")

    async def _locked_project(self, project_id: UUID) -> ProjectRecord:
        project = await self.session.scalar(
            select(ProjectRecord).where(ProjectRecord.id == project_id).with_for_update()
        )
        if project is None:
            raise NotFoundError("project not found")
        return project

    async def _version(self, version_id: UUID | None) -> StateVersionRecord:
        if version_id is None:
            raise NotFoundError("formal version not found")
        version = await self.session.get(StateVersionRecord, version_id)
        if version is None:
            raise NotFoundError("formal version not found")
        return version


def _validate_title(value: str) -> str:
    title = value.strip()
    if not 1 <= len(title) <= 200:
        raise ConflictError("formal chapter title must contain 1 to 200 characters")
    if "\n" in title or "\r" in title:
        raise ConflictError("formal chapter title must be a single line")
    return title


