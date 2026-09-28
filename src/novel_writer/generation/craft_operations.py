"""Author-only local changes with parent, manuscript, and remaining-call bindings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from novel_writer.db.models import GenerationCallRecord
from novel_writer.generation.content import fingerprint
from novel_writer.generation.craft_models import enabled
from novel_writer.generation.request_preparation import InputCapacityError, prepare_request
from novel_writer.services.errors import ConflictError

if TYPE_CHECKING:
    from novel_writer.db.models import GenerationBatchRecord
    from novel_writer.generation.schemas import GenerationSpec
    from novel_writer.generation.service import GenerationService


async def assert_idle(service: GenerationService, batch: GenerationBatchRecord) -> None:
    from novel_writer.generation.memory_recovery import resolved_failures

    calls = list(
        await service.session.scalars(
            select(GenerationCallRecord).where(GenerationCallRecord.batch_id == batch.id)
        )
    )
    resolved = await resolved_failures(service, batch, calls)
    if any(c.status != "completed" and str(c.id) not in resolved for c in calls):
        raise ConflictError("仍有在途、未知或未恢复调用，请先处理原步骤")


async def preview_remaining(
    service: GenerationService, batch: GenerationBatchRecord, spec: GenerationSpec, parent_sha: str
) -> None:
    if not enabled(spec) or not batch.next_action:
        return
    from novel_writer.generation.knowledge_binding import bind_request
    from novel_writer.generation.longform import prepared_reports, units_for

    plan = await service.artifact(batch, "plan")
    candidate = await service.artifact(batch, "candidate")
    note = await service.artifact(batch, "plan_author_note")
    assert plan
    action = batch.next_action
    reports = await prepared_reports(service, batch, action)
    prose = candidate.payload["body"] if candidate else None
    text_note = note.payload["note"] if note else None
    await bind_request(service, batch, spec, action, plan.payload, prose, reports, text_note)
    blockers = []
    try:
        request, count, _, _ = prepare_request(
            spec, batch.snapshot, action, plan.payload, prose, text_note, reports, {}
        )
    except InputCapacityError as error:
        request, count = error.request, None
        blockers.append(str(error))
    items = await units_for(service, batch)
    await service.append(
        batch,
        "plan_adjustment",
        {
            "parent_plan_sha256": parent_sha,
            "plan_sha256": plan.sha256,
            "candidate_sha256": candidate.sha256 if candidate else None,
            "unit_chain_sha256": fingerprint(items),
            "written_units": len(items),
            "remaining_unit_slots": spec.unit_limit - len(items),
            "max_cost_cny": str(spec.max_cost_cny),
            "action": action,
            "model_request_sha256": fingerprint(request.model_dump(mode="json")),
            "input_count": count,
            "blockers": blockers,
            "knowledge_retrieval": reports.get("knowledge_retrieval"),
            "author_note_sha256": note.sha256 if note else None,
            "scope": "unwritten-only-original-capacity-and-cost",
        },
    )


async def validate_adjustment(
    service: GenerationService,
    batch: GenerationBatchRecord,
    expected_plan: str | None,
    expected_adjustment: str | None = None,
) -> None:
    item = await service.artifact(batch, "plan_adjustment")
    if not item or item.payload.get("action") != batch.next_action:
        return
    if expected_adjustment != item.sha256:
        raise ConflictError("剩余请求预览已经改变，请重新确认本次范围与费用")
    from novel_writer.generation.longform import units_for

    plan = await service.artifact(batch, "plan")
    candidate = await service.artifact(batch, "candidate")
    note = await service.artifact(batch, "plan_author_note")
    if not plan or expected_plan != plan.sha256 or item.payload["plan_sha256"] != plan.sha256:
        raise ConflictError("请确认当前剩余计划；旧确认不能启动修改后的方案")
    if item.payload.get("author_note_sha256") != (note.sha256 if note else None):
        raise ConflictError("作者要求已变化，请重新预检剩余计划")
    if item.payload["candidate_sha256"] != (
        candidate.sha256 if candidate else None
    ) or item.payload["unit_chain_sha256"] != fingerprint(await units_for(service, batch)):
        raise ConflictError("正文或接力已变化，请重新预检剩余计划")
    if item.payload["blockers"]:
        raise ConflictError("剩余请求预检未通过，请核对输入容量与费用")
