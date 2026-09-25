from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, any_, bindparam, delete, or_, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.engine import Database
from novel_writer.db.models import Base, ProjectDeletionRecord
from novel_writer.services.canonical_json import canonical_json_sha256
from novel_writer.services.errors import ConflictError, NotFoundError, WorkflowError
from novel_writer.services.idempotency import command_key_hash
from novel_writer.services.project_deletion_files import (
    DeletionRoots,
    cleanup_files,
    collect_files,
)
from novel_writer.services.project_deletion_inventory import (
    REFLECTED_TABLES,
    DeletionInventory,
    assert_no_file_producers,
    inventory_tables,
    lock_deletion_maintenance,
    read_inventory,
)
from novel_writer.services.retrieval.engine import purge_project_indexes

EXCLUSIONS = [
    "其他小说、全局配置和共享资源不会删除；共享引用可能阻止删除",
    "作者自行导出的文件、原始参考书、日志及仓库外备份不自动清理",
    "无法归属到此作品的历史孤立文件不自动清理",
    "仅保留不含作品名称、正文、提示词或原始响应的删除凭证和费用摘要",
    "这是应用层删除，不是磁盘安全擦除；数据库空间由 PostgreSQL 后续回收复用",
]


class ProjectDeletionService:
    def __init__(self, database: Database, roots: DeletionRoots) -> None:
        self.database = database
        self.roots = roots

    async def preview(self, session: AsyncSession, project_id: UUID) -> dict[str, Any]:
        inventory = await read_inventory(session, project_id)
        projects = inventory.table_rows("story_projects")
        if not projects:
            raise NotFoundError("project not found")
        files = await asyncio.to_thread(collect_files, self.roots, inventory, project_id)
        blockers = inventory.blockers()
        try:
            assert_no_file_producers(inventory.rows)
        except ConflictError as error:
            blockers.append(str(error))
        return {
            "project_id": str(project_id),
            "title": projects[0].values["title"],
            "binding_sha256": _binding(inventory, files),
            "counts": inventory.counts,
            "file_count": len(files["files"]),
            "estimated_file_bytes": files["estimated_file_bytes"],
            "shared_files_preserved": files["shared_files_preserved"],
            "cost_summary": inventory.cost_summary,
            "blockers": sorted(set(blockers)),
            "exclusions": EXCLUSIONS,
        }

    async def delete_project(
        self,
        project_id: UUID,
        confirmed_title: str,
        binding_sha256: str,
        key: str,
    ) -> dict[str, Any]:
        fingerprint = canonical_json_sha256(
            {
                "project_id": str(project_id),
                "confirmed_title": confirmed_title,
                "binding_sha256": binding_sha256,
            }
        )
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        async with self.database.session() as session, session.begin():
            await lock_deletion_maintenance(session)
            existing = await session.scalar(
                select(ProjectDeletionRecord).where(
                    or_(
                        ProjectDeletionRecord.project_id == project_id,
                        ProjectDeletionRecord.request_key_sha256 == key_hash,
                    )
                )
            )
            if existing is not None:
                if (
                    existing.project_id != project_id
                    or existing.request_sha256 != fingerprint
                    or existing.request_key_sha256 != key_hash
                ):
                    raise ConflictError("删除请求与已保存的确认不一致，请查看删除凭证")
                return deletion_response(existing)
            inventory = await read_inventory(session, project_id)
            projects = inventory.table_rows("story_projects")
            if not projects:
                raise NotFoundError("project not found")
            if confirmed_title != projects[0].values["title"]:
                raise WorkflowError("输入的作品名称不一致")
            blockers = inventory.blockers()
            if blockers:
                raise ConflictError("；".join(blockers))
            assert_no_file_producers(inventory.rows)
            files = await asyncio.to_thread(collect_files, self.roots, inventory, project_id)
            if _binding(inventory, files) != binding_sha256:
                raise ConflictError("作品数据或文件已变化，请重新预览并确认删除")
            record = ProjectDeletionRecord(
                project_id=project_id,
                request_key_sha256=key_hash,
                request_sha256=fingerprint,
                counts=inventory.counts,
                cost_summary=inventory.cost_summary,
                cleanup_manifest=files,
                retired_commands=[
                    command_key_hash(row.values["command"], row.values["key"])
                    for row in inventory.table_rows("idempotency_keys")
                ],
            )
            session.add(record)
            try:
                async with session.begin_nested():
                    tables = await inventory_tables(session)
                    for table_name in sorted(REFLECTED_TABLES):
                        for row in inventory.table_rows(table_name):
                            table = tables[table_name]
                            await session.execute(
                                delete(table).where(
                                    and_(
                                        *(
                                            column == value
                                            for column, value in zip(
                                                table.primary_key, row.key, strict=True
                                            )
                                        )
                                    )
                                )
                            )
                    for table in reversed(Base.metadata.sorted_tables):
                        owned_rows = inventory.table_rows(table.name)
                        if owned_rows:
                            if table.name in {"knowledge_chunks", "knowledge_vectors"}:
                                if any(
                                    row.values["project_id"] != project_id for row in owned_rows
                                ):
                                    raise ConflictError("知识索引归属与删除作品不一致")
                                await session.execute(
                                    delete(table).where(table.c.project_id == project_id)
                                )
                                continue
                            columns = list(table.primary_key)
                            if len(columns) != 1:
                                raise ConflictError("删除归属规则尚未支持此复合主键")
                            column = columns[0]
                            await session.execute(
                                delete(table).where(
                                    column
                                    == any_(
                                        bindparam(
                                            "owned_ids",
                                            [row.key[0] for row in owned_rows],
                                            type_=ARRAY(column.type),
                                        )
                                    )
                                )
                            )
                    await session.flush()
            except IntegrityError as error:
                raise ConflictError("存在未解除的数据引用，整本删除已回滚") from error
            deletion_id = record.id
        return await self.cleanup(deletion_id)

    async def cleanup(self, deletion_id: UUID) -> dict[str, Any]:
        async with self.database.session() as session, session.begin():
            record = await session.get(ProjectDeletionRecord, deletion_id)
            if record is None:
                raise NotFoundError("deletion receipt not found")
            if record.status == "completed":
                return deletion_response(record)
            try:
                await lock_deletion_maintenance(session)
                inventory = await read_inventory(session, record.project_id)
                if inventory.owned:
                    raise ConflictError("被删除作品的数据重新出现，停止清理")
                assert_no_file_producers(inventory.rows)
                if record.cleanup_manifest.get("roots") != self.roots.binding():
                    raise ConflictError("存储根目录已变化，请恢复删除时的目录配置再继续清理")
                await purge_project_indexes(
                    record.project_id,
                    self.roots.content.resolve().parent / "lancedb",
                )
                shared = cleanup_files(self.roots, record.cleanup_manifest, inventory)
            except ConflictError as error:
                record.cleanup_error = str(error)[:80]
            except OSError as error:
                record.cleanup_error = f"文件清理失败（系统错误 {error.errno}），请检查权限或占用"
            except (ValueError, RuntimeError, ImportError):
                record.cleanup_error = "file_or_index_cleanup_failed"
            else:
                record.status = "completed"
                record.completed_at = datetime.now(UTC)
                record.cleanup_error = None
                record.counts = {
                    **record.counts,
                    "shared_files_preserved": shared
                    + int(record.cleanup_manifest.get("shared_files_preserved", 0)),
                }
                record.cleanup_manifest = {}
            await session.flush()
            return deletion_response(record)


def _binding(inventory: DeletionInventory, files: dict[str, Any]) -> str:
    return canonical_json_sha256(
        {
            "schema": "project-deletion-v1",
            "rows": sorted((row.table, row.digest) for row in inventory.owned),
            "files": files,
        }
    )


def deletion_response(record: ProjectDeletionRecord) -> dict[str, Any]:
    return {
        "deletion_id": str(record.id),
        "project_id": str(record.project_id),
        "status": record.status,
        "database_deleted": True,
        "counts": record.counts,
        "cost_summary": record.cost_summary,
        "cleanup_error": record.cleanup_error,
        "created_at": record.created_at.isoformat(),
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
    }
