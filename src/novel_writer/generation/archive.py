"""Portable generation evidence. Restore never recreates dispatchable calls or authority."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import (
    GenerationArtifactRecord,
    GenerationBatchRecord,
    GenerationCallRecord,
)
from novel_writer.generation.content import fingerprint


async def export_history(session: AsyncSession, project_id: UUID) -> list[dict[str, Any]]:
    result = []
    for batch in await session.scalars(
        select(GenerationBatchRecord)
        .where(
            GenerationBatchRecord.project_id == project_id,
        )
        .order_by(GenerationBatchRecord.created_at)
    ):
        artifacts = list(
            await session.scalars(
                select(GenerationArtifactRecord).where(
                    GenerationArtifactRecord.batch_id == batch.id
                )
            )
        )
        original = next((a for a in artifacts if a.kind == "restored_archive"), None)
        if original is not None:
            result.append(original.payload["original"])
            continue
        calls = list(
            await session.scalars(
                select(GenerationCallRecord)
                .where(GenerationCallRecord.batch_id == batch.id)
                .order_by(GenerationCallRecord.slot)
            )
        )
        result.append(
            {
                "id": str(batch.id),
                "revision": batch.revision,
                "spec": batch.spec,
                "snapshot": batch.snapshot,
                "state": batch.state,
                "status": batch.status,
                "preview_sha256": batch.preview_sha256,
                "artifacts": [
                    {"id": str(a.id), "kind": a.kind, "payload": a.payload, "sha256": a.sha256}
                    for a in artifacts
                ],
                "calls": [
                    {
                        "id": str(c.id),
                        "slot": c.slot,
                        "action": c.action,
                        "status": c.status,
                        "provider": c.provider,
                        "model": c.model,
                        "request": c.request,
                        "request_sha256": c.request_sha256,
                        "response": c.response,
                        "error_code": c.error_code,
                        "actual_cost_cny": str(c.actual_cost_cny)
                        if c.actual_cost_cny is not None
                        else None,
                        "started_at": c.started_at.isoformat(),
                        "finished_at": c.finished_at.isoformat() if c.finished_at else None,
                    }
                    for c in calls
                ],
            }
        )
    return result


async def restore_history(
    session: AsyncSession, project_id: UUID, version_id: UUID, history: list[dict[str, Any]]
) -> None:
    for original in history:
        batch = GenerationBatchRecord(
            project_id=project_id,
            base_version_id=version_id,
            revision=original["revision"],
            spec=original["spec"],
            snapshot=original["snapshot"],
            preview_sha256=original["preview_sha256"],
            status="archived",
            authorized=False,
            pause_requested=True,
            next_action=None,
            state={"message": "恢复的历史证据，只读且不继承模型授权"},
        )
        session.add(batch)
        await session.flush()
        payload = {"original": original}
        session.add(
            GenerationArtifactRecord(
                project_id=project_id,
                batch_id=batch.id,
                kind="restored_archive",
                payload=payload,
                sha256=fingerprint(payload),
            )
        )
        pointers = {}
        for item in original["artifacts"]:
            artifact = GenerationArtifactRecord(
                project_id=project_id,
                batch_id=batch.id,
                kind=item["kind"],
                payload=item["payload"],
                sha256=item["sha256"],
            )
            if fingerprint(artifact.payload) != artifact.sha256:
                raise ValueError("备份中的生成工件 SHA 不一致")
            session.add(artifact)
            await session.flush()
            if original["state"].get(f"{artifact.kind}_id") == item["id"]:
                pointers[f"{artifact.kind}_id"] = str(artifact.id)
        batch.state = {**batch.state, **pointers}
