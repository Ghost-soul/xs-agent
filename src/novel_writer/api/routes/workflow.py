from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from novel_writer.api.dependencies import IdempotencyKey, Session
from novel_writer.api.schemas.projects import (
    ArchivedProjectSummary,
    ChapterSummary,
    ProjectSummary,
    VersionSummary,
)
from novel_writer.db.models import ProjectSetupDraftRecord
from novel_writer.domain.models import StateDelta
from novel_writer.services.errors import ConflictError, NotFoundError
from novel_writer.services.idempotency import (
    load_idempotent_response,
    save_idempotent_response,
)
from novel_writer.services.import_previews import (
    ImportPreviewBinding,
    ImportPreviewCommitService,
)
from novel_writer.services.project_queries import ProjectQueryService
from novel_writer.services.style_profiles import StyleProfileService
from novel_writer.services.version_maintenance import VersionMaintenanceService
from novel_writer.services.workflow import WorkflowService

router = APIRouter(prefix="/api", tags=["workflow"])


class CreateProjectRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    genre: str = Field(default="", max_length=80)
    genre_card_id: str | None = Field(default=None, max_length=80)
    secondary_genre_card_ids: tuple[str, ...] = ()
    target_chapters: int | None = Field(default=None, ge=1, le=10000)
    world_summary: str = Field(default="", max_length=5000)
    character_names: tuple[str, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def validate_genre_cards(self) -> "CreateProjectRequest":
        if self.genre_card_id is not None:
            self.genre_card_id = self.genre_card_id.strip() or None
        self.secondary_genre_card_ids = tuple(
            card_id.strip() for card_id in self.secondary_genre_card_ids
        )
        if any(not card_id for card_id in self.secondary_genre_card_ids):
            raise ValueError("副题材卡 ID 不能为空")
        if self.secondary_genre_card_ids and self.genre_card_id is None:
            raise ValueError("副题材卡必须与主题材卡一起指定")
        if len(set((self.genre_card_id, *self.secondary_genre_card_ids))) != (
            1 + len(self.secondary_genre_card_ids)
        ):
            raise ValueError("主题材卡和副题材卡不能重复")
        return self


class ProjectSetupImportBinding(BaseModel):
    preview_id: UUID
    upload_sha256: str = Field(min_length=64, max_length=64)
    chapter_manifest_sha256: str = Field(min_length=64, max_length=64)
    selected_encoding: Literal["utf-8", "gb18030", "utf-16le", "utf-16be"]
    parser_version: str = Field(min_length=1, max_length=80)


class ProjectSetupPayload(BaseModel):
    mode: Literal["blank", "import"] = "blank"
    title: str = Field(default="", max_length=200)
    genre: str = Field(default="", max_length=80)
    genre_selection_mode: Literal["automatic", "unselected", "specified"] = "unselected"
    genre_card_id: str | None = Field(default=None, max_length=80)
    secondary_genre_card_ids: tuple[str, ...] = ()
    target_chapters: int | None = Field(default=None, ge=1, le=10000)
    chapter_min_characters: int = Field(default=4000, ge=500, le=50000)
    chapter_target_characters: int = Field(default=5000, ge=500, le=50000)
    chapter_max_characters: int = Field(default=6500, ge=500, le=50000)
    world_summary: str = Field(default="", max_length=5000)
    character_names: tuple[str, ...] = Field(default=(), max_length=20)
    import_filename: str = Field(default="", max_length=300)
    import_encoding: str = Field(default="auto", max_length=30)
    import_preview: ProjectSetupImportBinding | None = None
    step: int = Field(default=1, ge=1, le=6)

    @model_validator(mode="after")
    def validate_genre_selection(self) -> "ProjectSetupPayload":
        self.genre = self.genre.strip()
        if self.genre_selection_mode == "automatic":
            self.genre_selection_mode = "unselected"
        if self.genre_selection_mode == "specified" and not (self.genre_card_id or "").strip():
            raise ValueError("指定题材卡模式必须选择题材卡")
        if self.genre_selection_mode == "unselected" and self.genre_card_id is not None:
            raise ValueError("未选择题材卡时不能携带指定题材卡")
        if self.genre_card_id is not None:
            self.genre_card_id = self.genre_card_id.strip()
        self.secondary_genre_card_ids = tuple(
            card_id.strip() for card_id in self.secondary_genre_card_ids
        )
        if any(not card_id for card_id in self.secondary_genre_card_ids):
            raise ValueError("副题材卡 ID 不能为空")
        if self.genre_selection_mode == "unselected" and self.secondary_genre_card_ids:
            raise ValueError("未选择题材卡时不能携带副题材卡")
        selected_ids = (self.genre_card_id, *self.secondary_genre_card_ids)
        if self.genre_selection_mode == "specified" and len(set(selected_ids)) != len(
            selected_ids
        ):
            raise ValueError("主题材卡和副题材卡不能重复")
        return self


class SaveProjectSetupDraftRequest(BaseModel):
    payload: ProjectSetupPayload
    expected_updated_at: str | None = None


class FinalizeProjectSetupDraftRequest(BaseModel):
    confirmed: Literal[True]
    import_binding: ProjectSetupImportBinding | None = None


class ProjectSetupDraftResponse(BaseModel):
    draft_id: UUID
    schema_version: Literal["project-setup-draft-v1"]
    title: str
    payload: ProjectSetupPayload
    status: Literal["draft", "completed"]
    finalized_project_id: UUID | None
    updated_at: str


class ProjectSetupFinalizeResponse(BaseModel):
    project_id: UUID
    setup_draft_id: UUID
    mode: Literal["blank", "import"]
    preview_id: UUID | None = None


class CreateChapterRequest(BaseModel):
    ordinal: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)


class ArchiveProjectRequest(BaseModel):
    confirmed: Literal[True]
    confirmed_title: str = Field(min_length=1, max_length=200)


class RestoreArchivedProjectRequest(BaseModel):
    confirmed: Literal[True]
    confirmed_title: str = Field(min_length=1, max_length=200)


class TruncateChapterRequest(BaseModel):
    confirmed: Literal[True]
    base_version: int = Field(ge=1)
    preview_sha256: str = Field(min_length=64, max_length=64)
    reason: str = Field(min_length=1, max_length=1000)


class RollbackRequest(BaseModel):
    target_version: int = Field(ge=1)
    base_version: int = Field(ge=1)
    confirmed: Literal[True] = True
    reason: str = Field(default="作者从版本历史恢复正式状态", min_length=1, max_length=1000)


class RestartStoryRequest(BaseModel):
    base_version: int = Field(ge=1)
    confirmed: Literal[True]
    reason: str = Field(
        default="作者保留当前正式世界观、人物和大纲并从头重新创作",
        min_length=1,
        max_length=1000,
    )


class PruneVersionsRequest(BaseModel):
    base_version: int = Field(ge=1)
    keep_latest: Literal[10] = 10
    confirmed: Literal[True]
    confirmed_title: str = Field(min_length=1, max_length=200)
    reason: str = Field(default="作者手动清理旧版本并保留最新10版", min_length=1, max_length=1000)


class ImpactAnalysisRequest(BaseModel):
    base_version: int = Field(ge=1)
    proposed_delta: StateDelta


@router.get("/project-setup-drafts")
async def list_project_setup_drafts(session: Session) -> list[ProjectSetupDraftResponse]:
    rows = list(
        await session.scalars(
            select(ProjectSetupDraftRecord)
            .where(ProjectSetupDraftRecord.status == "draft")
            .order_by(ProjectSetupDraftRecord.updated_at.desc())
        )
    )
    return [_setup_draft_response(item) for item in rows]


@router.post("/project-setup-drafts", status_code=201)
async def create_project_setup_draft(
    payload: SaveProjectSetupDraftRequest, session: Session
) -> ProjectSetupDraftResponse:
    item = ProjectSetupDraftRecord(
        title=payload.payload.title.strip() or "未命名作品",
        payload=payload.payload.model_dump(mode="json"),
    )
    session.add(item)
    await session.flush()
    await session.refresh(item)
    return _setup_draft_response(item)


@router.put("/project-setup-drafts/{draft_id}")
async def update_project_setup_draft(
    draft_id: UUID,
    payload: SaveProjectSetupDraftRequest,
    session: Session,
) -> ProjectSetupDraftResponse:
    item = await session.get(ProjectSetupDraftRecord, draft_id, with_for_update=True)
    if item is None:
        raise NotFoundError("project setup draft not found")
    if item.status != "draft":
        raise ConflictError("project setup draft is already completed")
    if payload.expected_updated_at and item.updated_at.isoformat() != payload.expected_updated_at:
        raise ConflictError("project setup draft changed; reload before saving")
    item.title = payload.payload.title.strip() or "未命名作品"
    item.payload = payload.payload.model_dump(mode="json")
    await session.flush()
    await session.refresh(item)
    return _setup_draft_response(item)


@router.post(
    "/project-setup-drafts/{draft_id}/finalize",
    status_code=201,
    response_model_exclude_none=True,
)
async def finalize_project_setup_draft(
    draft_id: UUID,
    payload: FinalizeProjectSetupDraftRequest,
    request: Request,
    idempotency_key: IdempotencyKey,
    session: Session,
) -> ProjectSetupFinalizeResponse:
    request_payload = payload.model_dump(mode="json")
    command = "finalize_project_setup_draft"
    resource_scope = f"project-setup-draft:{draft_id}"
    cached = await load_idempotent_response(
        session,
        command,
        idempotency_key,
        request_payload,
        resource_scope=resource_scope,
    )
    if cached is not None:
        return ProjectSetupFinalizeResponse.model_validate(cached)
    item = await session.get(ProjectSetupDraftRecord, draft_id, with_for_update=True)
    if item is None:
        raise NotFoundError("project setup draft not found")
    setup = ProjectSetupPayload.model_validate(item.payload)
    if item.status == "completed" and item.finalized_project_id:
        response = ProjectSetupFinalizeResponse(
            project_id=item.finalized_project_id,
            setup_draft_id=item.id,
            mode=setup.mode,
            preview_id=(
                setup.import_preview.preview_id if setup.import_preview is not None else None
            ),
        )
        await save_idempotent_response(
            session,
            command,
            idempotency_key,
            response.model_dump(mode="json", exclude_none=True),
            request_payload,
            resource_scope=resource_scope,
        )
        return response
    if not setup.title.strip():
        raise ConflictError("project title is required")
    if setup.mode == "import":
        if setup.import_preview is None or payload.import_binding != setup.import_preview:
            raise ConflictError("import preview binding is required and must match the setup draft")
        binding = setup.import_preview
        import_result = await ImportPreviewCommitService(
            session,
            request.app.state.content_store_root / "import-staging",
        ).commit(
            binding.preview_id,
            ImportPreviewBinding(
                upload_sha256=binding.upload_sha256,
                chapter_manifest_sha256=binding.chapter_manifest_sha256,
                selected_encoding=binding.selected_encoding,
                parser_version=binding.parser_version,
            ),
            expected_title=setup.title,
        )
        created_project_id = UUID(str(import_result["project_id"]))
        if setup.genre_selection_mode == "specified":
            await StyleProfileService(session).save(
                created_project_id,
                [],
                selection_mode="specified",
                genre_card_id=setup.genre_card_id,
                secondary_genre_card_ids=setup.secondary_genre_card_ids,
            )
    else:
        if payload.import_binding is not None:
            raise ConflictError("blank project setup cannot include an import binding")
        blank_result = await WorkflowService(session).create_project(
            setup.title.strip(),
            idempotency_key,
            genre=setup.genre,
            genre_card_id=(
                setup.genre_card_id if setup.genre_selection_mode == "specified" else None
            ),
            secondary_genre_card_ids=(
                setup.secondary_genre_card_ids
                if setup.genre_selection_mode == "specified"
                else ()
            ),
            target_chapters=setup.target_chapters,
            world_summary=setup.world_summary,
            character_names=setup.character_names,
        )
        created_project_id = UUID(str(blank_result["project_id"]))
    item.status = "completed"
    item.finalized_project_id = created_project_id
    await session.flush()
    response = ProjectSetupFinalizeResponse(
        project_id=item.finalized_project_id,
        setup_draft_id=item.id,
        mode=setup.mode,
        preview_id=(
            setup.import_preview.preview_id if setup.import_preview is not None else None
        ),
    )
    await save_idempotent_response(
        session,
        command,
        idempotency_key,
        response.model_dump(mode="json", exclude_none=True),
        request_payload,
        resource_scope=resource_scope,
    )
    return response


@router.get("/projects", response_model=list[ProjectSummary])
async def list_projects(session: Session) -> list[dict[str, Any]]:
    return await ProjectQueryService(session).list_projects()


@router.get("/projects/archived", response_model=list[ArchivedProjectSummary])
async def list_archived_projects(session: Session) -> list[dict[str, Any]]:
    return await ProjectQueryService(session).list_archived_projects()


def _setup_draft_response(item: ProjectSetupDraftRecord) -> ProjectSetupDraftResponse:
    return ProjectSetupDraftResponse(
        draft_id=item.id,
        schema_version="project-setup-draft-v1",
        title=item.title,
        payload=ProjectSetupPayload.model_validate(item.payload),
        status=item.status,
        finalized_project_id=item.finalized_project_id,
        updated_at=item.updated_at.isoformat(),
    )


@router.get("/projects/{project_id}/chapters", response_model=list[ChapterSummary])
async def list_chapters(project_id: UUID, session: Session) -> list[dict[str, Any]]:
    return await ProjectQueryService(session).list_chapters(project_id)


@router.get("/projects/{project_id}/versions", response_model=list[VersionSummary])
async def list_versions(project_id: UUID, session: Session) -> list[dict[str, Any]]:
    return await ProjectQueryService(session).list_versions(project_id)


@router.get("/projects/{project_id}/restart-story/preview")
async def restart_story_preview(project_id: UUID, session: Session) -> dict[str, Any]:
    return await WorkflowService(session).restart_story_preview(project_id)


@router.get("/projects/{project_id}/versions/prune-preview")
async def prune_versions_preview(project_id: UUID, session: Session) -> dict[str, Any]:
    return await VersionMaintenanceService(session).preview(project_id, keep_latest=10)


@router.post("/projects", status_code=201)
async def create_project(
    payload: CreateProjectRequest, idempotency_key: IdempotencyKey, session: Session
) -> dict[str, Any]:
    return await WorkflowService(session).create_project(
        payload.title,
        idempotency_key,
        genre=payload.genre,
        genre_card_id=payload.genre_card_id,
        secondary_genre_card_ids=payload.secondary_genre_card_ids,
        target_chapters=payload.target_chapters,
        world_summary=payload.world_summary,
        character_names=payload.character_names,
    )


@router.post("/projects/{project_id}/archive")
async def archive_project(
    project_id: UUID,
    payload: ArchiveProjectRequest,
    idempotency_key: IdempotencyKey,
    session: Session,
) -> dict[str, Any]:
    return await WorkflowService(session).archive_project(
        project_id, payload.confirmed_title, idempotency_key
    )


@router.post("/projects/{project_id}/restore")
async def restore_archived_project(
    project_id: UUID,
    payload: RestoreArchivedProjectRequest,
    idempotency_key: IdempotencyKey,
    session: Session,
) -> dict[str, Any]:
    return await WorkflowService(session).restore_archived_project(
        project_id, payload.confirmed_title, idempotency_key
    )


@router.get("/projects/{project_id}/archive-preview")
async def archive_project_preview(project_id: UUID, session: Session) -> dict[str, Any]:
    return await WorkflowService(session).archive_project_preview(project_id)


@router.post("/projects/{project_id}/chapters", status_code=201)
async def create_chapter(
    project_id: UUID,
    payload: CreateChapterRequest,
    idempotency_key: IdempotencyKey,
    session: Session,
) -> dict[str, Any]:
    return await WorkflowService(session).create_chapter(
        project_id, payload.ordinal, payload.title, idempotency_key
    )


@router.get("/chapters/{chapter_id}/truncate-preview")
async def truncate_chapter_preview(chapter_id: UUID, session: Session) -> dict[str, Any]:
    return await WorkflowService(session).truncate_chapter_preview(chapter_id)


@router.post("/chapters/{chapter_id}/truncate")
async def truncate_chapter(
    chapter_id: UUID,
    payload: TruncateChapterRequest,
    idempotency_key: IdempotencyKey,
    session: Session,
) -> dict[str, Any]:
    return await WorkflowService(session).remove_chapter(
        chapter_id,
        payload.reason,
        idempotency_key,
        base_version=payload.base_version,
        preview_sha256=payload.preview_sha256,
    )


@router.get("/projects/{project_id}/versions/diff")
async def diff_versions(
    project_id: UUID,
    session: Session,
    from_version: Annotated[int, Query(ge=1)],
    to_version: Annotated[int, Query(ge=1)],
) -> dict[str, Any]:
    return await WorkflowService(session).diff_versions(project_id, from_version, to_version)


@router.get("/projects/{project_id}/reader-boundary")
async def reader_boundary(
    project_id: UUID,
    session: Session,
    version: Annotated[int, Query(ge=1)],
    scene_id: UUID,
) -> dict[str, Any]:
    return await WorkflowService(session).reader_boundary(project_id, version, scene_id)


@router.post("/projects/{project_id}/setting-impact")
async def analyze_setting_impact(
    project_id: UUID,
    payload: ImpactAnalysisRequest,
    session: Session,
) -> dict[str, Any]:
    return await WorkflowService(session).analyze_setting_impact(
        project_id,
        payload.base_version,
        payload.proposed_delta,
    )


@router.post("/projects/{project_id}/rollback", status_code=201)
async def rollback(
    project_id: UUID,
    payload: RollbackRequest,
    idempotency_key: IdempotencyKey,
    session: Session,
) -> dict[str, Any]:
    return await WorkflowService(session).rollback(
        project_id,
        payload.target_version,
        payload.base_version,
        idempotency_key,
        payload.reason,
    )


@router.post("/projects/{project_id}/restart-story", status_code=201)
async def restart_story(
    project_id: UUID,
    payload: RestartStoryRequest,
    idempotency_key: IdempotencyKey,
    session: Session,
) -> dict[str, Any]:
    return await WorkflowService(session).restart_story(
        project_id,
        payload.base_version,
        payload.reason,
        idempotency_key,
    )


@router.post("/projects/{project_id}/versions/prune")
async def prune_versions(
    project_id: UUID,
    payload: PruneVersionsRequest,
    idempotency_key: IdempotencyKey,
    session: Session,
) -> dict[str, Any]:
    return await VersionMaintenanceService(session).prune(
        project_id,
        payload.keep_latest,
        payload.base_version,
        payload.confirmed_title,
        payload.reason,
        idempotency_key,
    )
