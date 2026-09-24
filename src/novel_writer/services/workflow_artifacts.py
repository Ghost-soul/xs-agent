from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import WritingArtifactRecord
from novel_writer.services.artifact_validation import ArtifactValidation
from novel_writer.services.canonical_json import canonical_json_sha256
from novel_writer.services.errors import ConflictError

_SUCCESSION = "workflow_artifact_succession"
_STAGES = {"brief", "directing", "memory", "checker", "editor", "reader"}
_BINDINGS = (
    "candidate_sha256",
    "candidate_snapshot_sha256",
    "source_raw_sha256",
    "original_sha256",
    "current_sha256",
    "round_number",
    "raw_sha256",
    "chunk_sha256",
    "chunk_ordinal",
    "reflection_index",
)


def unavailable_workflow_artifact(artifact: WritingArtifactRecord | None) -> bool:
    if artifact is None or artifact.stage not in _STAGES:
        return False
    availability = artifact.content.get("availability")
    return isinstance(availability, dict) and availability.get("status") == "unavailable"


def successor_artifact_kind(stage: str, kind: str) -> str:
    digest = hashlib.sha256(f"{stage}:{kind}".encode()).hexdigest()[:9]
    return f"{kind[:20]}~{digest}"


def workflow_artifact_payload(content: dict[str, Any]) -> dict[str, Any]:
    """Copy logical content without transferring another slot's succession identity."""

    return {key: value for key, value in content.items() if key != _SUCCESSION}


def _check_bindings(original: dict[str, Any], successor: dict[str, Any]) -> None:
    for field in _BINDINGS:
        if original.get(field) is not None and successor.get(field) != original[field]:
            raise ConflictError(f"workflow artifact successor changed {field}")


def _usable_validation(content: dict[str, Any]) -> bool:
    if "artifact_validation" not in content:
        return True
    try:
        return (
            ArtifactValidation.model_validate(content["artifact_validation"]).status != "unusable"
        )
    except ValueError:
        return False


def _project_successor(
    original: WritingArtifactRecord, successor: WritingArtifactRecord
) -> WritingArtifactRecord:
    lineage = successor.content.get(_SUCCESSION)
    result = successor.content.get("brief" if original.stage == "brief" else "result")
    expected = {
        "contract_version": "workflow-artifact-succession-v1",
        "source_artifact_id": str(original.id),
        "source_content_sha256": canonical_json_sha256(original.content),
        "logical_kind": original.kind,
        "successor_content_sha256": canonical_json_sha256(
            workflow_artifact_payload(successor.content)
        ),
    }
    if (
        not unavailable_workflow_artifact(original)
        or unavailable_workflow_artifact(successor)
        or lineage != expected
        or successor.session_id != original.session_id
        or successor.stage != original.stage
        or successor.kind != successor_artifact_kind(original.stage, original.kind)
        or not isinstance(result, dict)
        or not result
        or not _usable_validation(successor.content)
        or not _usable_validation(result)
    ):
        raise ConflictError("workflow artifact succession is invalid")
    _check_bindings(original.content, successor.content)
    projected = WritingArtifactRecord(
        id=successor.id,
        session_id=successor.session_id,
        stage=successor.stage,
        kind=original.kind,
        content=dict(successor.content),
        created_at=successor.created_at,
    )
    projected.__dict__["_workflow_logical_projection"] = True
    return projected


async def resolve_workflow_artifact(
    session: AsyncSession, artifact: WritingArtifactRecord | None
) -> WritingArtifactRecord | None:
    """Read an effective artifact without changing the immutable failed placeholder."""

    if artifact is None or getattr(artifact, "_workflow_logical_projection", False):
        return artifact
    lineage = artifact.content.get(_SUCCESSION)
    if lineage is not None:
        if not isinstance(lineage, dict):
            raise ConflictError("workflow artifact succession is invalid")
        try:
            source_id = UUID(str(lineage.get("source_artifact_id")))
        except ValueError as error:
            raise ConflictError("workflow artifact succession source is invalid") from error
        original = await session.get(WritingArtifactRecord, source_id)
        if original is None:
            raise ConflictError("workflow artifact succession source is missing")
        return _project_successor(original, artifact)
    if not unavailable_workflow_artifact(artifact):
        return artifact
    successor = await session.scalar(
        select(WritingArtifactRecord).where(
            WritingArtifactRecord.session_id == artifact.session_id,
            WritingArtifactRecord.stage == artifact.stage,
            WritingArtifactRecord.kind == successor_artifact_kind(artifact.stage, artifact.kind),
        )
    )
    return _project_successor(artifact, successor) if successor is not None else artifact


