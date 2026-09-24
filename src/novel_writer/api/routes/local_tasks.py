from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from novel_writer.api.dependencies import IdempotencyKey, Session
from novel_writer.db.models import LocalTaskRecord, ProjectRecord
from novel_writer.services.errors import ConflictError, NotFoundError
from novel_writer.services.local_tasks import (
    LocalTaskKind,
    LocalTaskService,
    local_task_artifact_path,
    task_artifact_metadata,
)

router = APIRouter(prefix="/api", tags=["local-tasks"])


class QueueLocalTaskRequest(BaseModel):
    kind: LocalTaskKind
    confirmed: Literal[True]


class QueueLocalTaskResponse(BaseModel):
    task_id: UUID
    project_id: UUID
    kind: LocalTaskKind
    status: Literal["queued", "running", "completed", "failed"]
    input_sha256: str


@router.post("/projects/{project_id}/local-tasks", status_code=202)
async def queue_local_task(
    project_id: UUID,
    payload: QueueLocalTaskRequest,
    idempotency_key: IdempotencyKey,
    session: Session,
) -> QueueLocalTaskResponse:
    item = await LocalTaskService(session).queue(project_id, payload.kind, idempotency_key)
    return QueueLocalTaskResponse(
        task_id=item.id,
        project_id=project_id,
        kind=payload.kind,
        status=item.status,
        input_sha256=item.input_sha256,
    )


@router.get("/local-tasks/{task_id}/artifact")
async def download_local_task_artifact(
    task_id: UUID,
    request: Request,
    session: Session,
) -> FileResponse:
    item = await session.get(LocalTaskRecord, task_id)
    if item is None:
        raise NotFoundError("local task not found")
    if item.status != "completed" or item.output_sha256 is None:
        raise ConflictError("local task artifact is not ready")
    if item.project_id is None:
        raise ConflictError("local task has no project")
    project = await session.get(ProjectRecord, item.project_id)
    if project is None:
        raise NotFoundError("project not found")
    snapshot = item.payload.get("formal_snapshot")
    snapshot_title = snapshot.get("project_title") if isinstance(snapshot, dict) else None
    filename, media_type, extension = task_artifact_metadata(
        item.kind, str(snapshot_title or project.title)
    )
    path = local_task_artifact_path(
        request.app.state.content_store_root,
        item.id,
        item.output_sha256,
        extension,
    )
    if not path.is_file():
        raise ConflictError("local task artifact file is unavailable")
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
