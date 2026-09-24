from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import ProjectCreativePolicyRecord, ProjectRecord
from novel_writer.services.errors import NotFoundError


class CreativePolicy(BaseModel):
    """Historical creative settings retained only for project backup and restore."""

    target_chapters: int = Field(default=1, ge=1, le=10000)
    scene_target_chars: int = Field(default=4000, ge=500, le=50000)
    chapter_min_chars: int = Field(default=4000, ge=500, le=100000)
    chapter_target_chars: int = Field(default=5000, ge=500, le=100000)
    chapter_max_chars: int = Field(default=6000, ge=500, le=100000)
    target_min_chars: int = Field(default=20000, ge=500, le=1000000)
    target_max_chars: int = Field(default=30000, ge=500, le=2000000)
    minimum_follow_read_signal: Literal["weak", "strong"] = "strong"
    stop_on_blocking_risk: bool = True
    target_cost_cny: Decimal = Field(default=Decimal("3"), ge=0, le=1000000)
    soft_budget_cny: Decimal = Field(default=Decimal("5"), ge=0, le=1000000)
    hard_safety_ceiling_cny: Decimal = Field(default=Decimal("20"), ge=0, le=1000000)

    @model_validator(mode="after")
    def validate_relationships(self) -> CreativePolicy:
        if self.target_max_chars < self.target_min_chars:
            raise ValueError("target_max_chars must be at least target_min_chars")
        if not self.chapter_min_chars <= self.chapter_target_chars <= self.chapter_max_chars:
            raise ValueError(
                "chapter_target_chars must fall between chapter_min_chars and chapter_max_chars"
            )
        if self.target_cost_cny > self.soft_budget_cny:
            raise ValueError("target_cost_cny must not exceed soft_budget_cny")
        if self.soft_budget_cny > self.hard_safety_ceiling_cny:
            raise ValueError("soft budget must not exceed the reference budget ceiling")
        return self


class CreativePolicyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, project_id: UUID) -> dict[str, Any]:
        await self._project(project_id)
        record = await self.session.get(ProjectCreativePolicyRecord, project_id)
        if record is None:
            return self._response(project_id, CreativePolicy(), saved=False)
        return self._response(
            project_id,
            CreativePolicy(
                target_chapters=record.target_chapters,
                scene_target_chars=record.scene_target_chars,
                chapter_min_chars=record.chapter_min_chars,
                chapter_target_chars=record.chapter_target_chars,
                chapter_max_chars=record.chapter_max_chars,
                target_min_chars=record.target_min_chars,
                target_max_chars=record.target_max_chars,
                minimum_follow_read_signal=record.minimum_follow_read_signal,
                stop_on_blocking_risk=record.stop_on_blocking_risk,
                target_cost_cny=record.target_cost_cny,
                soft_budget_cny=record.soft_budget_cny,
                hard_safety_ceiling_cny=record.hard_safety_ceiling_cny,
            ),
            saved=True,
        )

    async def save(self, project_id: UUID, policy: CreativePolicy) -> dict[str, Any]:
        await self._project(project_id)
        values = policy.model_dump()
        record = await self.session.get(ProjectCreativePolicyRecord, project_id)
        if record is None:
            record = ProjectCreativePolicyRecord(project_id=project_id, **values)
            self.session.add(record)
        else:
            for field, value in values.items():
                setattr(record, field, value)
        await self.session.flush()
        return self._response(project_id, policy, saved=True)

    async def _project(self, project_id: UUID) -> ProjectRecord:
        project = await self.session.get(ProjectRecord, project_id)
        if project is None:
            raise NotFoundError("project not found")
        return project

    @staticmethod
    def _response(project_id: UUID, policy: CreativePolicy, *, saved: bool) -> dict[str, Any]:
        return {
            "project_id": str(project_id),
            "saved": saved,
            **policy.model_dump(mode="json"),
            "recommended_policy": CreativePolicy().model_dump(mode="json"),
            "immutable_safety_boundaries": [
                "cost_and_external_data_confirmation",
                "formal_merge_confirmation",
                "story_state_transaction_integrity",
                "local_failure_never_externalizes_data",
                "provider_log_redaction",
                "provider_profile_consistency",
            ],
        }
