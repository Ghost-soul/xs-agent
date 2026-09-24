from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import (
    ApprovalRecord,
    ProjectRecord,
    StateVersionRecord,
)
from novel_writer.domain.models import (
    StoryState,
)
from novel_writer.services.blueprint_sections import (
    BlueprintSectionName,
    blueprint_section_response,
    rebuild_story_state_for_blueprint_section,
)
from novel_writer.services.errors import ConflictError, WorkflowError
from novel_writer.services.foreshadowing import validate_foreshadowing_transition
from novel_writer.services.formal_version_sync import (
    invalidate_formal_dependents,
)
from novel_writer.services.workflow_persistence import WorkflowPersistenceMixin


class BlueprintManagementService(WorkflowPersistenceMixin):
    """Manual author edits to formal story data; no generation or candidate adoption."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    session: AsyncSession


    async def blueprint(self, project_id: UUID) -> dict[str, Any]:
        project = await self._project(project_id)
        version = await self._version(project.current_version_id)
        state = StoryState.model_validate(version.state)
        return _blueprint_response(project, version, state)

    async def blueprint_section(
        self, project_id: UUID, section: BlueprintSectionName
    ) -> dict[str, Any]:
        project = await self._project(project_id)
        version = await self._version(project.current_version_id)
        state = StoryState.model_validate(version.state)
        return blueprint_section_response(
            project.id,
            version.number,
            version.id,
            state,
            section,
        )

    async def update_blueprint_section(
        self,
        project_id: UUID,
        section: BlueprintSectionName,
        expected_state_version: int,
        data: dict[str, Any],
        reason: str,
        confirmed: bool,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not confirmed:
            raise WorkflowError("author confirmation is required")
        command = f"update_story_blueprint_section:{project_id}:{section}"
        request_payload = {
            "project_id": str(project_id),
            "section": section,
            "expected_state_version": expected_state_version,
            "data": data,
            "reason": reason,
            "confirmed": confirmed,
        }
        cached = await self._idempotent(command, idempotency_key, request_payload)
        if cached is not None:
            return cached

        project = await self._locked_project(project_id)
        current = await self._version(project.current_version_id)
        if current.number != expected_state_version:
            raise ConflictError("story blueprint state version is stale")
        current_state = StoryState.model_validate(current.state)
        updated = rebuild_story_state_for_blueprint_section(current_state, section, data)
        if section == "foreshadowings":
            _validate_author_foreshadowing_changes(current_state, updated)
        sync = await invalidate_formal_dependents(
            self.session,
            project.id,
            "作者保存了新的正式蓝图分区；旧创作批次已归档，自动运行同步终止。",
        )
        version = StateVersionRecord(
            project_id=project.id,
            number=current.number + 1,
            parent_id=current.id,
            parent_number=current.number,
            state=updated.model_dump(mode="json"),
            chapter_revisions=current.chapter_revisions,
            chapter_titles=current.chapter_titles or {},
        )
        self.session.add(version)
        await self.session.flush()
        project.current_version_id = version.id
        self.session.add(
            ApprovalRecord(
                target_type="story_blueprint_section",
                target_id=project.id,
                decision="approved",
                reason=reason or f"作者确认更新蓝图分区 {section}",
            )
        )
        response = blueprint_section_response(
            project.id,
            version.number,
            version.id,
            updated,
            section,
        )
        response["dependent_sync"] = sync
        await self._save_idempotent(command, idempotency_key, response, request_payload)
        return response

    async def update_blueprint(
        self,
        project_id: UUID,
        base_version: int,
        characters: list[dict[str, Any]] | None,
        relationships: list[dict[str, Any]] | None,
        story_forces: list[dict[str, Any]],
        scheduled_developments: list[dict[str, Any]],
        plot_threads: list[dict[str, Any]],
        story_foundation: dict[str, Any],
        open_questions: list[dict[str, Any]],
        foreshadowings: list[dict[str, Any]] | None,
        narrative_phases: list[dict[str, Any]],
        plot_history: list[dict[str, Any]],
        narrative_position: dict[str, Any],
        world_lore: list[dict[str, Any]],
        world_rules: list[dict[str, Any]],
        reason: str,
        confirmed: bool,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not confirmed:
            raise WorkflowError("author confirmation is required")
        command = f"update_story_blueprint:{project_id}"
        request_payload = {
            "project_id": str(project_id),
            "base_version": base_version,
            "characters": characters,
            "relationships": relationships,
            "story_forces": story_forces,
            "scheduled_developments": scheduled_developments,
            "plot_threads": plot_threads,
            "story_foundation": story_foundation,
            "open_questions": open_questions,
            "foreshadowings": foreshadowings,
            "narrative_phases": narrative_phases,
            "plot_history": plot_history,
            "narrative_position": narrative_position,
            "world_lore": world_lore,
            "world_rules": world_rules,
            "reason": reason,
            "confirmed": confirmed,
        }
        cached = await self._idempotent(command, idempotency_key, request_payload)
        if cached is not None:
            return cached
        project = await self._locked_project(project_id)
        current = await self._version(project.current_version_id)
        if current.number != base_version:
            raise ConflictError("story blueprint base version is stale")
        state = StoryState.model_validate(current.state)
        state_data = state.model_dump(mode="json")
        state_data.update(
            {
                "story_forces": story_forces,
                "scheduled_developments": scheduled_developments,
                "plot_threads": plot_threads,
                "story_foundation": story_foundation,
                "open_questions": open_questions,
                "narrative_phases": narrative_phases,
                "plot_history": plot_history,
                "narrative_position": narrative_position,
                "world_lore": world_lore,
                "world_rules": world_rules,
            }
        )
        if foreshadowings is not None:
            state_data["foreshadowings"] = foreshadowings
        if characters is not None:
            submitted_ids = {str(item.get("id")) for item in characters}
            preserved_retired = [
                item.model_copy(update={"library_status": "retired"}).model_dump(mode="json")
                for item in state.characters
                if str(item.id) not in submitted_ids
            ]
            state_data["characters"] = [*characters, *preserved_retired]
        if relationships is not None:
            state_data["relationships"] = relationships
        updated = StoryState.model_validate(state_data)
        if foreshadowings is not None:
            _validate_author_foreshadowing_changes(state, updated)
        sync = await invalidate_formal_dependents(
            self.session,
            project.id,
            "作者保存了新的正式蓝图；旧创作批次已归档，自动运行同步终止。",
        )
        version = StateVersionRecord(
            project_id=project.id,
            number=current.number + 1,
            parent_id=current.id,
            parent_number=current.number,
            state=updated.model_dump(mode="json"),
            chapter_revisions=current.chapter_revisions,
            chapter_titles=current.chapter_titles or {},
        )
        self.session.add(version)
        await self.session.flush()
        project.current_version_id = version.id
        self.session.add(
            ApprovalRecord(
                target_type="story_blueprint",
                target_id=project.id,
                decision="approved",
                reason=reason or "作者确认更新世界观与大纲",
            )
        )
        response = {**_blueprint_response(project, version, updated), **sync}
        await self._save_idempotent(command, idempotency_key, response, request_payload)
        return response



def _validate_author_foreshadowing_changes(before: StoryState, after: StoryState) -> None:
    before_by_id = {item.id: item for item in before.foreshadowings}
    after_by_id = {item.id: item for item in after.foreshadowings}
    removed = set(before_by_id) - set(after_by_id)
    if removed:
        raise ConflictError("formal foreshadowing cannot be deleted; mark it abandoned instead")
    for item_id, current in before_by_id.items():
        submitted = after_by_id[item_id]
        if submitted.lifecycle_events != current.lifecycle_events:
            raise ConflictError("historical foreshadowing lifecycle events are immutable")
        if submitted.status != current.status:
            try:
                validate_foreshadowing_transition(
                    current.status,
                    submitted.status,
                    actor="author",
                )
            except ValueError as error:
                raise ConflictError(str(error)) from error
            if submitted.status == "fulfilled" and not submitted.fulfilled_evidence:
                raise ConflictError("fulfilled foreshadowing requires formal body evidence")


def _blueprint_response(
    project: ProjectRecord, version: StateVersionRecord, state: StoryState
) -> dict[str, Any]:
    return {
        "project_id": str(project.id),
        "version": version.number,
        "version_id": str(version.id),
        "characters": [
            item.model_dump(mode="json")
            for item in state.characters
            if item.library_status == "active"
        ],
        "relationships": [item.model_dump(mode="json") for item in state.relationships],
        "story_forces": [item.model_dump(mode="json") for item in state.story_forces],
        "scheduled_developments": [
            item.model_dump(mode="json") for item in state.scheduled_developments
        ],
        "plot_threads": [item.model_dump(mode="json") for item in state.plot_threads],
        "story_foundation": state.story_foundation.model_dump(mode="json"),
        "open_questions": [item.model_dump(mode="json") for item in state.open_questions],
        "foreshadowings": [item.model_dump(mode="json") for item in state.foreshadowings],
        "narrative_phases": [item.model_dump(mode="json") for item in state.narrative_phases],
        "plot_history": [item.model_dump(mode="json") for item in state.plot_history],
        "narrative_position": state.narrative_position.model_dump(mode="json"),
        "world_lore": [item.model_dump(mode="json") for item in state.world_lore],
        "world_rules": [item.model_dump(mode="json") for item in state.world_rules],
    }


