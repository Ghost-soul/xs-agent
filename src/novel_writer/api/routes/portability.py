import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote, unquote
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import BaseModel, Field

from novel_writer.api.dependencies import IdempotencyKey, Session
from novel_writer.db.models import ImportPreviewRecord
from novel_writer.services.data_portability import DataPortabilityService
from novel_writer.services.errors import NotFoundError, WorkflowError
from novel_writer.services.idempotency import (
    load_idempotent_response,
    save_idempotent_response,
)
from novel_writer.services.import_previews import (
    PARSER_VERSION,
    CommittedEncoding,
    ImportPreviewBinding,
    ImportPreviewCommitService,
    SupportedEncoding,
    build_import_preview,
    decode_upload,
    staged_import_path,
)
from novel_writer.services.project_workspaces import ProjectWorkspaceService

router = APIRouter(prefix="/api", tags=["data-portability"])


class ImportManuscriptRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20_000_000)
    confirmed: Literal[True]


class ArchiveRequest(BaseModel):
    archive: dict[str, Any]


class RestoreArchiveRequest(ArchiveRequest):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    confirmed: Literal[True]


class SyncWorkspaceRequest(BaseModel):
    confirmed: Literal[True]


class ImportPreviewChapterResponse(BaseModel):
    ordinal: int
    title: str
    characters: int
    first_sample: str
    last_sample: str


class ImportWarningResponse(BaseModel):
    code: str
    ordinal: int | None = None
    title: str | None = None
    count: int | None = None
    previous: int | None = None
    current: int | None = None


class ImportPreviewResponse(BaseModel):
    preview_id: UUID
    schema_version: Literal["import-preview-v1"]
    title: str
    original_filename: str
    upload_sha256: str
    byte_size: int
    selected_encoding: CommittedEncoding
    requires_encoding_confirmation: bool
    parser_version: str
    chapter_manifest_sha256: str
    chapter_count: int
    character_count: int
    chapters: list[ImportPreviewChapterResponse]
    warnings: list[ImportWarningResponse]
    status: str
    expires_at: str


class ImportCommitRequest(BaseModel):
    upload_sha256: str = Field(min_length=64, max_length=64)
    chapter_manifest_sha256: str = Field(min_length=64, max_length=64)
    selected_encoding: Literal["utf-8", "gb18030", "utf-16le", "utf-16be"]
    parser_version: str = Field(min_length=1, max_length=80)
    confirmed: Literal[True]


class ImportCommitResponse(BaseModel):
    preview_id: UUID
    project_id: UUID
    version: int
    chapter_count: int
    upload_sha256: str
    chapter_manifest_sha256: str


@router.post("/imports/preview", status_code=201)
async def preview_import(
    request: Request,
    session: Session,
    title: Annotated[str, Query(min_length=1, max_length=200)],
    encoding: SupportedEncoding = "auto",
    original_filename: Annotated[str, Header(alias="X-Upload-Filename")] = "",
) -> ImportPreviewResponse:
    normalized_title = title.strip()
    if not normalized_title:
        raise WorkflowError("作品名称不能为空")
    preview_id = uuid4()
    storage_key = f"{preview_id}.bin"
    root = request.app.state.content_store_root / "import-staging"
    root.mkdir(parents=True, exist_ok=True)
    path = root / storage_key
    total = 0
    hasher = hashlib.sha256()
    try:
        with path.open("xb") as target:
            async for chunk in request.stream():
                total += len(chunk)
                if total > 20_000_000:
                    raise WorkflowError("导入文件超过 20,000,000 字节上限")
                hasher.update(chunk)
                target.write(chunk)
        raw = path.read_bytes()
        decoded = decode_upload(raw, encoding)
        preview = build_import_preview(raw, decoded)
        if preview["upload_sha256"] != hasher.hexdigest():
            raise WorkflowError("上传内容完整性校验失败")
        item = ImportPreviewRecord(
            id=preview_id,
            title=normalized_title,
            original_filename=_safe_display_filename(original_filename),
            byte_size=total,
            upload_sha256=preview["upload_sha256"],
            selected_encoding=preview["selected_encoding"],
            parser_version=PARSER_VERSION,
            chapter_manifest_sha256=preview["chapter_manifest_sha256"],
            preview=preview,
            storage_key=storage_key,
            status="previewed",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        session.add(item)
        await session.flush()
        return _import_preview_response(item)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


@router.get("/imports/{preview_id}")
async def get_import_preview(preview_id: UUID, session: Session) -> ImportPreviewResponse:
    item = await session.get(ImportPreviewRecord, preview_id)
    if item is None:
        raise NotFoundError("import preview not found")
    return _import_preview_response(item)


@router.post("/imports/{preview_id}/commit", status_code=201)
async def commit_import(
    preview_id: UUID,
    payload: ImportCommitRequest,
    request: Request,
    idempotency_key: IdempotencyKey,
    session: Session,
) -> ImportCommitResponse:
    request_payload = payload.model_dump(mode="json")
    command = "commit_import_preview"
    resource_scope = f"import-preview:{preview_id}"
    cached = await load_idempotent_response(
        session,
        command,
        idempotency_key,
        request_payload,
        resource_scope=resource_scope,
    )
    if cached is not None:
        return ImportCommitResponse.model_validate(cached)
    result = await ImportPreviewCommitService(
        session,
        Path(request.app.state.content_store_root) / "import-staging",
    ).commit(
        preview_id,
        ImportPreviewBinding(
            upload_sha256=payload.upload_sha256,
            chapter_manifest_sha256=payload.chapter_manifest_sha256,
            selected_encoding=payload.selected_encoding,
            parser_version=payload.parser_version,
        ),
    )
    response = ImportCommitResponse.model_validate(result)
    await save_idempotent_response(
        session,
        command,
        idempotency_key,
        response.model_dump(mode="json"),
        request_payload,
        resource_scope=resource_scope,
    )
    return response


def _import_preview_response(item: ImportPreviewRecord) -> ImportPreviewResponse:
    preview = item.preview
    return ImportPreviewResponse(
        preview_id=item.id,
        schema_version="import-preview-v1",
        title=item.title,
        original_filename=item.original_filename,
        upload_sha256=item.upload_sha256,
        byte_size=item.byte_size,
        selected_encoding=item.selected_encoding,
        requires_encoding_confirmation=bool(preview["requires_encoding_confirmation"]),
        parser_version=item.parser_version,
        chapter_manifest_sha256=item.chapter_manifest_sha256,
        chapter_count=int(preview["chapter_count"]),
        character_count=int(preview["character_count"]),
        chapters=[
            ImportPreviewChapterResponse.model_validate(value)
            for value in preview["chapters"]
        ],
        warnings=[ImportWarningResponse.model_validate(value) for value in preview["warnings"]],
        status=item.status,
        expires_at=item.expires_at.isoformat(),
    )


def _safe_display_filename(value: str) -> str:
    normalized = unquote(value).replace("\\", "/").split("/")[-1]
    return "".join(character for character in normalized if character.isprintable())[:300]


def _staged_import_path(request: Request, storage_key: str) -> Path:
    return staged_import_path(
        Path(request.app.state.content_store_root) / "import-staging",
        storage_key,
    )


@router.post("/data/import-manuscript", status_code=201)
async def import_manuscript(
    payload: ImportManuscriptRequest, session: Session
) -> dict[str, Any]:
    return await DataPortabilityService(session).import_manuscript(
        payload.title, payload.text
    )


@router.get("/projects/{project_id}/export/markdown")
async def export_markdown(project_id: UUID, session: Session) -> Response:
    filename, content = await DataPortabilityService(session).export_markdown(project_id)
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/projects/{project_id}/export/{format_name}")
async def export_project_format(
    project_id: UUID, format_name: Literal["txt", "docx", "epub"], session: Session
) -> Response:
    service = DataPortabilityService(session)
    if format_name == "txt":
        filename, content = await service.export_text(project_id)
        payload = content.encode("utf-8-sig")
        media_type = "text/plain; charset=utf-8"
    elif format_name == "docx":
        filename, payload = await service.export_docx(project_id)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        filename, payload = await service.export_epub(project_id)
        media_type = "application/epub+zip"
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/projects/{project_id}/backup")
async def create_backup(project_id: UUID, session: Session) -> dict[str, Any]:
    return await DataPortabilityService(session).create_archive(project_id)




@router.get("/projects/{project_id}/workspace")
async def project_workspace(
    project_id: UUID, request: Request, session: Session
) -> dict[str, Any]:
    return await ProjectWorkspaceService(
        session, request.app.state.settings.project_workspace_root
    ).status(project_id)


@router.post("/projects/{project_id}/workspace/sync")
async def sync_project_workspace(
    project_id: UUID,
    payload: SyncWorkspaceRequest,
    request: Request,
    session: Session,
) -> dict[str, Any]:
    return await ProjectWorkspaceService(
        session, request.app.state.settings.project_workspace_root
    ).sync(project_id)


@router.post("/data/backup/inspect")
async def inspect_backup(
    payload: ArchiveRequest, session: Session
) -> dict[str, Any]:
    return DataPortabilityService(session).inspect_archive(payload.archive)


@router.post("/data/backup/restore", status_code=201)
async def restore_backup(
    payload: RestoreArchiveRequest, session: Session
) -> dict[str, Any]:
    return await DataPortabilityService(session).restore_archive(
        payload.archive, payload.title
    )
