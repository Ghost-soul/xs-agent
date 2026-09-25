from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from typing import Any
from uuid import UUID

from sqlalchemy import MetaData, Table, select, text
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import Base
from novel_writer.services.errors import ConflictError

HASH_PATTERN = re.compile(rb"[0-9a-f][0-9a-f]{63,}")
UUID_PATTERN = re.compile(
    r"[0-9a-f][0-9a-f]{7}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)
CHILD_OWNERS = {
    "chapter_provider_locks": ("chapter_id", "chapters"),
    "chapter_briefs": ("chapter_id", "chapters"),
    "brief_planning_sessions": ("chapter_id", "chapters"),
    "chapter_revisions": ("chapter_id", "chapters"),
    "state_deltas": ("revision_id", "chapter_revisions"),
    "evidence_spans": ("delta_id", "state_deltas"),
    "baseline_generation_plans": ("chapter_id", "chapters"),
    "agent_runs": ("chapter_id", "chapters"),
    "agent_artifacts": ("run_id", "agent_runs"),
    "agent_generation_plans": ("chapter_id", "chapters"),
    "agent_model_calls": ("run_id", "agent_runs"),
    "authorized_action_slots": ("authorization_id", "run_authorizations"),
    "run_automation_states": ("run_id", "novel_automation_runs"),
    "reference_style_samples": ("manifest_id", "reference_corpus_manifests"),
    "novel_automation_checkpoints": ("run_id", "novel_automation_runs"),
    "writing_chunks": ("session_id", "writing_sessions"),
    "writing_artifacts": ("session_id", "writing_sessions"),
    "writing_cost_plans": ("session_id", "writing_sessions"),
    "writing_chunk_calls": ("session_id", "writing_sessions"),
    "chapter_title_batch_calls": ("session_id", "writing_sessions"),
    "context_compactions": ("session_id", "writing_sessions"),
}
GLOBAL_TABLES = {
    "idempotency_keys",
    "approvals",
    "agent_prompt_versions",
    "agent_prompt_activations",
    "global_reference_style_defaults",
    "project_deletions",
}
CHECKPOINT_TABLES = {"checkpoints", "checkpoint_blobs", "checkpoint_writes"}
REFLECTED_TABLES = CHECKPOINT_TABLES | {"chapter_provider_locks"}
CALL_TABLES = {
    "generation_calls",
    "writing_chunk_calls",
    "chapter_title_batch_calls",
    "agent_model_calls",
    "baseline_generation_plans",
    "brief_planning_sessions",
    "quality_lab_records",
}


def content_hashes(encoded: bytes) -> set[str]:
    return {value.decode("ascii") for value in HASH_PATTERN.findall(encoded) if len(value) == 64}


@dataclass
class InventoryRow:
    table: str
    key: tuple[Any, ...]
    values: dict[str, Any]
    digest: str
    references: set[str]
    hashes: set[str]


@dataclass
class DeletionInventory:
    rows: list[InventoryRow]
    owned: list[InventoryRow]

    def table_rows(self, table: str) -> list[InventoryRow]:
        return [row for row in self.owned if row.table == table]

    def ids(self, table: str) -> set[UUID]:
        return {row.values["id"] for row in self.table_rows(table)}

    @property
    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.owned:
            counts[row.table] = counts.get(row.table, 0) + 1
        return counts

    @property
    def cost_summary(self) -> dict[str, Any]:
        calls = [row for row in self.owned if row.table in CALL_TABLES]
        known = [
            row.values["actual_cost_cny"]
            for row in calls
            if row.values.get("actual_cost_cny") is not None
        ]
        return {
            "currency": "CNY",
            "recorded_cost": str(sum(known, Decimal("0"))),
            "call_records": len(calls),
            "unknown_cost_records": len(calls) - len(known),
        }

    def blockers(self) -> list[str]:
        blockers: set[str] = set()
        owned_keys = {(row.table, row.key) for row in self.owned}
        owned_ids: dict[str, set[UUID]] = {}
        for row in self.owned:
            identifier = row.values.get("id")
            if isinstance(identifier, UUID):
                owned_ids.setdefault(row.table, set()).add(identifier)
        for row in self.rows:
            if (row.table, row.key) not in owned_keys:
                if row.table not in Base.metadata.tables:
                    continue
                for foreign in Base.metadata.tables[row.table].foreign_keys:
                    if row.values.get(foreign.parent.name) in owned_ids.get(
                        foreign.column.table.name, set()
                    ):
                        blockers.add("存在其他作品或全局共享配置引用，请先解除共享引用")
                continue
            status = row.values.get("status")
            if row.table in CALL_TABLES and status in {
                "executing",
                "running",
                "outcome_uncertain",
                "pending",
                "unknown",
            }:
                blockers.add("作品仍有执行中或结果不确定的模型请求")
            if row.table == "local_tasks" and status in {"queued", "running"}:
                blockers.add("作品仍有排队或运行中的本地任务，请等待任务结束")
            if row.table == "generation_batches" and status in {"queued", "running"}:
                blockers.add("作品仍有已授权的创作任务，请先暂停并等待在途请求结束")
            if row.table == "run_automation_states" and (
                status in {"preparing", "queued", "running", "executing"} or _live_lease(row.values)
            ):
                blockers.add("作品自动运行尚未停止或仍持有有效租约，请先停止运行")
        return sorted(blockers)


async def lock_deletion_maintenance(session: AsyncSession) -> None:
    names = await _database_tables(session)
    tables = ", ".join('"' + name.replace('"', '""') + '"' for name in sorted(names))
    try:
        async with session.begin_nested():
            await session.execute(text(f"LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE NOWAIT"))
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) != "55P03":
            raise
        raise ConflictError("数据库仍有其他操作，请等待操作结束后重新清理") from error


def assert_no_file_producers(rows: list[InventoryRow]) -> None:
    for row in rows:
        status = row.values.get("status")
        if (
            row.table in CALL_TABLES
            and status in {"executing", "running"}
            or row.table == "local_tasks"
            and status == "running"
            or row.table == "run_automation_states"
            and (status in {"preparing", "running", "executing"} or _live_lease(row.values))
        ):
            raise ConflictError("仍有后台操作可能写入共享文件，请停止或等待其完成后清理")


async def read_inventory(session: AsyncSession, project_id: UUID) -> DeletionInventory:
    rows: list[InventoryRow] = []
    tables = await inventory_tables(session)
    for table in tables.values():
        if table.name == "project_deletions":
            continue
        if not (
            table.name in GLOBAL_TABLES
            or table.name in CHILD_OWNERS
            or table.name in CHECKPOINT_TABLES
            or table.name == "story_projects"
            or "project_id" in table.c
            or "finalized_project_id" in table.c
        ):
            raise ConflictError(f"删除归属规则未覆盖数据表：{table.name}")
        result = await session.stream(select(table))
        async for records in result.mappings().partitions(256):
            rows.extend(await asyncio.to_thread(_inventory_rows, table, records))
    owned: dict[tuple[str, tuple[Any, ...]], InventoryRow] = {}
    while True:
        previous_count = len(owned)
        ids = {str(value) for row in owned.values() for value in row.key if isinstance(value, UUID)}
        for row in rows:
            belongs = (
                row.table == "story_projects"
                and row.values.get("id") == project_id
                or row.values.get("project_id") == project_id
                or row.values.get("finalized_project_id") == project_id
            )
            if row.table in CHILD_OWNERS:
                field, parent = CHILD_OWNERS[row.table]
                belongs = (parent, (row.values.get(field),)) in owned
            if row.table == "idempotency_keys":
                belongs = bool(row.references & ids)
            if row.table == "approvals":
                belongs = str(row.values.get("target_id")) in ids
            if row.table in CHECKPOINT_TABLES:
                thread_id = str(row.values.get("thread_id", ""))
                belongs = bool({value.lower() for value in UUID_PATTERN.findall(thread_id)} & ids)
            if belongs:
                owned[(row.table, row.key)] = row
        if len(owned) == previous_count:
            break
    return DeletionInventory(rows, list(owned.values()))


def _inventory_rows(table: Table, records: Sequence[RowMapping]) -> list[InventoryRow]:
    rows: list[InventoryRow] = []
    for record in records:
        encoded = json.dumps(dict(record), default=str, sort_keys=True, ensure_ascii=False).encode(
            "utf-8"
        )
        values = {
            name: value
            for name, value in record.items()
            if not isinstance(value, dict | list | bytes)
            and (
                not isinstance(value, str)
                or name in {"title", "storage_key", "status", "lease_owner"}
                or len(value) <= 200
            )
        }
        reference_text = encoded.decode()
        if table.name == "idempotency_keys":
            reference_text = json.dumps(
                {name: record[name] for name in ("command", "resource_scope", "response")},
                default=str,
            )
        rows.append(
            InventoryRow(
                table=table.name,
                key=tuple(record[column.name] for column in table.primary_key),
                values=values,
                digest=hashlib.sha256(encoded).hexdigest(),
                references={value.lower() for value in UUID_PATTERN.findall(reference_text)},
                hashes=content_hashes(encoded),
            )
        )
    return rows


async def _database_tables(session: AsyncSession) -> set[str]:
    return set(
        await session.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
    )


async def inventory_tables(session: AsyncSession) -> dict[str, Table]:
    names = await _database_tables(session)
    extras = names - set(Base.metadata.tables)
    if extras - REFLECTED_TABLES - {"alembic_version", "checkpoint_migrations"}:
        raise ConflictError("数据库存在尚未盘点的数据表，拒绝整本删除")
    tables = dict(Base.metadata.tables)
    # These derived tables can be absent until the separately authorized index migration.
    for name in {"knowledge_indexes", "knowledge_chunks", "knowledge_vectors"} - names:
        tables.pop(name, None)
    connection = await session.connection()
    for name in sorted(extras & REFLECTED_TABLES):
        tables[name] = await connection.run_sync(partial(_reflect_table, name=name))
    return tables


def _reflect_table(connection: Connection, *, name: str) -> Table:
    return Table(name, MetaData(), autoload_with=connection)


def _live_lease(values: dict[str, Any]) -> bool:
    expires = values.get("lease_expires_at")
    return isinstance(expires, datetime) and expires > datetime.now(UTC)
