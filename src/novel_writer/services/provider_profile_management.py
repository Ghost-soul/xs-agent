from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import (
    AgentCallRecord,
    AuthorizedActionSlotRecord,
    GenerationBatchRecord,
    GenerationCallRecord,
    RunAutomationRecord,
)
from novel_writer.services.errors import ConflictError


async def guard_profile_deletion(session: AsyncSession, profile_id: str) -> None:
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "LOCK TABLE writing_chunk_calls, authorized_action_slots, "
                    "run_automation_states, generation_batches, generation_calls "
                    "IN SHARE ROW EXCLUSIVE MODE NOWAIT"
                )
            )
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) != "55P03":
            raise
        raise ConflictError("正在准备或执行其他操作，请等待结束后再删除配置") from error
    call = await session.scalar(
        select(AgentCallRecord.id)
        .where(
            AgentCallRecord.provider == profile_id,
            AgentCallRecord.status.in_(("executing", "outcome_uncertain")),
        )
        .limit(1)
    )
    if call is not None:
        raise ConflictError("该配置仍有执行中或结果不确定的调用，请先完成对账；不会取消或重试调用")
    generation_call = await session.scalar(select(GenerationCallRecord.id).where(
        GenerationCallRecord.provider == profile_id,
        GenerationCallRecord.status.in_(("executing", "outcome_uncertain")),
    ).limit(1))
    generation_batch = await session.scalar(select(GenerationBatchRecord.id).where(
        GenerationBatchRecord.spec["profile_id"].astext == profile_id,
        GenerationBatchRecord.status.in_(("queued", "running")),
    ).limit(1))
    if generation_call or generation_batch:
        raise ConflictError("配置仍被新创作流程使用，请先暂停并核对在途请求")
    active = await session.scalar(
        select(AuthorizedActionSlotRecord.id)
        .join(
            RunAutomationRecord,
            RunAutomationRecord.authorization_id == AuthorizedActionSlotRecord.authorization_id,
        )
        .where(
            AuthorizedActionSlotRecord.provider == profile_id,
            or_(
                RunAutomationRecord.status.in_(
                    ("preparing", "queued", "running", "executing", "stopping")
                ),
                RunAutomationRecord.lease_expires_at > func.now(),
            ),
        )
        .limit(1)
    )
    if active is not None:
        raise ConflictError("该配置仍被后台自动运行使用，请先停止相关作品并等待租约结束")
