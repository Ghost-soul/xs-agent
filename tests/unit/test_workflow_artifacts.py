from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import WritingArtifactRecord
from novel_writer.services.canonical_json import canonical_json_sha256
from novel_writer.services.errors import ConflictError
from novel_writer.services.workflow_artifacts import (
    _project_successor,
    resolve_workflow_artifact,
    successor_artifact_kind,
    unavailable_workflow_artifact,
    workflow_artifact_payload,
)


def _pair(stage: str, kind: str) -> tuple[WritingArtifactRecord, WritingArtifactRecord]:
    original = WritingArtifactRecord(
        id=uuid4(),
        session_id=uuid4(),
        stage=stage,
        kind=kind,
        content={
            "availability": {"status": "unavailable", "provider_called": False},
            "candidate_sha256": "a" * 64,
            "result": None,
        },
        created_at=datetime.now(UTC),
    )
    successor = WritingArtifactRecord(
        id=uuid4(),
        session_id=original.session_id,
        stage=stage,
        kind=successor_artifact_kind(stage, kind),
        content={
            "candidate_sha256": "a" * 64,
            "brief" if stage == "brief" else "result": {"evidence": "validated"},
            "workflow_artifact_succession": {
                "contract_version": "workflow-artifact-succession-v1",
                "source_artifact_id": str(original.id),
                "source_content_sha256": canonical_json_sha256(original.content),
                "logical_kind": kind,
            },
        },
        created_at=datetime.now(UTC),
    )
    successor.content["workflow_artifact_succession"]["successor_content_sha256"] = (
        canonical_json_sha256(
            {
                key: value
                for key, value in successor.content.items()
                if key != "workflow_artifact_succession"
            }
        )
    )
    return original, successor


@pytest.mark.parametrize(
    ("stage", "kind"),
    [
        ("brief", "creative_brief"),
        ("directing", "provider_reflection_1"),
        ("memory", "handoff_12"),
        ("checker", "report"),
        ("editor", "round_1"),
        ("editor", "round_2"),
        ("reader", "report"),
        ("reader", "report_g123_abcdef012345"),
    ],
)
@pytest.mark.asyncio
async def test_successor_is_logical_and_immutable(stage: str, kind: str) -> None:
    original, successor = _pair(stage, kind)
    before = deepcopy(original.content)
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = successor
    session.get.return_value = original

    projected = await resolve_workflow_artifact(session, original)

    assert projected is not None
    assert projected.id == successor.id
    assert projected.kind == kind
    assert projected.created_at == successor.created_at
    assert await resolve_workflow_artifact(session, projected) is projected
    assert original.content == before
    assert original.kind == kind
    assert successor.kind != kind
    assert len(successor.kind) <= 30


@pytest.mark.parametrize(
    "damage",
    [
        "source_sha",
        "source_id",
        "session",
        "stage",
        "kind",
        "body",
        "empty",
        "success",
        "snapshot",
        "unusable",
        "malformed_validation",
        "invalid_result_validation",
        "current_input",
        "editor_round",
    ],
)
def test_successor_damage_fails_closed(damage: str) -> None:
    original, successor = (
        _pair("editor", "round_2")
        if damage in {"current_input", "editor_round"}
        else _pair("reader", "report")
    )
    lineage = successor.content["workflow_artifact_succession"]
    if damage == "source_sha":
        lineage["source_content_sha256"] = "f" * 64
    elif damage == "source_id":
        lineage["source_artifact_id"] = str(uuid4())
    elif damage == "session":
        successor.session_id = uuid4()
    elif damage == "stage":
        successor.stage = "checker"
    elif damage == "kind":
        successor.kind = "report"
    elif damage == "body":
        successor.content["candidate_sha256"] = "b" * 64
    elif damage == "empty":
        successor.content["result"] = None
    elif damage == "snapshot":
        original.content["candidate_snapshot_sha256"] = "a" * 64
        successor.content["candidate_snapshot_sha256"] = "b" * 64
        lineage["source_content_sha256"] = canonical_json_sha256(original.content)
    elif damage in {"current_input", "editor_round"}:
        field = "current_sha256" if damage == "current_input" else "round_number"
        original.content[field] = "a" * 64 if damage == "current_input" else 2
        successor.content[field] = "b" * 64 if damage == "current_input" else 1
        lineage["source_content_sha256"] = canonical_json_sha256(original.content)
    elif damage == "unusable":
        successor.content["artifact_validation"] = {"status": "unusable"}
    elif damage == "malformed_validation":
        successor.content["artifact_validation"] = None
    elif damage == "invalid_result_validation":
        successor.content["result"]["artifact_validation"] = "complete"
    else:
        original.content["availability"] = {"status": "available"}
        lineage["source_content_sha256"] = canonical_json_sha256(original.content)

    lineage["successor_content_sha256"] = canonical_json_sha256(
        {
            key: value
            for key, value in successor.content.items()
            if key != "workflow_artifact_succession"
        }
    )

    with pytest.raises(ConflictError):
        _project_successor(original, successor)


@pytest.mark.parametrize("outcome", ["no_safe_patch", "skipped_no_issues", "title_placeholder"])
@pytest.mark.asyncio
async def test_completed_noop_is_not_an_unavailable_slot(outcome: str) -> None:
    original, _ = _pair("editor", "round_1")
    original.content = {"result": {"outcome": outcome, "patches": []}}
    session = AsyncMock(spec=AsyncSession)

    assert not unavailable_workflow_artifact(original)
    assert await resolve_workflow_artifact(session, original) is original
    session.scalar.assert_not_called()


@pytest.mark.asyncio
async def test_missing_successor_retains_the_unavailable_report() -> None:
    original, _ = _pair("reader", "report")
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = None

    assert await resolve_workflow_artifact(session, original) is original
    assert unavailable_workflow_artifact(original)


@pytest.mark.asyncio
async def test_orphan_successor_does_not_become_current() -> None:
    _, successor = _pair("reader", "report")
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = None

    with pytest.raises(ConflictError, match="source is missing"):
        await resolve_workflow_artifact(session, successor)


@pytest.mark.asyncio
async def test_carryover_copy_does_not_claim_the_previous_session_slot() -> None:
    _, successor = _pair("memory", "handoff_1")
    before = deepcopy(successor.content)
    copied = WritingArtifactRecord(
        id=uuid4(),
        session_id=uuid4(),
        stage="memory",
        kind="handoff_carryover_1",
        content={
            **workflow_artifact_payload(successor.content),
            "carryover_source_artifact_id": str(successor.id),
        },
    )
    session = AsyncMock(spec=AsyncSession)

    assert await resolve_workflow_artifact(session, copied) is copied
    assert "workflow_artifact_succession" not in copied.content
    assert successor.content == before
    session.get.assert_not_called()
