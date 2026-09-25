from typing import Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.api.dependencies import Session
from novel_writer.db.models import ProjectRecord, StateVersionRecord
from novel_writer.knowledge import embedding
from novel_writer.knowledge.models import KnowledgeIndexRecord
from novel_writer.knowledge.service import KnowledgeService, schema_ready
from novel_writer.services.errors import NotFoundError, WorkflowError

router = APIRouter(prefix="/api/projects/{project_id}/knowledge", tags=["knowledge"])


class KnowledgeQuery(BaseModel):
    query: str = Field(min_length=1, max_length=900)
    version_id: UUID | None = None
    through_chapter: int | None = Field(default=None, ge=1)


async def _version(
    session: AsyncSession,
    project_id: UUID,
    version_id: UUID | None = None,
) -> StateVersionRecord:
    project = await session.get(ProjectRecord, project_id)
    if not project or project.archived_at is not None:
        raise NotFoundError("作品不存在")
    version = await session.get(StateVersionRecord, version_id or project.current_version_id)
    if not version or version.project_id != project_id:
        raise NotFoundError("知识版本不属于本作品")
    return version


@router.get("")
async def status(project_id: UUID, session: Session) -> dict[str, Any]:
    version = await _version(session, project_id)
    return {
        "project_id": str(project_id),
        "version_id": str(version.id),
        **await KnowledgeService(session).status(project_id, version.id),
    }


@router.post("/search")
async def search(project_id: UUID, payload: KnowledgeQuery, session: Session) -> dict[str, Any]:
    version = await _version(session, project_id, payload.version_id)
    service = KnowledgeService(session)
    sources = await service.sources(project_id, version.id)
    try:
        model_key = embedding.identity(service.model_root)
    except (ValueError, OSError):
        model_key = None
    return await service.retrieve(
        project_id,
        version.id,
        sources,
        [payload.query],
        cutoff=payload.through_chapter,
        model_key=model_key,
    )


@router.get("/state")
async def state(
    project_id: UUID,
    session: Session,
    version_id: UUID | None = None,
    entity_id: UUID | None = None,
) -> dict[str, Any]:
    version = await _version(session, project_id, version_id)
    selected = version.state
    if entity_id:
        identifier = str(entity_id)
        selected = {
            key: [
                item
                for item in value
                if isinstance(item, dict)
                and (
                    item.get("id") == identifier
                    or item.get("character_id") == identifier
                    or identifier
                    in (item.get("source_character_id"), item.get("target_character_id"))
                )
            ]
            for key, value in version.state.items()
            if isinstance(value, list)
        }
    return {"version_id": str(version.id), "formal": True, "state": selected}


@router.post("/rebuild", status_code=202)
async def rebuild(project_id: UUID, session: Session) -> dict[str, Any]:
    version = await _version(session, project_id)
    if not await schema_ready(session):
        raise WorkflowError("知识索引数据库迁移尚未执行；当前仍可使用文本检索")
    statement = insert(KnowledgeIndexRecord).values(version_id=version.id, project_id=project_id)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[KnowledgeIndexRecord.version_id],
            set_={"status": "queued", "error_code": None},
        )
    )
    return {"status": "queued", "version_id": str(version.id)}
