from __future__ import annotations

import json
from collections import defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import (
    AgentGenerationPlanRecord,
    AgentRunRecord,
    ApprovalRecord,
    BaselineGenerationPlanRecord,
    BriefPlanningSessionRecord,
    ChapterBriefRecord,
    ChapterRevisionRecord,
    GenerationBatchRecord,
    NovelAutomationRunRecord,
    ProjectRecord,
    StateVersionRecord,
    WritingSessionRecord,
)
from novel_writer.services.errors import ConflictError, NotFoundError
from novel_writer.services.idempotency import (
    load_idempotent_response,
    save_idempotent_response,
)

_BASE_VERSION_REFERENCES = (
    ("generation_batches", GenerationBatchRecord),
    ("chapter_briefs", ChapterBriefRecord),
    ("brief_planning_sessions", BriefPlanningSessionRecord),
    ("chapter_revisions", ChapterRevisionRecord),
    ("baseline_generation_plans", BaselineGenerationPlanRecord),
    ("agent_runs", AgentRunRecord),
    ("agent_generation_plans", AgentGenerationPlanRecord),
    ("writing_sessions", WritingSessionRecord),
    ("novel_automation_runs", NovelAutomationRunRecord),
)


class VersionMaintenanceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def preview(self, project_id: UUID, keep_latest: int = 10) -> dict[str, Any]:
        project = await self.session.get(ProjectRecord, project_id)
        if project is None:
            raise NotFoundError("project not found")
        versions = await self._versions(project_id)
        return await self._build_preview(project, versions, keep_latest)

    async def prune(
        self,
        project_id: UUID,
        keep_latest: int,
        base_version: int,
        confirmed_title: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        command = f"prune_versions:{project_id}"
        request_payload = {
            "project_id": str(project_id),
            "keep_latest": keep_latest,
            "base_version": base_version,
            "confirmed_title": confirmed_title,
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
        project = await self.session.scalar(
            select(ProjectRecord).where(ProjectRecord.id == project_id).with_for_update()
        )
        if project is None:
            raise NotFoundError("project not found")
        if confirmed_title != project.title:
            raise ConflictError("confirmed project title does not match")
        current = await self.session.get(StateVersionRecord, project.current_version_id)
        if current is None:
            raise NotFoundError("formal version not found")
        if current.number != base_version:
            raise ConflictError(
                f"base_version {base_version} is stale; current version is {current.number}"
            )
        versions = await self._versions(project_id)
        preview = await self._build_preview(project, versions, keep_latest)
        deletable_ids = {UUID(value) for value in preview["deletable_version_ids"]}
        if deletable_ids:
            survivors = [item for item in versions if item.id not in deletable_ids]
            for version in survivors:
                if version.parent_id in deletable_ids:
                    version.parent_id = None
                if version.rollback_of_id in deletable_ids:
                    version.rollback_of_id = None
                if version.restart_of_id in deletable_ids:
                    version.restart_of_id = None
            await self.session.flush()
            await self.session.execute(
                delete(StateVersionRecord).where(StateVersionRecord.id.in_(deletable_ids))
            )
        self.session.add(
            ApprovalRecord(
                target_type="version_retention",
                target_id=project.id,
                decision="approved",
                reason=reason,
            )
        )
        response = {
            "project_id": str(project.id),
            "current_version": current.number,
            "keep_latest": keep_latest,
            "deleted_count": len(deletable_ids),
            "deleted_versions": preview["deletable_versions"],
            "protected_versions": preview["protected_versions"],
            "remaining_count": len(versions) - len(deletable_ids),
            "estimated_reclaimed_bytes": preview["estimated_reclaim_bytes"],
            "revisions_preserved": True,
            "provider_outputs_preserved": True,
            "audit_preserved": True,
            "provider_called": False,
        }
        await save_idempotent_response(
            self.session,
            command,
            idempotency_key,
            response,
            request_payload,
            resource_scope=command,
        )
        return response

    async def _build_preview(
        self,
        project: ProjectRecord,
        versions: list[StateVersionRecord],
        keep_latest: int,
    ) -> dict[str, Any]:
        retained = versions[:keep_latest]
        candidates = versions[keep_latest:]
        candidate_ids = {item.id for item in candidates}
        references: dict[UUID, list[str]] = defaultdict(list)
        if candidate_ids:
            for label, model in _BASE_VERSION_REFERENCES:
                referenced_ids = set(
                    await self.session.scalars(
                        select(model.base_version_id).where(
                            model.base_version_id.in_(candidate_ids)
                        )
                    )
                )
                for version_id in referenced_ids:
                    references[version_id].append(label)
        protected = [item for item in candidates if item.id in references]
        deletable = [item for item in candidates if item.id not in references]
        return {
            "project_id": str(project.id),
            "project_title": project.title,
            "current_version": versions[0].number if versions else 0,
            "keep_latest": keep_latest,
            "total_count": len(versions),
            "retained_versions": [item.number for item in retained],
            "candidate_count": len(candidates),
            "deletable_versions": [item.number for item in deletable],
            "deletable_version_ids": [str(item.id) for item in deletable],
            "protected_versions": [
                {
                    "number": item.number,
                    "reasons": sorted(references[item.id]),
                }
                for item in protected
            ],
            "estimated_reclaim_bytes": sum(_payload_size(item) for item in deletable),
            "revisions_preserved": True,
            "provider_outputs_preserved": True,
            "audit_preserved": True,
            "provider_called": False,
        }

    async def _versions(self, project_id: UUID) -> list[StateVersionRecord]:
        return list(
            await self.session.scalars(
                select(StateVersionRecord)
                .where(StateVersionRecord.project_id == project_id)
                .order_by(StateVersionRecord.number.desc())
            )
        )


def _payload_size(version: StateVersionRecord) -> int:
    payload = {"state": version.state, "chapter_revisions": version.chapter_revisions}
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
