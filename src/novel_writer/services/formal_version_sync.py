from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import (
    AgentCallRecord,
    GenerationBatchRecord,
    GenerationCallRecord,
    NovelAutomationCheckpointRecord,
    NovelAutomationRunRecord,
    WritingSessionRecord,
)
from novel_writer.services.errors import ConflictError

_TERMINAL_RUN_STATUSES = {"completed", "cancelled"}


async def invalidate_generation(
    session: AsyncSession, project_id: UUID, reason: str, exempt: UUID | None = None,
    *, nowait: bool = False,
) -> None:
    active = await session.scalar(select(GenerationCallRecord.id).where(
        GenerationCallRecord.project_id == project_id,
        GenerationCallRecord.status.in_(("executing", "outcome_uncertain")),
    ).limit(1))
    if active is not None:
        raise ConflictError("新创作流程仍有执行中或结果未知的模型请求，请先核对")
    try:
        batches = await session.scalars(select(GenerationBatchRecord).where(
            GenerationBatchRecord.project_id == project_id,
            GenerationBatchRecord.status.notin_(("adopted", "archived")),
        ).with_for_update(nowait=nowait))
    except OperationalError as error:
        _raise_formal_lock_conflict(error, nowait)
        raise
    for batch in batches:
        if batch.id == exempt:
            continue
        batch.status = "archived"
        batch.authorized = False
        batch.next_action = None
        batch.state = {**batch.state, "message": reason}


async def invalidate_formal_dependents(
    session: AsyncSession,
    project_id: UUID,
    pause_reason: str,
    *,
    exempt_session_id: UUID | None = None,
    exempt_generation_id: UUID | None = None,
    fail_fast_on_lock_conflict: bool = False,
) -> dict[str, Any]:
    """Archive stale workspaces and cancel unrelated runs before a content/state change."""

    await invalidate_generation(session, project_id, pause_reason, exempt_generation_id,
                                nowait=fail_fast_on_lock_conflict)

    try:
        sessions = list(
            (
                await session.scalars(
                    select(WritingSessionRecord)
                    .where(
                        WritingSessionRecord.project_id == project_id,
                        WritingSessionRecord.archived_at.is_(None),
                        WritingSessionRecord.status != "merged",
                    )
                    .order_by(WritingSessionRecord.id)
                    .with_for_update(nowait=fail_fast_on_lock_conflict)
                )
            ).all()
        )
    except OperationalError as error:
        _raise_formal_lock_conflict(error, fail_fast_on_lock_conflict)
        raise
    if any(item.status == "merging" and item.id != exempt_session_id for item in sessions):
        raise ConflictError("作品仍有创作批次正在合并，暂时不能改变正式版本")
    session_ids = [item.id for item in sessions]
    if session_ids:
        executing_call = await session.scalar(
            select(AgentCallRecord.id).where(
                AgentCallRecord.session_id.in_(session_ids),
                AgentCallRecord.status == "executing",
            )
        )
        if executing_call is not None:
            raise ConflictError("作品仍有模型请求正在执行，暂时不能改变正式版本")
    try:
        runs = list(
            (
                await session.scalars(
                    select(NovelAutomationRunRecord)
                    .where(
                        NovelAutomationRunRecord.project_id == project_id,
                        NovelAutomationRunRecord.status.notin_(tuple(_TERMINAL_RUN_STATUSES)),
                    )
                    .order_by(NovelAutomationRunRecord.id)
                    .with_for_update(nowait=fail_fast_on_lock_conflict)
                )
            ).all()
        )
    except OperationalError as error:
        _raise_formal_lock_conflict(error, fail_fast_on_lock_conflict)
        raise
    archived_at = datetime.now(UTC)
    archived_session_ids: list[str] = []
    for writing in sessions:
        if writing.id == exempt_session_id:
            continue
        writing.archived_at = archived_at
        archived_session_ids.append(str(writing.id))

    cancelled_run_ids: list[str] = []
    for run in runs:
        if exempt_session_id is not None and run.current_session_id == exempt_session_id:
            continue
        state = dict(run.state)
        state.update({"status": "cancelled", "pause_reason": pause_reason})
        run.status = "cancelled"
        run.state = state
        cancelled_run_ids.append(str(run.id))

    return {
        "formal_dependents_synchronized": True,
        "archived_session_count": len(archived_session_ids),
        "archived_session_ids": archived_session_ids,
        "cancelled_run_count": len(cancelled_run_ids),
        "cancelled_run_ids": cancelled_run_ids,
        "audit_preserved": True,
    }


def _raise_formal_lock_conflict(error: OperationalError, enabled: bool) -> None:
    lock_unavailable = getattr(error.orig, "sqlstate", None) == "55P03" or getattr(
        error.orig, "pgcode", None
    ) == "55P03"
    if enabled and lock_unavailable:
        raise ConflictError(
            "another project workflow is changing; refresh before merging"
        ) from error


async def rebase_title_only_dependents(
    session: AsyncSession,
    project_id: UUID,
    previous_version_id: UUID,
    new_version_id: UUID,
    new_version_number: int,
) -> dict[str, Any]:
    """Safely rebase unfinished work because only formal title metadata changed."""

    await invalidate_generation(session, project_id, "正式标题版本已变化，请建立新批次")

    writings = list(
        (
            await session.scalars(
                select(WritingSessionRecord)
                .where(
                    WritingSessionRecord.project_id == project_id,
                    WritingSessionRecord.archived_at.is_(None),
                    WritingSessionRecord.status != "merged",
                )
                .with_for_update()
            )
        ).all()
    )
    writing_by_id = {item.id: item for item in writings}
    runs = list(
        (
            await session.scalars(
                select(NovelAutomationRunRecord)
                .where(
                    NovelAutomationRunRecord.project_id == project_id,
                    NovelAutomationRunRecord.status.in_(("active", "paused")),
                )
                .with_for_update()
            )
        ).all()
    )
    for run in runs:
        if run.base_version_id != previous_version_id:
            raise ConflictError("active NovelRun is already based on another formal version")
        if run.current_session_id is not None:
            writing = writing_by_id.get(run.current_session_id)
            if writing is None or writing.base_version_id != previous_version_id:
                raise ConflictError("active NovelRun and WritingSession baselines are inconsistent")

    rebased_sessions = 0
    for writing in writings:
        if writing.base_version_id == previous_version_id:
            writing.base_version_id = new_version_id
            rebased_sessions += 1
    for run in runs:
        run.base_version_id = new_version_id
        await append_run_checkpoint(
            session,
            run.id,
            "formal_title_version_rebased",
            {
                "previous_version_id": str(previous_version_id),
                "new_version_id": str(new_version_id),
                "new_version": new_version_number,
                "reason": "正式标题版本变化；正文、Revision 与 StoryState 均未改变。",
            },
        )
    return {
        "active_runs_rebased": len(runs),
        "writing_sessions_rebased": rebased_sessions,
    }


async def append_run_checkpoint(
    session: AsyncSession,
    run_id: UUID,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    sequence = (
        int(
            await session.scalar(
                select(func.coalesce(func.max(NovelAutomationCheckpointRecord.sequence), 0)).where(
                    NovelAutomationCheckpointRecord.run_id == run_id
                )
            )
            or 0
        )
        + 1
    )
    session.add(
        NovelAutomationCheckpointRecord(
            run_id=run_id,
            sequence=sequence,
            event={"type": event_type, "payload": payload},
        )
    )
