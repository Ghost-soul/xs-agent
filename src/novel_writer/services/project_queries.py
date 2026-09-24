"""Read-only project and formal chapter projections."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import ChapterRecord, ProjectRecord, StateVersionRecord
from novel_writer.services.errors import NotFoundError


class ProjectQueryService:
    """Read projections without loading full StoryState or invoking write workflows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _project(self, project_id: UUID) -> ProjectRecord:
        project = await self.session.get(ProjectRecord, project_id)
        if project is None:
            raise NotFoundError("project not found")
        return project

    async def list_chapters(self, project_id: UUID) -> list[dict[str, Any]]:
        project = await self._project(project_id)
        current = (
            await self.session.execute(
                select(StateVersionRecord.number, StateVersionRecord.chapter_revisions)
                .where(StateVersionRecord.id == project.current_version_id)
            )
        ).one_or_none()
        if current is None:
            raise NotFoundError("state version not found")
        formal_ids = set(current.chapter_revisions)
        chapters = list(
            await self.session.scalars(
                select(ChapterRecord).where(ChapterRecord.project_id == project_id)
            )
        )
        visible = [
            item
            for item in chapters
            if item.display_ordinal is not None
            and (item.current_revision_id is None or str(item.id) in formal_ids)
        ]
        visible.sort(key=lambda item: (item.display_ordinal or item.ordinal, item.ordinal))
        return [
            {
                "chapter_id": str(item.id),
                "project_id": str(project.id),
                "ordinal": item.display_ordinal or item.ordinal,
                "title": item.title,
                "current_version": current.number,
                "phase": "committed" if item.current_revision_id else "pending",
                "next_action": None,
                "revision_id": str(item.current_revision_id)
                if item.current_revision_id
                else None,
            }
            for item in visible
        ]

    async def list_versions(self, project_id: UUID) -> list[dict[str, Any]]:
        await self._project(project_id)
        versions = (
            await self.session.execute(
                select(
                    StateVersionRecord.id,
                    StateVersionRecord.number,
                    StateVersionRecord.parent_id,
                    StateVersionRecord.parent_number,
                    StateVersionRecord.rollback_of_id,
                    StateVersionRecord.rollback_of_number,
                    StateVersionRecord.restart_of_id,
                    StateVersionRecord.restart_of_number,
                    StateVersionRecord.restart_base_number,
                    StateVersionRecord.chapter_revisions,
                    StateVersionRecord.created_at,
                )
                .where(StateVersionRecord.project_id == project_id)
                .order_by(StateVersionRecord.number.desc())
            )
        ).all()
        return [
            {
                "version_id": str(version.id),
                "number": version.number,
                "parent_id": str(version.parent_id) if version.parent_id else None,
                "parent_number": version.parent_number,
                "rollback_of_id": str(version.rollback_of_id)
                if version.rollback_of_id
                else None,
                "rollback_of_number": version.rollback_of_number,
                "restart_of_id": str(version.restart_of_id)
                if version.restart_of_id
                else None,
                "restart_of_number": version.restart_of_number,
                "restart_base_number": version.restart_base_number,
                "chapter_count": len(version.chapter_revisions),
                "created_at": version.created_at.isoformat(),
            }
            for version in versions
        ]


    async def list_projects(self) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                select(ProjectRecord, StateVersionRecord.number)
                .outerjoin(
                    StateVersionRecord,
                    StateVersionRecord.id == ProjectRecord.current_version_id,
                )
                .where(ProjectRecord.archived_at.is_(None))
                .order_by(ProjectRecord.created_at, ProjectRecord.id)
            )
        ).all()
        result: list[dict[str, Any]] = []
        for project, current_version in rows:
            if current_version is None:
                raise NotFoundError("state version not found")
            result.append(
                {
                    "project_id": str(project.id),
                    "title": project.title,
                    "current_version": current_version,
                    "created_at": project.created_at.isoformat(),
                }
            )
        return result

    async def list_archived_projects(self) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                select(ProjectRecord, StateVersionRecord.number)
                .outerjoin(
                    StateVersionRecord,
                    StateVersionRecord.id == ProjectRecord.current_version_id,
                )
                .where(ProjectRecord.archived_at.is_not(None))
                .order_by(ProjectRecord.archived_at.desc(), ProjectRecord.id)
            )
        ).all()
        result: list[dict[str, Any]] = []
        for project, current_version in rows:
            if current_version is None:
                raise NotFoundError("state version not found")
            result.append(
                {
                    "project_id": str(project.id),
                    "title": project.title,
                    "current_version": current_version,
                    "created_at": project.created_at.isoformat(),
                    "archived_at": project.archived_at.isoformat()
                    if project.archived_at is not None
                    else None,
                }
            )
        return result

