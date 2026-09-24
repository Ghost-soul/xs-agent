"""Explicit, offline, copy-only relocation between database URL namespaces.

Preview reads the destination DB. Apply rechecks the entire inventory before
copying anything, preserves the original receipts and never writes DB rows.
The runtime later persists them using the same exact binding checks.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from novel_writer.core.private_files import atomic_private_write
from novel_writer.db.models import GenerationBatchRecord, GenerationCallRecord
from novel_writer.generation.content import fingerprint, json_text
from novel_writer.generation.response_binding import validate_response_binding
from novel_writer.generation.response_journal import ResponseJournal
from novel_writer.services.errors import WorkflowError


def preview_migration(
    session: Session, source: ResponseJournal, target: ResponseJournal
) -> dict[str, Any]:
    if source.root.resolve() == target.root.resolve():
        raise WorkflowError("源和目标响应缓冲相同，无需迁移")
    for root in (source.root, target.root):
        if root.is_symlink() or root.parent.is_symlink():
            raise WorkflowError("迁移目录不能使用符号链接")
    items = []
    for path in source.pending():
        entry = source.read(path)
        call_id, batch_id = UUID(entry["call_id"]), UUID(entry["batch_id"])
        batch = session.get(GenerationBatchRecord, batch_id)
        call = session.get(GenerationCallRecord, call_id)
        validate_response_binding(batch, call, entry)
        assert batch is not None and call is not None
        destination = target.root / path.name
        if (destination.exists() or destination.is_symlink()) and target.read(destination) != entry:
            raise WorkflowError("目标缓冲已有不同收据，拒绝覆盖")
        items.append({
            "batch_id": str(batch_id),
            "call_id": str(call_id),
            "batch_preview_sha256": batch.preview_sha256,
            "request_sha256": call.request_sha256,
            "model_request_sha256": entry["model_request_sha256"],
            "response_sha256": entry["response"]["sha256"],
            "receipt_sha256": entry["sha256"],
        })
    if not items:
        raise WorkflowError("指定源地址没有待转移响应，未扫描其他数据库目录")
    value = {
        "version": "response-relocation-v1",
        "target_database": dict(session.execute(text(
            "SELECT datname AS name, oid FROM pg_database WHERE datname = current_database()"
        )).mappings().one()),
        "source_root": str(source.root.resolve()),
        "target_root": str(target.root.resolve()),
        "items": items,
    }
    return {**value, "sha256": fingerprint(value)}


def apply_migration(
    session: Session,
    source: ResponseJournal,
    target: ResponseJournal,
    plan: dict[str, Any],
    approved_sha256: str,
    audit_root: Path,
) -> dict[str, Any]:
    """Caller must hold storage leases and offline_database for the target."""
    if plan.get("sha256") != approved_sha256 or approved_sha256 != fingerprint(
        {k: v for k, v in plan.items() if k != "sha256"}
    ):
        raise WorkflowError("迁移预览摘要与确认值不一致")
    current = preview_migration(session, source, target)
    if current != plan:
        raise WorkflowError("迁移来源或目标绑定已变化，请重新预览")
    receipt: dict[str, Any] = {
        "plan": plan,
        "started_at": datetime.now(UTC).isoformat(),
        "status": "copying",
        "originals_preserved": True,
        "database_rows_modified": False,
    }
    audit = audit_root / f"{approved_sha256}-{uuid4()}.json"
    atomic_private_write(audit, json_text(receipt).encode("utf-8"))
    for item in plan["items"]:
        filename = f"{UUID(item['call_id'])}.json"
        path = source.root / filename
        entry = source.read(path)
        if entry["sha256"] != item["receipt_sha256"]:
            raise WorkflowError("源缓冲在复制期间变化，原文件保留，请重新核对")
        destination = target.root / filename
        if destination.exists() or destination.is_symlink():
            if target.read(destination) != entry:
                raise WorkflowError("目标缓冲已变化，拒绝覆盖")
        else:
            # Preserve received_at as well as the original response and fees.
            atomic_private_write(destination, json_text(entry).encode("utf-8"))
        if target.read(destination) != entry:
            raise WorkflowError("转移后校验失败；原缓冲保留")
    receipt.update(status="copied", completed_at=datetime.now(UTC).isoformat())
    atomic_private_write(audit, json_text(receipt).encode("utf-8"))
    return receipt
