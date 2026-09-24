from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any, cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import IdempotencyKeyRecord, ProjectDeletionRecord
from novel_writer.services.canonical_json import canonical_json_sha256
from novel_writer.services.errors import ConflictError

IDEMPOTENCY_REQUEST_SCHEMA_VERSION = "idempotency-request-v1"


def request_fingerprint(command: str, payload: Any) -> str:
    return canonical_json_sha256({"command": command, "request": payload})


def command_key_hash(command: str, key: str) -> str:
    return canonical_json_sha256({"command": command, "key": key})


async def load_idempotent_response(
    session: AsyncSession,
    command: str,
    key: str,
    request_payload: Any,
    *,
    resource_scope: str,
    reject_other_commands: bool = False,
) -> dict[str, Any] | None:
    await _acquire_idempotency_lock(session, key)
    if reject_other_commands:
        await _assert_key_command(session, command, key)
    record = await _record(session, command, key)
    if record is None or not hasattr(record, "response"):
        return None
    _validate_bound_record(record, command, request_payload, resource_scope)
    return deepcopy(record.response)


async def save_idempotent_response(
    session: AsyncSession,
    command: str,
    key: str,
    response: dict[str, Any],
    request_payload: Any,
    *,
    resource_scope: str,
    reject_other_commands: bool = False,
) -> None:
    await _acquire_idempotency_lock(session, key)
    if reject_other_commands:
        await _assert_key_command(session, command, key)
    existing = await _record(session, command, key, for_update=True)
    if existing is not None and hasattr(existing, "response"):
        _validate_bound_record(existing, command, request_payload, resource_scope)
        if existing.response != response:
            raise ConflictError("idempotent request completed with a different response")
        return
    session.add(
        IdempotencyKeyRecord(
            command=command,
            key=key,
            request_fingerprint_sha256=request_fingerprint(command, request_payload),
            request_schema_version=IDEMPOTENCY_REQUEST_SCHEMA_VERSION,
            resource_scope=resource_scope,
            response=deepcopy(response),
        )
    )
    await session.flush()






async def _record(
    session: AsyncSession,
    command: str,
    key: str,
    *,
    for_update: bool = False,
) -> IdempotencyKeyRecord | None:
    statement = select(IdempotencyKeyRecord).where(
        IdempotencyKeyRecord.command == command,
        IdempotencyKeyRecord.key == key,
    )
    if for_update:
        statement = statement.with_for_update()
    record = cast(IdempotencyKeyRecord | None, await session.scalar(statement))
    if record is None:
        retired = await session.scalar(select(ProjectDeletionRecord.id).where(
            ProjectDeletionRecord.retired_commands.contains([command_key_hash(command, key)])
        ).limit(1))
        if retired is not None:
            raise ConflictError("此操作所属作品已永久删除，旧请求不能重新执行")
    return record


async def _assert_key_command(session: AsyncSession, command: str, key: str) -> None:
    other_command = await session.scalar(
        select(IdempotencyKeyRecord.command)
        .where(
            IdempotencyKeyRecord.key == key,
            IdempotencyKeyRecord.command != command,
        )
        .limit(1)
    )
    if other_command is not None:
        raise ConflictError("idempotency key was already used for a different resource")


def _validate_bound_record(
    record: IdempotencyKeyRecord,
    command: str,
    request_payload: Any,
    resource_scope: str,
) -> None:
    saved_fingerprint = getattr(record, "request_fingerprint_sha256", None)
    saved_schema = getattr(record, "request_schema_version", None)
    saved_scope = getattr(record, "resource_scope", None)
    if saved_fingerprint is None or saved_schema is None or saved_scope is None:
        raise ConflictError(
            "idempotency key belongs to a legacy unbound request; use a new key"
        )
    if saved_schema != IDEMPOTENCY_REQUEST_SCHEMA_VERSION:
        raise ConflictError("idempotency key uses a different request schema")
    if saved_scope != resource_scope:
        raise ConflictError("idempotency key was already used for a different resource")
    if saved_fingerprint != request_fingerprint(command, request_payload):
        raise ConflictError("idempotency key was already used with a different request payload")


async def _acquire_idempotency_lock(
    session: AsyncSession,
    key: str,
) -> None:
    digest = hashlib.sha256(key.encode()).digest()
    lock_id = int.from_bytes(digest[:8], byteorder="big", signed=True)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:idempotency_lock_id)"),
        {"idempotency_lock_id": lock_id},
    )
