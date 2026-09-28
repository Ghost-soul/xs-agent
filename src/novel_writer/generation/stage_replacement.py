"""Replace inactive draft stages without rewriting their responses or accounting."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import and_, or_, select

from novel_writer.db.models import GenerationBatchRecord, GenerationCallRecord
from novel_writer.services.errors import ConflictError

if TYPE_CHECKING:
    from novel_writer.generation.service import GenerationService


async def require_idle(service: GenerationService, project_id: UUID) -> None:
    """Caller holds the project lock also used when claiming the next model call."""
    active = await service.session.scalar(
        select(GenerationCallRecord).join(
            GenerationBatchRecord, GenerationBatchRecord.id == GenerationCallRecord.batch_id,
        ).where(
            GenerationCallRecord.project_id == project_id,
            or_(
                GenerationCallRecord.status == "executing",
                and_(
                    GenerationCallRecord.status == "response_saved",
                    GenerationBatchRecord.status.not_in(("adopted", "archived")),
                ),
            ),
        ).limit(1)
    )
    if active is not None:
        raise ConflictError(
            f"旧阶段 {active.batch_id} 的 {active.action} 仍在执行或处理响应；"
            "请先请求暂停，等待本次响应保存处理完成后再新建覆盖，旧阶段尚未修改"
        )


async def replace_previous(service: GenerationService, new: GenerationBatchRecord) -> None:
    """One transaction covers a viable new preview, history receipts and retirement."""
    if new.snapshot.get("blockers"):
        return
    await require_idle(service, new.project_id)
    previous = list(await service.session.scalars(
        select(GenerationBatchRecord).where(
            GenerationBatchRecord.project_id == new.project_id,
            GenerationBatchRecord.id != new.id,
            GenerationBatchRecord.status.not_in(("adopted", "archived")),
        ).order_by(GenerationBatchRecord.created_at, GenerationBatchRecord.id).with_for_update()
    ))
    if not previous:
        return
    unknown = list(await service.session.scalars(
        select(GenerationCallRecord).where(
            GenerationCallRecord.batch_id.in_([b.id for b in previous]),
            GenerationCallRecord.status == "outcome_uncertain",
        ).order_by(GenerationCallRecord.started_at, GenerationCallRecord.id)
    ))
    replaced = []
    for old in previous:
        previous_status = old.status
        calls = [str(c.id) for c in unknown if c.batch_id == old.id]
        await service.append(old, "stage_superseded", {
            "replacement_batch_id": str(new.id),
            "previous_status": previous_status,
            "previous_next_action": old.next_action,
            "previous_authorized": old.authorized,
            "previous_pause_requested": old.pause_requested,
            "unknown_call_ids": calls,
            "reason": "author_new_stage_replaces_previous",
            "provider_outcome_confirmed": False,
        })
        old.status, old.next_action = "archived", None
        old.authorized, old.pause_requested = False, True
        old.state = {**old.state, "message": "已由新阶段接替；原正文、响应和费用记录保留"}
        replaced.append({"batch_id": str(old.id), "previous_status": previous_status})
    await service.append(new, "stage_replacement", {
        "replaced_stages": replaced,
        "unknown_call_ids": [str(c.id) for c in unknown],
        "unknown_cost_call_ids": [str(c.id) for c in unknown if c.actual_cost_cny is None],
        "historical_costs_in_new_budget": False,
    })
