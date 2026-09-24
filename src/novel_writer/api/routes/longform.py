import hashlib
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from novel_writer.api.dependencies import Session
from novel_writer.db.models import (
    ChapterRecord,
    ChapterRevisionRecord,
    ChapterSummaryRecord,
    LocalTaskRecord,
    ProjectRecord,
    ReadingBookmarkRecord,
    StateVersionRecord,
)
from novel_writer.services.errors import NotFoundError
from novel_writer.services.local_search import LocalSearchService, SearchScope
from novel_writer.services.local_tasks import LocalTaskService
from novel_writer.services.longform_dashboard import LongformDashboardService
from novel_writer.services.reader_view import ReaderViewService
from novel_writer.services.state_health import StateHealthService

router = APIRouter(prefix="/api", tags=["longform-dashboard"])
SearchRebuildIdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=200),
]


class ClosedSearchModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchLocatorResponse(ClosedSearchModel):
    route: str
    chapter_id: str | None = None
    resource_id: str
    session_id: str | None = None
    section: str
    ordinal: int | None = None


class SearchResultResponse(ClosedSearchModel):
    source_kind: str
    source_id: str
    title: str
    snippet: str
    offset: int
    match_field: Literal["title", "text"]
    version_id: UUID | None
    version: int | None
    revision_id: UUID | None
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    route: str
    chapter_id: str | None
    locator: SearchLocatorResponse
    historical: bool
    source_scope: Literal["current", "history"]


class LocalSearchResponse(ClosedSearchModel):
    query: str
    scope: SearchScope
    include_history: bool
    results: list[SearchResultResponse]
    next_cursor: int | None = None
    total: int = 0
    provider_called: Literal[False]
    index_status: Literal["not_built", "rebuilding", "ready", "failed", "stale"]


class SearchStatusResponse(ClosedSearchModel):
    project_id: UUID
    status: Literal["not_built", "rebuilding", "ready", "failed", "stale"]
    document_count: int = Field(ge=0)
    current_document_count: int = Field(ge=0)
    historical_document_count: int = Field(ge=0)
    stale_document_count: int = Field(ge=0)
    last_indexed_at: str | None
    latest_task_id: UUID | None
    latest_task_status: Literal["queued", "running", "completed", "failed"] | None
    latest_task_error_code: str | None
    provider_called: Literal[False]


class SearchRebuildRequest(ClosedSearchModel):
    project_id: UUID
    confirmed: Literal[True]


class SearchRebuildResponse(ClosedSearchModel):
    task_id: UUID
    project_id: UUID
    kind: Literal["search_rebuild"]
    status: Literal["queued", "running", "completed", "failed"]
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_called: Literal[False]


class ReaderManifestChapterResponse(BaseModel):
    chapter_id: UUID
    revision_id: UUID
    ordinal: int
    title: str
    char_count: int
    body_sha256: str


class ReaderManifestResponse(BaseModel):
    project_id: UUID
    title: str
    formal_version: int
    chapters: list[ReaderManifestChapterResponse]


class ReaderChapterResponse(BaseModel):
    project_id: UUID
    formal_version: int
    chapter_id: UUID
    revision_id: UUID
    ordinal: int
    title: str
    body: str
    body_sha256: str


@router.get("/search")
async def local_search(
    project_id: UUID,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    session: Session,
    scope: SearchScope = "all",
    include_history: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[int, Query(ge=0)] = 0,
) -> LocalSearchResponse:
    return LocalSearchResponse.model_validate(await LocalSearchService(session).search(
        project_id,
        q,
        scope=scope,
        include_history=include_history,
        limit=limit,
        cursor=cursor,
    ))


@router.get("/search/status")
async def local_search_status(project_id: UUID, session: Session) -> SearchStatusResponse:
    return SearchStatusResponse.model_validate(
        await LocalSearchService(session).status(project_id)
    )


@router.post("/search/rebuild", status_code=202)
async def rebuild_local_search(
    payload: SearchRebuildRequest,
    idempotency_key: SearchRebuildIdempotencyKey,
    session: Session,
) -> SearchRebuildResponse:
    item = await LocalTaskService(session).queue(
        payload.project_id,
        "search_rebuild",
        idempotency_key,
    )
    return SearchRebuildResponse.model_validate(
        {
            "task_id": item.id,
            "project_id": payload.project_id,
            "kind": item.kind,
            "status": item.status,
            "input_sha256": item.input_sha256,
            "provider_called": False,
        }
    )


@router.get("/projects/{project_id}/longform-dashboard")
async def longform_dashboard(project_id: UUID, session: Session) -> dict[str, Any]:
    return await LongformDashboardService(session).build(project_id)


@router.get("/projects/{project_id}/reader-view")
async def reader_view(project_id: UUID, session: Session) -> dict[str, Any]:
    return await ReaderViewService(session).build(project_id)


@router.get("/projects/{project_id}/reader/manifest")
async def reader_manifest(project_id: UUID, session: Session) -> ReaderManifestResponse:
    return ReaderManifestResponse.model_validate(
        await ReaderViewService(session).build_manifest(project_id)
    )


@router.get(
    "/projects/{project_id}/reader/chapters/{chapter_id}",
    response_model=ReaderChapterResponse,
)
async def reader_chapter(
    project_id: UUID,
    chapter_id: UUID,
    request: Request,
    response: Response,
    session: Session,
) -> ReaderChapterResponse | Response:
    result = ReaderChapterResponse.model_validate(
        await ReaderViewService(session).build_chapter(project_id, chapter_id)
    )
    etag = f'"{result.body_sha256}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return result


class RepairStateHealthRequest(BaseModel):
    base_version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=1000)
    confirmed: Literal[True]


class BookmarkRequest(BaseModel):
    chapter_id: UUID
    revision_id: UUID
    version_number: int = Field(ge=1)
    offset: int = Field(ge=0)
    anchor: str = Field(min_length=1, max_length=500)
    note: str = Field(default="", max_length=500)


class BookmarkResponse(BaseModel):
    bookmark_id: UUID
    chapter_id: UUID
    revision_id: UUID
    version_number: int
    offset: int
    anchor_sha256: str
    note: str
    created_at: str


class BookmarkWriteResponse(BaseModel):
    bookmark_id: UUID
    anchor_sha256: str


class LocalTaskResponse(BaseModel):
    task_id: UUID
    project_id: UUID | None
    kind: str
    status: str
    progress: int
    output_sha256: str | None
    error_code: str | None
    created_at: str
    completed_at: str | None
    unread: bool


@router.get("/local-tasks")
async def list_local_tasks(
    session: Session, project_id: UUID | None = None, limit: int = 50
) -> list[LocalTaskResponse]:
    query = (
        select(LocalTaskRecord)
        .order_by(LocalTaskRecord.created_at.desc())
        .limit(min(limit, 100))
    )
    if project_id is not None:
        query = query.where(LocalTaskRecord.project_id == project_id)
    rows = list(await session.scalars(query))
    return [_local_task_response(item) for item in rows]


@router.post("/local-tasks/{task_id}/read")
async def mark_local_task_read(task_id: UUID, session: Session) -> LocalTaskResponse:
    item = await session.get(LocalTaskRecord, task_id)
    if item is None:
        raise NotFoundError("local task not found")
    item.read_at = datetime.now(UTC)
    await session.flush()
    return _local_task_response(item)


@router.get("/projects/{project_id}/bookmarks")
async def list_bookmarks(project_id: UUID, session: Session) -> list[BookmarkResponse]:
    if await session.get(ProjectRecord, project_id) is None:
        raise NotFoundError("project not found")
    rows = list(
        await session.scalars(
            select(ReadingBookmarkRecord)
            .where(ReadingBookmarkRecord.project_id == project_id)
            .order_by(ReadingBookmarkRecord.created_at.desc())
        )
    )
    return [
        BookmarkResponse.model_validate({
            "bookmark_id": str(item.id),
            "chapter_id": str(item.chapter_id),
            "revision_id": str(item.revision_id),
            "version_number": item.version_number,
            "offset": item.offset,
            "anchor_sha256": item.anchor_sha256,
            "note": item.note,
            "created_at": item.created_at.isoformat(),
        })
        for item in rows
    ]


@router.post("/projects/{project_id}/bookmarks", status_code=201)
async def create_bookmark(
    project_id: UUID, payload: BookmarkRequest, session: Session
) -> BookmarkWriteResponse:
    if await session.get(ProjectRecord, project_id) is None:
        raise NotFoundError("project not found")
    chapter = await session.get(ChapterRecord, payload.chapter_id)
    if chapter is None or chapter.project_id != project_id:
        raise NotFoundError("chapter not found in project")
    revision = await session.get(ChapterRevisionRecord, payload.revision_id)
    if revision is None or revision.chapter_id != payload.chapter_id:
        raise NotFoundError("revision not found in chapter")
    anchor_sha256 = hashlib.sha256(payload.anchor.encode("utf-8")).hexdigest()
    item = await session.scalar(
        select(ReadingBookmarkRecord).where(
            ReadingBookmarkRecord.project_id == project_id,
            ReadingBookmarkRecord.chapter_id == payload.chapter_id,
            ReadingBookmarkRecord.anchor_sha256 == anchor_sha256,
        )
    )
    if item is None:
        item = ReadingBookmarkRecord(
            project_id=project_id,
            chapter_id=payload.chapter_id,
            revision_id=payload.revision_id,
            version_number=payload.version_number,
            offset=payload.offset,
            anchor_sha256=anchor_sha256,
            note=payload.note,
        )
        session.add(item)
    else:
        item.version_number = payload.version_number
        item.revision_id = payload.revision_id
        item.offset = payload.offset
        item.note = payload.note
    await session.flush()
    return BookmarkWriteResponse(bookmark_id=item.id, anchor_sha256=item.anchor_sha256)


def _local_task_response(item: LocalTaskRecord) -> LocalTaskResponse:
    return LocalTaskResponse(
        task_id=item.id,
        project_id=item.project_id,
        kind=item.kind,
        status=item.status,
        progress=item.progress,
        output_sha256=item.output_sha256,
        error_code=item.error_code,
        created_at=item.created_at.isoformat(),
        completed_at=item.completed_at.isoformat() if item.completed_at else None,
        unread=item.read_at is None and item.status in {"completed", "failed"},
    )


@router.get("/projects/{project_id}/state-health")
async def state_health(project_id: UUID, session: Session) -> dict[str, Any]:
    return await StateHealthService(session).inspect(project_id)


@router.post("/projects/{project_id}/state-health/repair")
async def repair_state_health(
    project_id: UUID, payload: RepairStateHealthRequest, session: Session
) -> dict[str, Any]:
    return await StateHealthService(session).apply_safe_repairs(
        project_id, payload.base_version, payload.reason
    )


@router.get("/projects/{project_id}/chapter-summaries")
async def chapter_summaries(
    project_id: UUID,
    session: Session,
    chapter: int | None = None,
) -> dict[str, Any]:
    """Retrieve structured chapter summaries (S3).

    Returns all summaries for the project, or a single chapter if ?chapter=N.
    """
    project = await session.get(ProjectRecord, project_id)
    if project is None:
        raise NotFoundError("project not found")
    version = await session.get(StateVersionRecord, project.current_version_id)
    if version is None:
        raise NotFoundError("current story version not found")
    formal_revision_ids = [UUID(value) for value in version.chapter_revisions.values()]
    if not formal_revision_ids:
        return {"project_id": str(project_id), "count": 0, "summaries": []}
    query = (
        select(ChapterSummaryRecord)
        .where(
            ChapterSummaryRecord.project_id == project_id,
            ChapterSummaryRecord.revision_id.in_(formal_revision_ids),
        )
        .order_by(ChapterSummaryRecord.chapter_number)
    )
    if chapter is not None:
        query = query.where(ChapterSummaryRecord.chapter_number == chapter)
    rows = (await session.execute(query)).scalars().all()
    return {
        "project_id": str(project_id),
        "count": len(rows),
        "summaries": [
            {
                "chapter_number": r.chapter_number,
                "title": r.title,
                "key_events": r.key_events,
                "characters_present": r.characters_present,
                "character_changes": r.character_changes,
                "promises_touched": r.promises_touched,
                "foreshadowings_touched": r.foreshadowings_touched,
                "location": r.location,
                "time_position": r.time_position,
                "word_count": r.word_count,
                "narrative_summary": r.narrative_summary,
                "quality_handoff": r.quality_handoff,
                "revision_id": str(r.revision_id) if r.revision_id else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }
