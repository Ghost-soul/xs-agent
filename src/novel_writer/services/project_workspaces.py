from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import (
    ProjectRecord,
    StateVersionRecord,
    WritingChunkRecord,
    WritingSessionRecord,
)
from novel_writer.services.data_portability import DataPortabilityService
from novel_writer.services.errors import NotFoundError


class ProjectWorkspaceService:
    def __init__(self, session: AsyncSession, root: Path) -> None:
        self.session = session
        self.root = root.resolve()

    async def status(self, project_id: UUID) -> dict[str, Any]:
        project, version = await self._project_version(project_id)
        folder = self._folder(project)
        session_count = int(
            await self.session.scalar(
                select(func.count())
                .select_from(WritingSessionRecord)
                .where(WritingSessionRecord.project_id == project_id)
            )
            or 0
        )
        chunk_count = int(
            await self.session.scalar(
                select(func.count())
                .select_from(WritingChunkRecord)
                .join(
                    WritingSessionRecord,
                    WritingSessionRecord.id == WritingChunkRecord.session_id,
                )
                .where(WritingSessionRecord.project_id == project_id)
            )
            or 0
        )
        manifest = _read_json(folder / "manifest.json") if folder.exists() else {}
        return {
            "project_id": str(project.id),
            "title": project.title,
            "path": str(folder),
            "exists": folder.exists(),
            "formal_version": version.number,
            "session_count": session_count,
            "chunk_count": chunk_count,
            "last_synced_at": manifest.get("synced_at"),
            "synced_version": manifest.get("formal_version"),
            "up_to_date": (
                manifest.get("formal_version") == version.number
                and manifest.get("session_count") == session_count
                and manifest.get("chunk_count") == chunk_count
            ),
            "policy": "数据库是正式事实源；项目文件夹是可读镜像，不会反向覆盖正式状态。",
        }

    async def sync(self, project_id: UUID) -> dict[str, Any]:
        project, version = await self._project_version(project_id)
        folder = self._folder(project)
        folder.mkdir(parents=True, exist_ok=True)
        formal_dir = folder / "formal"
        sessions_dir = folder / "sessions"
        backup_dir = folder / "backups"
        for directory in (formal_dir, sessions_dir, backup_dir):
            directory.mkdir(parents=True, exist_ok=True)

        portability = DataPortabilityService(self.session)
        _, manuscript = await portability.export_markdown(project_id)
        archive = await portability.create_archive(project_id)
        _write_text(formal_dir / "manuscript.md", manuscript)
        _write_json(formal_dir / "story-state.json", version.state)
        _write_json(backup_dir / "latest.json", archive)

        sessions = (
            await self.session.scalars(
                select(WritingSessionRecord)
                .where(WritingSessionRecord.project_id == project_id)
                .order_by(WritingSessionRecord.created_at, WritingSessionRecord.id)
            )
        ).all()
        total_chunks = 0
        for writing in sessions:
            session_dir = sessions_dir / str(writing.id)
            chunks_dir = session_dir / "chunks"
            chunks_dir.mkdir(parents=True, exist_ok=True)
            chunks = (
                await self.session.scalars(
                    select(WritingChunkRecord)
                    .where(WritingChunkRecord.session_id == writing.id)
                    .order_by(WritingChunkRecord.ordinal)
                )
            ).all()
            total_chunks += len(chunks)
            _write_json(
                session_dir / "session.json",
                {
                    "session_id": str(writing.id),
                    "base_version_id": str(writing.base_version_id),
                    "status": writing.status,
                    "stage": writing.stage,
                    "direction": writing.direction,
                    "author_guidance": writing.author_guidance,
                    "story_intent": writing.story_intent,
                    "target_min_chars": writing.target_min_chars,
                    "target_max_chars": writing.target_max_chars,
                    "scene_target_chars": writing.scene_target_chars,
                    "chapter_target_chars": writing.chapter_target_chars,
                    "chunk_count": len(chunks),
                    "created_at": writing.created_at.isoformat(),
                },
            )
            for chunk in chunks:
                _write_text(chunks_dir / f"{chunk.ordinal:03d}.md", chunk.body + "\n")
            if writing.raw_body:
                _write_text(session_dir / "raw-manuscript.md", writing.raw_body + "\n")
            if writing.review_package:
                _write_json(session_dir / "review-package.json", writing.review_package)

        synced_at = datetime.now(UTC).isoformat()
        manifest = {
            "format": "novel-writer-workspace-v1",
            "project_id": str(project.id),
            "title": project.title,
            "formal_version": version.number,
            "formal_version_id": str(version.id),
            "session_count": len(sessions),
            "chunk_count": total_chunks,
            "synced_at": synced_at,
            "source_of_truth": "PostgreSQL",
        }
        _write_json(folder / "manifest.json", manifest)
        _write_text(folder / "README.md", _workspace_readme(project.title, project.id))
        return await self.status(project_id)

    async def _project_version(self, project_id: UUID) -> tuple[ProjectRecord, StateVersionRecord]:
        project = await self.session.get(ProjectRecord, project_id)
        if project is None or project.current_version_id is None:
            raise NotFoundError("project not found")
        version = await self.session.get(StateVersionRecord, project.current_version_id)
        if version is None:
            raise NotFoundError("formal version not found")
        return project, version

    def _folder(self, project: ProjectRecord) -> Path:
        slug = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", project.title).strip(" .-")
        slug = (slug or "未命名作品")[:80]
        folder = (self.root / f"{slug}--{str(project.id)[:8]}").resolve()
        if self.root not in folder.parents:
            raise ValueError("invalid project workspace path")
        return folder


def _write_text(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _write_json(path: Path, payload: Any) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _workspace_readme(title: str, project_id: UUID) -> str:
    return f"""# {title}

项目 ID：`{project_id}`

此目录由长篇小说工作台生成，用于分书保存可读镜像：

- `formal/`：当前正式正文与 StoryState。
- `sessions/`：候选写作会话、技术分块、封存稿和审核包。
- `backups/latest.json`：最近一次同步时生成的可验证作品备份。

PostgreSQL 仍是正式故事唯一事实源。直接修改这里的文件不会改变工作台中的正式版本。
"""
