"""Formal version inspection and author-controlled version commands."""

from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import (
    ApprovalRecord,
    ChapterRecord,
    ChapterRevisionRecord,
    StateDeltaRecord,
    StateVersionRecord,
)
from novel_writer.domain.models import StateDelta, StoryState
from novel_writer.services.errors import ConflictError, NotFoundError, WorkflowError
from novel_writer.services.workflow_state import restart_state_from_current_blueprint


class WorkflowVersionMixin:
    session: AsyncSession

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)

    async def rollback(
        self,
        project_id: UUID,
        target_version: int,
        base_version: int,
        idempotency_key: str,
        reason: str = "作者从版本历史恢复正式状态",
    ) -> dict[str, Any]:
        request_payload = {
            "project_id": str(project_id),
            "target_version": target_version,
            "base_version": base_version,
            "reason": reason,
        }
        cached = await self._idempotent("rollback", idempotency_key, request_payload)
        if cached is not None:
            return dict(cached)
        project = await self._locked_project(project_id)
        current = await self._version(project.current_version_id)
        if current.number != base_version:
            raise ConflictError(
                f"base_version {base_version} is stale; current version is {current.number}"
            )
        target = await self._project_version(project.id, target_version)
        if target.id == current.id:
            raise ConflictError("target version is already current")
        sync = await self._synchronize_formal_dependents(
            project.id,
            dict(target.chapter_revisions),
            "正式版本已回退；旧创作批次已归档，自动运行同步终止。",
        )
        await self._renumber_formal_chapters(
            project.id,
            dict(target.chapter_revisions),
            dict(target.chapter_titles or {}),
        )
        new_version = StateVersionRecord(
            project_id=project.id,
            number=current.number + 1,
            parent_id=current.id,
            parent_number=current.number,
            rollback_of_id=target.id,
            rollback_of_number=target.number,
            state=target.state,
            chapter_revisions=target.chapter_revisions,
            chapter_titles=target.chapter_titles or {},
        )
        self.session.add(new_version)
        await self.session.flush()
        project.current_version_id = new_version.id
        self.session.add(
            ApprovalRecord(
                target_type="version_rollback",
                target_id=new_version.id,
                decision="approved",
                reason=reason,
            )
        )
        response = {
            "project_id": str(project.id),
            "version_id": str(new_version.id),
            "version": new_version.number,
            "rollback_of": target.number,
            "chapter_count": len(target.chapter_revisions),
            **sync,
        }
        await self._save_idempotent("rollback", idempotency_key, response, request_payload)
        return response

    async def restart_story_preview(self, project_id: UUID) -> dict[str, Any]:
        project = await self._project(project_id)
        current = await self._version(project.current_version_id)
        state = StoryState.model_validate(current.state)
        return {
            "project_id": str(project.id),
            "project_title": project.title,
            "current_version": current.number,
            "setting_source_version": current.number,
            "chapter_count_to_clear": len(current.chapter_revisions),
            "scene_count_to_clear": len(state.scenes),
            "event_count_to_clear": len(state.events),
            "timeline_constraint_count_to_clear": len(state.timeline_constraints),
            "disclosure_count_to_clear": len(state.disclosures),
            "reader_promise_count_to_clear": len(state.reader_promises),
            "preserved_character_count": len(state.characters),
            "preserved_world_rule_count": len(state.world_rules),
            "preserved_world_lore_count": len(state.world_lore),
            "preserved_plot_thread_count": len(state.plot_threads),
            "manual_blueprint_review_required": True,
            "provider_called": False,
        }

    async def restart_story(
        self,
        project_id: UUID,
        base_version: int,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        command = f"restart_story:{project_id}"
        request_payload = {
            "project_id": str(project_id),
            "base_version": base_version,
            "reason": reason,
        }
        cached = await self._idempotent(command, idempotency_key, request_payload)
        if cached is not None:
            return dict(cached)
        project = await self._locked_project(project_id)
        current = await self._version(project.current_version_id)
        if current.number != base_version:
            raise ConflictError(
                f"base_version {base_version} is stale; current version is {current.number}"
            )
        current_state = StoryState.model_validate(current.state)
        restarted_state = restart_state_from_current_blueprint(current_state)
        sync = await self._synchronize_formal_dependents(
            project.id,
            {},
            "作者保留当前正式设定重新开书；旧创作批次已归档，自动运行同步终止。",
        )
        await self._renumber_formal_chapters(project.id, {}, {})
        new_version = StateVersionRecord(
            project_id=project.id,
            number=current.number + 1,
            parent_id=current.id,
            parent_number=current.number,
            restart_of_id=current.id,
            restart_of_number=current.number,
            restart_base_number=current.number,
            state=restarted_state.model_dump(mode="json"),
            chapter_revisions={},
            chapter_titles={},
        )
        self.session.add(new_version)
        await self.session.flush()
        project.current_version_id = new_version.id
        self.session.add(
            ApprovalRecord(
                target_type="story_restart",
                target_id=new_version.id,
                decision="approved",
                reason=reason,
            )
        )
        response = {
            "project_id": str(project.id),
            "version_id": str(new_version.id),
            "version": new_version.number,
            "restart_of": current.number,
            "setting_source_version": current.number,
            "chapter_count": 0,
            "manual_blueprint_review_required": True,
            "historical_formal_story_preserved": True,
            "provider_called": False,
            **sync,
        }
        await self._save_idempotent(command, idempotency_key, response, request_payload)
        return response

    async def diff_versions(
        self, project_id: UUID, from_version: int, to_version: int
    ) -> dict[str, Any]:
        before = await self._project_version(project_id, from_version)
        after = await self._project_version(project_id, to_version)
        return {
            "from_version": before.number,
            "to_version": after.number,
            "state_before": before.state,
            "state_after": after.state,
            "chapter_revisions_before": before.chapter_revisions,
            "chapter_revisions_after": after.chapter_revisions,
        }

    async def reader_boundary(
        self, project_id: UUID, version: int, scene_id: UUID
    ) -> dict[str, Any]:
        await self._project(project_id)
        state_version = await self._project_version(project_id, version)
        state = StoryState.model_validate(state_version.state)
        scene = next((item for item in state.scenes if item.id == scene_id), None)
        if scene is None:
            raise NotFoundError("scene not found")
        disclosures = state.reader_disclosures_at(scene_id)
        return {
            "project_id": str(project_id),
            "version": version,
            "scene": scene.model_dump(mode="json"),
            "disclosures": [item.model_dump(mode="json") for item in disclosures],
        }

    async def analyze_setting_impact(
        self,
        project_id: UUID,
        base_version: int,
        proposed_delta: StateDelta,
    ) -> dict[str, Any]:
        project = await self._project(project_id)
        current = await self._version(project.current_version_id)
        if current.number != base_version or proposed_delta.base_version != base_version:
            raise ConflictError(
                f"base_version {base_version} is stale; current version is {current.number}"
            )

        state = StoryState.model_validate(current.state)
        try:
            proposed_delta.apply(state)
        except ValidationError as error:
            raise WorkflowError(f"proposed delta violates story constraints: {error}") from error

        field_mapping = (
            ("add_characters", "characters", "character"),
            ("add_places", "places", "place"),
            ("add_scenes", "scenes", "scene"),
            ("add_events", "events", "event"),
            ("add_timeline_constraints", "timeline_constraints", "timeline_constraint"),
            ("add_relationships", "relationships", "relationship"),
            ("add_world_rules", "world_rules", "world_rule"),
            ("add_world_lore", "world_lore", "world_lore"),
            ("add_beliefs", "beliefs", "belief"),
            ("add_plot_threads", "plot_threads", "plot_thread"),
            ("add_open_questions", "open_questions", "open_question"),
            ("add_foreshadowings", "foreshadowings", "foreshadowing"),
            ("add_narrative_phases", "narrative_phases", "narrative_phase"),
            ("add_plot_history", "plot_history", "plot_history"),
            ("add_story_forces", "story_forces", "story_force"),
            (
                "add_scheduled_developments",
                "scheduled_developments",
                "scheduled_development",
            ),
            ("add_disclosures", "disclosures", "disclosure"),
        )
        changed_entities: list[dict[str, str]] = []
        changed_ids: set[str] = set()
        changed_delta_fields: set[str] = set()
        for delta_field, state_field, entity_type in field_mapping:
            existing = {
                str(item.id): item.model_dump(mode="json")
                for item in getattr(state, state_field)
            }
            for item in getattr(proposed_delta, delta_field):
                item_id = str(item.id)
                if item_id in existing and existing[item_id] != item.model_dump(mode="json"):
                    changed_ids.add(item_id)
                    changed_delta_fields.add(delta_field)
                    changed_entities.append({"type": entity_type, "id": item_id})

        if not changed_entities:
            return {
                "project_id": str(project_id),
                "base_version": base_version,
                "changed_entities": [],
                "impacted_chapters": [],
            }

        revision_rows = (
            await self.session.execute(
                select(
                    ChapterRecord.id,
                    ChapterRecord.ordinal,
                    ChapterRecord.title,
                    ChapterRevisionRecord.id,
                    ChapterRevisionRecord.supersedes_id,
                    StateDeltaRecord.content,
                )
                .join(
                    ChapterRevisionRecord,
                    ChapterRevisionRecord.chapter_id == ChapterRecord.id,
                )
                .join(
                    StateDeltaRecord,
                    StateDeltaRecord.revision_id == ChapterRevisionRecord.id,
                )
                .where(
                    ChapterRecord.project_id == project_id,
                    ChapterRevisionRecord.status == "committed",
                )
            )
        ).all()
        direct_chapter_ids: set[UUID] = set()
        direct_ordinals: list[int] = []
        parent_by_revision = {
            str(revision_id): str(supersedes_id) if supersedes_id else None
            for _chapter_id, _ordinal, _title, revision_id, supersedes_id, _delta in revision_rows
        }
        formal_revision_ids = set(current.chapter_revisions.values())
        pending_lineage = list(formal_revision_ids)
        while pending_lineage:
            revision_id = pending_lineage.pop()
            parent_id = parent_by_revision.get(revision_id)
            if parent_id is not None and parent_id not in formal_revision_ids:
                formal_revision_ids.add(parent_id)
                pending_lineage.append(parent_id)
        for chapter_id, ordinal, _title, revision_id, _parent_id, delta_content in revision_rows:
            if str(revision_id) not in formal_revision_ids:
                continue
            introduced_ids = {
                str(item["id"])
                for field in changed_delta_fields
                for item in delta_content.get(field, [])
                if "id" in item
            }
            if changed_ids & introduced_ids:
                direct_chapter_ids.add(chapter_id)
                direct_ordinals.append(ordinal)

        current_chapter_ids = set(current.chapter_revisions)
        chapters = (
            await self.session.scalars(
                select(ChapterRecord)
                .where(ChapterRecord.project_id == project_id)
                .order_by(ChapterRecord.ordinal)
            )
        ).all()
        first_affected_ordinal = min(direct_ordinals, default=1)
        impacted_chapters = [
            {
                "chapter_id": str(chapter.id),
                "ordinal": chapter.ordinal,
                "title": chapter.title,
                "reason": (
                    "direct_source" if chapter.id in direct_chapter_ids else "downstream_review"
                ),
            }
            for chapter in chapters
            if str(chapter.id) in current_chapter_ids
            and chapter.ordinal >= first_affected_ordinal
        ]
        return {
            "project_id": str(project_id),
            "base_version": base_version,
            "changed_entities": changed_entities,
            "impacted_chapters": impacted_chapters,
        }
