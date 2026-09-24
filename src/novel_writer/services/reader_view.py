from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import (
    ChapterRecord,
    ChapterRevisionRecord,
    ProjectRecord,
    StateVersionRecord,
)
from novel_writer.services.errors import NotFoundError


class ReaderViewService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def build(self, project_id: UUID) -> dict[str, Any]:
        project = await self.session.get(ProjectRecord, project_id)
        if project is None or project.current_version_id is None:
            raise NotFoundError("project not found")
        version = await self.session.get(StateVersionRecord, project.current_version_id)
        if version is None:
            raise NotFoundError("formal version not found")

        chapter_records = list(
            await self.session.scalars(
                select(ChapterRecord).where(ChapterRecord.project_id == project.id)
            )
        )
        revision_ids = [UUID(value) for value in version.chapter_revisions.values()]
        revision_records = (
            list(
                await self.session.scalars(
                    select(ChapterRevisionRecord).where(ChapterRevisionRecord.id.in_(revision_ids))
                )
            )
            if revision_ids
            else []
        )
        return build_reader_view(
            project.title,
            version,
            chapter_records,
            {str(item.id): item for item in revision_records},
        )

    async def build_manifest(self, project_id: UUID) -> dict[str, Any]:
        project, version, chapters = await self._current(project_id)
        revision_ids = [UUID(value) for value in version.chapter_revisions.values()]
        revisions = (
            {
                item.id: item
                for item in await self.session.scalars(
                    select(ChapterRevisionRecord).where(
                        ChapterRevisionRecord.id.in_(revision_ids)
                    )
                )
            }
            if revision_ids
            else {}
        )
        return build_reader_manifest(
            project.title,
            version,
            chapters,
            revisions,
        )

    async def build_chapter(self, project_id: UUID, chapter_id: UUID) -> dict[str, Any]:
        project = await self.session.get(ProjectRecord, project_id)
        if project is None or project.current_version_id is None:
            raise NotFoundError("project not found")
        version = await self.session.get(StateVersionRecord, project.current_version_id)
        if version is None:
            raise NotFoundError("formal version not found")
        chapter = await self.session.scalar(
            select(ChapterRecord).where(
                ChapterRecord.id == chapter_id,
                ChapterRecord.project_id == project_id,
            )
        )
        if chapter is None:
            raise NotFoundError("formal chapter not found")
        revision_id = version.chapter_revisions.get(str(chapter.id))
        if revision_id is None:
            raise NotFoundError("formal chapter not found")
        revision = await self.session.get(ChapterRevisionRecord, UUID(revision_id))
        if revision is None or revision.chapter_id != chapter.id:
            raise NotFoundError("formal revision not found")
        body_sha256 = hashlib.sha256(revision.body.encode("utf-8")).hexdigest()
        return {
            "project_id": str(project.id),
            "formal_version": version.number,
            "chapter_id": str(chapter.id),
            "revision_id": str(revision.id),
            "ordinal": chapter.display_ordinal or chapter.ordinal,
            "title": (version.chapter_titles or {}).get(str(chapter.id), chapter.title),
            "body": revision.body,
            "body_sha256": body_sha256,
        }

    async def _current(
        self, project_id: UUID
    ) -> tuple[ProjectRecord, StateVersionRecord, list[ChapterRecord]]:
        project = await self.session.get(ProjectRecord, project_id)
        if project is None or project.current_version_id is None:
            raise NotFoundError("project not found")
        version = await self.session.get(StateVersionRecord, project.current_version_id)
        if version is None:
            raise NotFoundError("formal version not found")
        chapters = list(
            await self.session.scalars(
                select(ChapterRecord).where(ChapterRecord.project_id == project.id)
            )
        )
        return project, version, chapters


def build_reader_view(
    project_title: str,
    version: StateVersionRecord,
    chapter_records: list[ChapterRecord],
    revisions: dict[str, ChapterRevisionRecord],
) -> dict[str, Any]:
    chapters: list[dict[str, Any]] = []
    title_map = version.chapter_titles or {}
    ordered = sorted(
        chapter_records,
        key=lambda item: (item.display_ordinal or item.ordinal, item.ordinal),
    )
    for chapter in ordered:
        revision_id = version.chapter_revisions.get(str(chapter.id))
        if revision_id is None:
            continue
        revision = revisions.get(revision_id)
        if revision is None or revision.chapter_id != chapter.id:
            raise NotFoundError("formal revision not found")
        chapters.append(
            {
                "chapter_id": str(chapter.id),
                "revision_id": str(revision.id),
                "ordinal": chapter.display_ordinal or chapter.ordinal,
                "title": title_map.get(str(chapter.id), chapter.title),
                "body": revision.body,
                "body_sha256": hashlib.sha256(revision.body.encode("utf-8")).hexdigest(),
            }
        )
    return {
        "project_id": str(version.project_id),
        "title": project_title,
        "formal_version": version.number,
        "chapters": chapters,
    }


def build_reader_manifest(
    project_title: str,
    version: StateVersionRecord,
    chapter_records: list[ChapterRecord],
    revisions: dict[UUID, ChapterRevisionRecord],
) -> dict[str, Any]:
    title_map = version.chapter_titles or {}
    entries: list[dict[str, Any]] = []
    ordered = sorted(
        chapter_records,
        key=lambda item: (item.display_ordinal or item.ordinal, item.ordinal),
    )
    for chapter in ordered:
        revision_id = version.chapter_revisions.get(str(chapter.id))
        if revision_id is None:
            continue
        revision = revisions.get(UUID(revision_id))
        if revision is None or revision.chapter_id != chapter.id:
            raise NotFoundError("formal revision not found")
        entries.append(
            {
                "chapter_id": str(chapter.id),
                "revision_id": str(revision.id),
                "ordinal": chapter.display_ordinal or chapter.ordinal,
                "title": title_map.get(str(chapter.id), chapter.title),
                "char_count": len(revision.body),
                "body_sha256": hashlib.sha256(revision.body.encode("utf-8")).hexdigest(),
            }
        )
    return {
        "project_id": str(version.project_id),
        "title": project_title,
        "formal_version": version.number,
        "chapters": entries,
    }
