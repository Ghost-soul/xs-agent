from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from novel_writer.api.dependencies import IdempotencyKey, Session
from novel_writer.db.models import ProjectDeletionRecord
from novel_writer.services.project_deletion import ProjectDeletionService, deletion_response
from novel_writer.services.project_deletion_files import DeletionRoots

router = APIRouter(prefix="/api", tags=["project-deletion"])


class DeleteProjectRequest(BaseModel):
    confirmed: Literal[True]
    confirmed_title: str = Field(min_length=1, max_length=200)
    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RetryCleanupRequest(BaseModel):
    confirmed: Literal[True]


class ProjectDeletionResponse(BaseModel):
    deletion_id: UUID
    project_id: UUID
    status: Literal["cleanup_pending", "completed"]
    database_deleted: Literal[True]
    counts: dict[str, int]
    cost_summary: dict[str, Any]
    cleanup_error: str | None
    created_at: str
    completed_at: str | None


class ProjectDeletionPreview(BaseModel):
    project_id: UUID
    title: str
    binding_sha256: str
    counts: dict[str, int]
    file_count: int
    estimated_file_bytes: int
    shared_files_preserved: int
    cost_summary: dict[str, Any]
    blockers: list[str]
    exclusions: list[str]


def _service(request: Request) -> ProjectDeletionService:
    settings = request.app.state.settings
    return ProjectDeletionService(
        request.app.state.database,
        DeletionRoots(settings.content_store_root, settings.project_workspace_root),
    )


@router.get("/projects/{project_id}/delete-preview", response_model=ProjectDeletionPreview)
async def preview_project_deletion(
    project_id: UUID,
    request: Request,
    session: Session,
) -> dict[str, Any]:
    return await _service(request).preview(session, project_id)


@router.post("/projects/{project_id}/delete", response_model=ProjectDeletionResponse)
async def delete_project(
    project_id: UUID,
    payload: DeleteProjectRequest,
    request: Request,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    return await _service(request).delete_project(
        project_id,
        payload.confirmed_title,
        payload.binding_sha256,
        idempotency_key,
    )


@router.get("/project-deletions", response_model=list[ProjectDeletionResponse])
async def list_project_deletions(session: Session) -> list[dict[str, Any]]:
    records = await session.scalars(
        select(ProjectDeletionRecord).order_by(ProjectDeletionRecord.created_at.desc())
    )
    return [deletion_response(record) for record in records]


@router.post("/project-deletions/{deletion_id}/cleanup", response_model=ProjectDeletionResponse)
async def retry_project_cleanup(
    deletion_id: UUID,
    payload: RetryCleanupRequest,
    request: Request,
) -> dict[str, Any]:
    return await _service(request).cleanup(deletion_id)
