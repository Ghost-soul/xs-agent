from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import (
    AgentCallRecord,
    ApprovalRecord,
    ChapterRecord,
    NovelAutomationRunRecord,
    ProjectRecord,
    ProjectStyleProfileRecord,
    StateVersionRecord,
    WritingSessionRecord,
)
from novel_writer.domain.character import Character
from novel_writer.domain.models import (
    StateDelta,
    StoryState,
)
from novel_writer.domain.world import WorldLoreEntry
from novel_writer.services.canonical_json import canonical_json_sha256
from novel_writer.services.errors import (
    ConflictError,
    NotFoundError,
    WorkflowError,
)
from novel_writer.services.formal_version_sync import invalidate_generation
from novel_writer.services.style_profiles import validate_project_card_selection
from novel_writer.services.workflow_persistence import WorkflowPersistenceMixin
from novel_writer.services.workflow_state import (
    restart_state_from_current_blueprint,
    without_chapter,
)
from novel_writer.services.workflow_state import (
    truncate_story_state as _truncate_story_state,
)
from novel_writer.services.workflow_versions import WorkflowVersionMixin

_without_chapter = without_chapter
_restart_state_from_current_blueprint = restart_state_from_current_blueprint



class WorkflowService(
    WorkflowVersionMixin,
    WorkflowPersistenceMixin,
):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_project(
        self,
        title: str,
        idempotency_key: str,
        *,
        genre: str = "",
        genre_card_id: str | None = None,
        secondary_genre_card_ids: tuple[str, ...] = (),
        target_chapters: int | None = None,
        world_summary: str = "",
        character_names: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        request_payload = {
            "title": title,
            "genre": genre,
            "genre_card_id": genre_card_id,
            "secondary_genre_card_ids": list(secondary_genre_card_ids),
            "target_chapters": target_chapters,
            "world_summary": world_summary,
            "character_names": list(character_names),
        }
        cached = await self._idempotent("create_project", idempotency_key, request_payload)
        if cached is not None:
            return cached

        normalized_genre_card_id: str | None = None
        normalized_secondary_genre_ids: tuple[str, ...] = ()
        if genre_card_id is not None:
            normalized_genre_card_id, normalized_secondary_genre_ids = (
                validate_project_card_selection(genre_card_id, secondary_genre_card_ids)
            )
        elif secondary_genre_card_ids:
            raise WorkflowError("副题材卡必须与主题材卡一起指定")
        project = ProjectRecord(title=title)
        self.session.add(project)
        await self.session.flush()
        characters = tuple(
            Character(name=name.strip()) for name in character_names if name.strip()
        )
        world_lore = (
            WorldLoreEntry(
                category="world_structure",
                name="初始设定",
                summary=world_summary.strip(),
                source="author",
            ),
        ) if world_summary.strip() else ()
        state = StoryState(characters=characters, world_lore=world_lore)
        version = StateVersionRecord(
            project_id=project.id,
            number=1,
            state=state.model_dump(mode="json"),
            chapter_revisions={},
            chapter_titles={},
        )
        self.session.add(version)
        await self.session.flush()
        project.current_version_id = version.id
        if normalized_genre_card_id is not None:
            self.session.add(
                ProjectStyleProfileRecord(
                    project_id=project.id,
                    selection_mode="specified",
                    genre_id=normalized_genre_card_id,
                    secondary_genre_ids=list(normalized_secondary_genre_ids),
                    assets=[],
                )
            )
        response = {
            "project_id": str(project.id),
            "version_id": str(version.id),
            "version": 1,
            "setup": {
                "genre": genre,
                "genre_card_id": normalized_genre_card_id,
                "secondary_genre_card_ids": list(normalized_secondary_genre_ids),
                "target_chapters": target_chapters,
                "character_count": len(characters),
                "world_initialized": bool(world_lore),
            },
        }
        await self._save_idempotent(
            "create_project", idempotency_key, response, request_payload
        )
        return response

    async def create_chapter(
        self,
        project_id: UUID,
        ordinal: int,
        title: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request_payload = {
            "project_id": str(project_id),
            "ordinal": ordinal,
            "title": title,
        }
        cached = await self._idempotent("create_chapter", idempotency_key, request_payload)
        if cached is not None:
            return cached
        await self._project(project_id)
        physical_ordinal = (
            int(
                await self.session.scalar(
                    select(func.coalesce(func.max(ChapterRecord.ordinal), 0)).where(
                        ChapterRecord.project_id == project_id
                    )
                )
                or 0
            )
            + 1
        )
        chapter = ChapterRecord(
            project_id=project_id,
            ordinal=physical_ordinal,
            display_ordinal=ordinal,
            title=title,
        )
        self.session.add(chapter)
        await self.session.flush()
        response = {"chapter_id": str(chapter.id), "ordinal": ordinal, "title": title}
        await self._save_idempotent(
            "create_chapter", idempotency_key, response, request_payload
        )
        return response

    async def remove_chapter(
        self,
        chapter_id: UUID,
        reason: str,
        idempotency_key: str,
        *,
        base_version: int | None = None,
        preview_sha256: str | None = None,
    ) -> dict[str, Any]:
        command = f"remove_chapter:{chapter_id}"
        request_payload = {
            "chapter_id": str(chapter_id),
            "reason": reason,
            "base_version": base_version,
            "preview_sha256": preview_sha256,
        }
        cached = await self._idempotent(command, idempotency_key, request_payload)
        if cached is not None:
            return cached
        chapter = await self._chapter(chapter_id)
        project = await self._locked_project(chapter.project_id)
        current = await self._version(project.current_version_id)
        if base_version is not None or preview_sha256 is not None:
            if base_version is None or preview_sha256 is None:
                raise ConflictError("截断必须同时提交预览版本和 binding SHA")
            if base_version != current.number:
                raise ConflictError("截断预览基于旧正式版本，请重新预览")
            expected = await self._truncate_preview_binding(project.id, chapter, current)
            if expected["preview_sha256"] != preview_sha256:
                raise ConflictError("截断范围或活动状态已变化，请重新预览")
        revision_map = dict(current.chapter_revisions)
        if str(chapter.id) not in revision_map:
            if chapter.current_revision_id is None:
                await self.session.delete(chapter)
                response = {
                    "project_id": str(project.id),
                    "chapter_id": str(chapter.id),
                    "removed": True,
                    "version": current.number,
                    "recoverable": False,
                }
                await self._save_idempotent(
                    command, idempotency_key, response, request_payload
                )
                return response
            raise ConflictError("chapter is not part of the current formal version")

        cutoff_ordinal = chapter.display_ordinal or chapter.ordinal
        formal_chapters = list(
            await self.session.scalars(
                select(ChapterRecord).where(
                    ChapterRecord.project_id == project.id,
                    ChapterRecord.id.in_([UUID(value) for value in revision_map]),
                )
            )
        )
        formal_chapters.sort(key=lambda item: (item.display_ordinal or item.ordinal, item.ordinal))
        retained_chapters = [
            item
            for item in formal_chapters
            if (item.display_ordinal or item.ordinal) < cutoff_ordinal
        ]
        removed_chapters = [
            item
            for item in formal_chapters
            if (item.display_ordinal or item.ordinal) >= cutoff_ordinal
        ]

        selected_revision = await self._revision(UUID(revision_map[str(chapter.id)]))
        root_revision = await self._root_revision(selected_revision)
        base = await self._version(root_revision.base_version_id)
        retained_deltas: list[StateDelta] = []
        for retained in retained_chapters:
            if str(retained.id) in base.chapter_revisions:
                continue
            retained_revision = await self._revision(UUID(revision_map[str(retained.id)]))
            retained_root = await self._root_revision(retained_revision)
            retained_delta = await self._delta_for_revision(retained_root.id)
            retained_deltas.append(StateDelta.model_validate(retained_delta.content))

        state = _truncate_story_state(
            StoryState.model_validate(base.state),
            StoryState.model_validate(current.state),
            retained_deltas,
            cutoff_ordinal,
        )
        revision_map = {
            str(item.id): current.chapter_revisions[str(item.id)] for item in retained_chapters
        }
        sync = await self._synchronize_formal_dependents(
            project.id,
            revision_map,
            "正式章节被截断；旧创作批次已归档，自动运行同步终止。",
        )
        current_titles = current.chapter_titles or {}
        title_map = {chapter_id: current_titles.get(chapter_id, "") for chapter_id in revision_map}
        await self._renumber_formal_chapters(project.id, revision_map, title_map)
        version = StateVersionRecord(
            project_id=project.id,
            number=current.number + 1,
            parent_id=current.id,
            parent_number=current.number,
            state=state.model_dump(mode="json"),
            chapter_revisions=revision_map,
            chapter_titles=title_map,
        )
        self.session.add(version)
        await self.session.flush()
        project.current_version_id = version.id
        self.session.add(
            ApprovalRecord(
                target_type="chapter_removal",
                target_id=chapter.id,
                decision="approved",
                reason=reason,
            )
        )
        response = {
            "project_id": str(project.id),
            "chapter_id": str(chapter.id),
            "removed": True,
            "version": version.number,
            "version_id": str(version.id),
            "recoverable": True,
            "removed_chapter_count": len(removed_chapters),
            "removed_chapter_ids": [str(item.id) for item in removed_chapters],
            "state_restored_from_version": base.number,
            **sync,
        }
        await self._save_idempotent(command, idempotency_key, response, request_payload)
        return response

    async def truncate_chapter_preview(self, chapter_id: UUID) -> dict[str, Any]:
        chapter = await self._chapter(chapter_id)
        project = await self._project(chapter.project_id)
        current = await self._version(project.current_version_id)
        binding = await self._truncate_preview_binding(project.id, chapter, current)
        return {
            "action": "truncate_chapter",
            "project_id": str(project.id),
            "chapter_id": str(chapter.id),
            "chapter_title": chapter.title,
            "base_version": current.number,
            "preview_sha256": binding["preview_sha256"],
            "affected_chapter_ids": binding["affected_chapter_ids"],
            "affected_chapter_count": len(binding["affected_chapter_ids"]),
            "formal_version_change": {"from": current.number, "to": current.number + 1},
            "recoverable": True,
            "sessions_archived": binding["sessions_archived"],
            "runs_stopped": binding["runs_stopped"],
        }

    async def archive_project_preview(self, project_id: UUID) -> dict[str, Any]:
        project = await self._project(project_id)
        current = await self._version(project.current_version_id)
        sessions = list(
            await self.session.scalars(
                select(WritingSessionRecord).where(
                    WritingSessionRecord.project_id == project.id,
                    WritingSessionRecord.archived_at.is_(None),
                )
            )
        )
        runs = list(
            await self.session.scalars(
                select(NovelAutomationRunRecord).where(
                    NovelAutomationRunRecord.project_id == project.id,
                    NovelAutomationRunRecord.status.notin_(("completed", "cancelled")),
                )
            )
        )
        return {
            "action": "archive_project",
            "project_id": str(project.id),
            "title": project.title,
            "formal_version": current.number,
            "chapter_count": len(current.chapter_revisions),
            "sessions_archived": len(sessions),
            "runs_stopped": len(runs),
            "recoverable": True,
            "formal_story_unchanged": True,
        }

    async def _truncate_preview_binding(
        self, project_id: UUID, chapter: ChapterRecord, current: StateVersionRecord
    ) -> dict[str, Any]:
        chapter_ids = [
            str(item.id)
            for item in list(
                await self.session.scalars(
                    select(ChapterRecord).where(
                        ChapterRecord.project_id == project_id,
                        ChapterRecord.id.in_([UUID(value) for value in current.chapter_revisions]),
                    )
                )
            )
            if (item.display_ordinal or item.ordinal)
            >= (chapter.display_ordinal or chapter.ordinal)
        ]
        session_count = int(
            await self.session.scalar(
                select(func.count(WritingSessionRecord.id)).where(
                    WritingSessionRecord.project_id == project_id,
                    WritingSessionRecord.archived_at.is_(None),
                )
            )
            or 0
        )
        run_count = int(
            await self.session.scalar(
                select(func.count(NovelAutomationRunRecord.id)).where(
                    NovelAutomationRunRecord.project_id == project_id,
                    NovelAutomationRunRecord.status.notin_(("completed", "cancelled")),
                )
            )
            or 0
        )
        payload = {
            "schema_version": "truncate-preview-v1",
            "project_id": str(project_id),
            "chapter_id": str(chapter.id),
            "base_version": current.number,
            "affected_chapter_ids": sorted(chapter_ids),
            "sessions_archived": session_count,
            "runs_stopped": run_count,
        }
        return {
            **payload,
            "preview_sha256": canonical_json_sha256(payload),
        }



    async def archive_project(
        self, project_id: UUID, confirmed_title: str, idempotency_key: str
    ) -> dict[str, Any]:
        command = f"archive_project:{project_id}"
        request_payload = {
            "project_id": str(project_id),
            "confirmed_title": confirmed_title,
        }
        cached = await self._idempotent(command, idempotency_key, request_payload)
        if cached is not None:
            return cached
        project = await self._locked_project(project_id)
        if project.archived_at is not None:
            raise NotFoundError("project not found")
        if confirmed_title.strip() != project.title:
            raise WorkflowError("输入的作品名称与当前作品不一致")
        sessions = list(
            (
                await self.session.scalars(
                    select(WritingSessionRecord)
                    .where(
                        WritingSessionRecord.project_id == project.id,
                        WritingSessionRecord.archived_at.is_(None),
                    )
                    .with_for_update()
                )
            ).all()
        )
        if any(item.status == "merging" for item in sessions):
            raise ConflictError("作品仍有创作批次正在合并，暂时不能归档")
        session_ids = [item.id for item in sessions]
        if session_ids:
            executing_call = await self.session.scalar(
                select(AgentCallRecord.id).where(
                    AgentCallRecord.session_id.in_(session_ids),
                    AgentCallRecord.status == "executing",
                )
            )
            if executing_call is not None:
                raise ConflictError("作品仍有模型请求正在执行，暂时不能归档")
        runs = list(
            (
                await self.session.scalars(
                    select(NovelAutomationRunRecord)
                    .where(
                        NovelAutomationRunRecord.project_id == project.id,
                        NovelAutomationRunRecord.status.notin_(("completed", "cancelled")),
                    )
                    .with_for_update()
                )
            ).all()
        )
        archived_at = datetime.now(UTC)
        await invalidate_generation(self.session, project_id, "作品已归档")
        project.archived_at = archived_at
        for writing_session in sessions:
            writing_session.archived_at = archived_at
        cancelled_run_ids: list[str] = []
        for run in runs:
            state = dict(run.state)
            state.update(
                {
                    "status": "cancelled",
                    "pause_reason": "所属作品已由作者归档；自动运行已安全终止。",
                }
            )
            run.status = "cancelled"
            run.state = state
            cancelled_run_ids.append(str(run.id))
        self.session.add(
            ApprovalRecord(
                target_type="project_archive",
                target_id=project.id,
                decision="approved",
                reason="作者输入完整作品名并确认归档",
            )
        )
        response = {
            "project_id": str(project.id),
            "archived": True,
            "formal_story_unchanged": True,
            "audit_preserved": True,
            "archived_session_count": len(sessions),
            "cancelled_run_count": len(runs),
            "cancelled_run_ids": cancelled_run_ids,
        }
        await self._save_idempotent(command, idempotency_key, response, request_payload)
        return response

    async def restore_archived_project(
        self, project_id: UUID, confirmed_title: str, idempotency_key: str
    ) -> dict[str, Any]:
        command = f"restore_archived_project:{project_id}"
        request_payload = {
            "project_id": str(project_id),
            "confirmed_title": confirmed_title,
        }
        cached = await self._idempotent(command, idempotency_key, request_payload)
        if cached is not None:
            return cached
        project = await self._locked_project(project_id)
        if project.archived_at is None:
            raise ConflictError("project is not archived")
        if confirmed_title.strip() != project.title:
            raise WorkflowError("输入的作品名称与归档作品不一致")
        project.archived_at = None
        self.session.add(
            ApprovalRecord(
                target_type="project_restore",
                target_id=project.id,
                decision="approved",
                reason="作者输入完整作品名并确认恢复归档作品；历史会话保持归档",
            )
        )
        response = {
            "project_id": str(project.id),
            "restored": True,
            "formal_story_unchanged": True,
            "historical_sessions_unchanged": True,
        }
        await self._save_idempotent(command, idempotency_key, response, request_payload)
        return response
