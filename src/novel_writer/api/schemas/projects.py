"""Compact project projections shared through OpenAPI with the management frontend."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ProjectSummary(BaseModel):
    project_id: UUID
    title: str
    current_version: int = Field(ge=1)
    created_at: str


class ArchivedProjectSummary(ProjectSummary):
    archived_at: str


class ChapterSummary(BaseModel):
    chapter_id: UUID
    project_id: UUID
    ordinal: int = Field(ge=1)
    title: str
    current_version: int = Field(ge=1)
    phase: Literal["committed", "pending"]
    next_action: None
    revision_id: UUID | None


class VersionSummary(BaseModel):
    version_id: UUID
    number: int = Field(ge=1)
    parent_id: UUID | None
    parent_number: int | None
    rollback_of_id: UUID | None
    rollback_of_number: int | None
    restart_of_id: UUID | None
    restart_of_number: int | None
    restart_base_number: int | None
    chapter_count: int = Field(ge=0)
    created_at: str
