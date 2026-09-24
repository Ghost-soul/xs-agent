from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.engine import Database
from novel_writer.db.models import LocalTaskRecord, ProjectRecord
from novel_writer.services.canonical_json import canonical_json_sha256
from novel_writer.services.data_portability import DataPortabilityService
from novel_writer.services.errors import ConflictError, NotFoundError, WorkflowError
from novel_writer.services.idempotency import (
    load_idempotent_response,
    save_idempotent_response,
)
from novel_writer.services.local_search import LocalSearchService

logger = logging.getLogger(__name__)

LocalTaskKind = Literal[
    "export_markdown",
    "export_txt",
    "export_docx",
    "export_epub",
    "project_backup",
    "search_rebuild",
]
LOCAL_TASK_ALLOWLIST: frozenset[str] = frozenset(
    {
        "export_markdown",
        "export_txt",
        "export_docx",
        "export_epub",
        "project_backup",
        "search_rebuild",
    }
)
LOCAL_TASK_SCHEMA_VERSION = "local-task-v1"


@dataclass(frozen=True)
class LocalTaskArtifact:
    filename: str
    media_type: str
    extension: str
    content: bytes


@dataclass(frozen=True)
class LocalTaskExecution:
    output_sha256: str
    artifact: LocalTaskArtifact | None


class LocalTaskService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def queue(
        self,
        project_id: UUID,
        kind: LocalTaskKind,
        idempotency_key: str,
    ) -> LocalTaskRecord:
        if kind not in LOCAL_TASK_ALLOWLIST:
            raise WorkflowError("local task kind is not allowlisted")
        project = await self.session.scalar(
            select(ProjectRecord)
            .where(ProjectRecord.id == project_id)
            .with_for_update()
        )
        if project is None or project.archived_at is not None:
            raise NotFoundError("active project not found")
        manifest: dict[str, Any] = {
            "schema_version": LOCAL_TASK_SCHEMA_VERSION,
            "project_id": str(project_id),
            "kind": kind,
        }
        if kind == "search_rebuild":
            manifest["requested_current_version_id"] = (
                str(project.current_version_id) if project.current_version_id else None
            )
        else:
            manifest["formal_snapshot"] = await DataPortabilityService(
                self.session
            ).formal_snapshot_manifest(project_id)
        command = "queue_local_task"
        resource_scope = f"project:{project_id}:local-task:{kind}"
        cached = await load_idempotent_response(
            self.session,
            command,
            idempotency_key,
            manifest,
            resource_scope=resource_scope,
        )
        if cached is not None:
            existing = await self.session.get(LocalTaskRecord, UUID(str(cached["task_id"])))
            if existing is None:
                raise ConflictError("idempotent local task record is unavailable")
            return existing
        if kind == "search_rebuild":
            pending_task: LocalTaskRecord | None = await self.session.scalar(
                select(LocalTaskRecord)
                .where(
                    LocalTaskRecord.project_id == project_id,
                    LocalTaskRecord.kind == "search_rebuild",
                    LocalTaskRecord.status.in_(("queued", "running")),
                )
                .order_by(LocalTaskRecord.created_at.desc(), LocalTaskRecord.id.desc())
                .limit(1)
            )
            if (
                pending_task is not None
                and pending_task.payload.get("requested_current_version_id")
                == manifest["requested_current_version_id"]
            ):
                await save_idempotent_response(
                    self.session,
                    command,
                    idempotency_key,
                    {"task_id": str(pending_task.id)},
                    manifest,
                    resource_scope=resource_scope,
                )
                return pending_task
        item = LocalTaskRecord(
            project_id=project_id,
            kind=kind,
            input_sha256=canonical_json_sha256(manifest),
            payload=manifest,
        )
        self.session.add(item)
        await self.session.flush()
        await save_idempotent_response(
            self.session,
            command,
            idempotency_key,
            {"task_id": str(item.id)},
            manifest,
            resource_scope=resource_scope,
        )
        return item

    async def claim(self, owner: str, *, lease_seconds: int = 120) -> LocalTaskRecord | None:
        now = datetime.now(UTC)
        item = await self.session.scalar(
            select(LocalTaskRecord)
            .where(
                or_(
                    LocalTaskRecord.status == "queued",
                    (
                        (LocalTaskRecord.status == "running")
                        & (LocalTaskRecord.lease_expires_at.is_not(None))
                        & (LocalTaskRecord.lease_expires_at < now)
                    ),
                )
            )
            .order_by(LocalTaskRecord.created_at, LocalTaskRecord.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if item is None:
            return None
        _validate_record_manifest(item)
        item.status = "running"
        item.progress = max(1, item.progress)
        item.error_code = None
        item.lease_owner = owner
        item.lease_expires_at = now + timedelta(seconds=lease_seconds)
        await self.session.flush()
        return item


class LocalTaskRunner:
    """Provider-free worker for a fixed set of deterministic local artifacts."""

    def __init__(self, database: Database, owner: str, artifact_root: Path) -> None:
        self.database = database
        self.owner = owner
        self.artifact_root = artifact_root

    async def tick(self) -> bool:
        async with self.database.session() as session, session.begin():
            item = await LocalTaskService(session).claim(self.owner)
            if item is None:
                return False
            task_id = item.id
        try:
            async with self.database.session() as session, session.begin():
                item = await session.get(LocalTaskRecord, task_id)
                if item is None or item.lease_owner != self.owner or item.status != "running":
                    return True
                execution = await _execute_task(session, item)
            if execution.artifact is not None:
                path = local_task_artifact_path(
                    self.artifact_root,
                    task_id,
                    execution.output_sha256,
                    execution.artifact.extension,
                )
                _write_artifact(path, execution.artifact.content)
            async with self.database.session() as session, session.begin():
                item = await session.get(LocalTaskRecord, task_id, with_for_update=True)
                if item is None or item.lease_owner != self.owner or item.status != "running":
                    return True
                item.status = "completed"
                item.progress = 100
                item.output_sha256 = execution.output_sha256
                item.error_code = None
                item.completed_at = datetime.now(UTC)
                item.lease_owner = None
                item.lease_expires_at = None
            return True
        except Exception as error:
            logger.exception("local task failed", extra={"task_id": str(task_id)})
            async with self.database.session() as session, session.begin():
                item = await session.get(LocalTaskRecord, task_id, with_for_update=True)
                if item is not None and item.lease_owner == self.owner:
                    item.status = "failed"
                    item.error_code = _stable_error_code(error, kind=item.kind)
                    item.completed_at = datetime.now(UTC)
                    item.lease_owner = None
                    item.lease_expires_at = None
            return True

    async def run(self, stopped: asyncio.Event, *, poll_seconds: float = 1.0) -> None:
        while not stopped.is_set():
            try:
                progressed = await self.tick()
            except Exception:
                logger.exception("local task worker tick failed")
                progressed = False
            if progressed:
                await asyncio.sleep(0)
                continue
            try:
                await asyncio.wait_for(stopped.wait(), timeout=poll_seconds)
            except TimeoutError:
                continue


async def _build_artifact(
    session: AsyncSession,
    item: LocalTaskRecord,
) -> LocalTaskArtifact:
    _validate_record_manifest(item)
    if item.project_id is None:
        raise WorkflowError("local task has no project")
    service = DataPortabilityService(session)
    snapshot = item.payload.get("formal_snapshot")
    if not isinstance(snapshot, dict):
        raise WorkflowError("local task formal snapshot manifest is invalid")
    await service.verify_formal_snapshot_manifest(snapshot)
    version_id = UUID(str(snapshot["version_id"]))
    project_title = str(snapshot["project_title"])
    if item.kind == "export_markdown":
        filename, text = await service.export_markdown(
            item.project_id, version_id=version_id, project_title=project_title
        )
        return LocalTaskArtifact(
            filename,
            "text/markdown; charset=utf-8",
            "md",
            text.encode("utf-8-sig"),
        )
    if item.kind == "export_txt":
        filename, text = await service.export_text(
            item.project_id, version_id=version_id, project_title=project_title
        )
        return LocalTaskArtifact(
            filename,
            "text/plain; charset=utf-8",
            "txt",
            text.encode("utf-8-sig"),
        )
    if item.kind == "export_docx":
        filename, content = await service.export_docx(
            item.project_id, version_id=version_id, project_title=project_title
        )
        return LocalTaskArtifact(
            filename,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "docx",
            content,
        )
    if item.kind == "export_epub":
        filename, content = await service.export_epub(
            item.project_id, version_id=version_id, project_title=project_title
        )
        return LocalTaskArtifact(filename, "application/epub+zip", "epub", content)
    if item.kind == "project_backup":
        project = await session.get(ProjectRecord, item.project_id)
        if project is None:
            raise NotFoundError("project not found")
        payload = await service.create_archive(
            item.project_id, version_id=version_id, project_title=project_title
        )
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        return LocalTaskArtifact(
            f"{project.title}-backup.json", "application/json", "json", content
        )
    raise WorkflowError("local task kind is not allowlisted")


async def _execute_task(
    session: AsyncSession,
    item: LocalTaskRecord,
) -> LocalTaskExecution:
    _validate_record_manifest(item)
    if item.project_id is None:
        raise WorkflowError("local task has no project")
    if item.kind == "search_rebuild":
        project = await session.get(ProjectRecord, item.project_id)
        expected_version_id = item.payload.get("requested_current_version_id")
        actual_version_id = (
            str(project.current_version_id) if project and project.current_version_id else None
        )
        if actual_version_id != expected_version_id:
            raise WorkflowError("local task source version changed")
        rebuilt = await LocalSearchService(session).rebuild_project_index(item.project_id)
        return LocalTaskExecution(output_sha256=rebuilt.index_sha256, artifact=None)
    artifact = await _build_artifact(session, item)
    return LocalTaskExecution(
        output_sha256=hashlib.sha256(artifact.content).hexdigest(),
        artifact=artifact,
    )


def _validate_record_manifest(item: LocalTaskRecord) -> None:
    payload: dict[str, Any] = item.payload
    kind_payload_valid = (
        (item.kind == "search_rebuild" and "requested_current_version_id" in payload)
        or (
            item.kind != "search_rebuild"
            and isinstance(payload.get("formal_snapshot"), dict)
        )
    )
    if (
        item.schema_version != LOCAL_TASK_SCHEMA_VERSION
        or payload.get("schema_version") != LOCAL_TASK_SCHEMA_VERSION
        or payload.get("project_id") != (str(item.project_id) if item.project_id else None)
        or payload.get("kind") != item.kind
        or item.kind not in LOCAL_TASK_ALLOWLIST
        or not kind_payload_valid
        or canonical_json_sha256(payload) != item.input_sha256
    ):
        raise WorkflowError("local task manifest is invalid")


def local_task_artifact_path(
    root: Path,
    task_id: UUID,
    output_sha256: str,
    extension: str,
) -> Path:
    if len(output_sha256) != 64 or any(value not in "0123456789abcdef" for value in output_sha256):
        raise WorkflowError("local task artifact SHA is invalid")
    if extension not in {"md", "txt", "docx", "epub", "json"}:
        raise WorkflowError("local task artifact extension is invalid")
    resolved_root = (root / "local-tasks").resolve()
    path = (resolved_root / str(task_id) / f"{output_sha256}.{extension}").resolve()
    if path.parent.parent != resolved_root:
        raise WorkflowError("local task artifact path escaped its root")
    return path


def task_artifact_metadata(kind: str, project_title: str) -> tuple[str, str, str]:
    safe_title = (
        "".join("_" if value in '\\/:*?\"<>|' else value for value in project_title)
        .strip(" .")
        or "小说"
    )
    mapping = {
        "export_markdown": (f"{safe_title}.md", "text/markdown; charset=utf-8", "md"),
        "export_txt": (f"{safe_title}.txt", "text/plain; charset=utf-8", "txt"),
        "export_docx": (
            f"{safe_title}.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "docx",
        ),
        "export_epub": (f"{safe_title}.epub", "application/epub+zip", "epub"),
        "project_backup": (f"{safe_title}-backup.json", "application/json", "json"),
    }
    if kind not in mapping:
        raise WorkflowError("local task artifact kind is invalid")
    return mapping[kind]


def _write_artifact(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{uuid4()}.tmp"
    try:
        with temporary.open("xb") as target:
            target.write(content)
            target.flush()
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _stable_error_code(error: Exception, *, kind: str | None = None) -> str:
    if isinstance(error, WorkflowError) and "manifest" in str(error):
        return "task_manifest_invalid"
    if kind == "search_rebuild":
        if "source version changed" in str(error):
            return "task_source_changed"
        return "search_rebuild_failed"
    if isinstance(error, OSError):
        return "artifact_write_failed"
    return "task_execution_failed"
