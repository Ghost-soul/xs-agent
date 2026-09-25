"""Explicitly re-budget unused slots while retaining the completed Chief and its sources."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from novel_writer.db.models import GenerationBatchRecord, GenerationCallRecord
from novel_writer.generation.budget import cost_for, option_for, request_for
from novel_writer.generation.content import fingerprint
from novel_writer.generation.novel import model_for, slots_for
from novel_writer.generation.request_preparation import prepare_request
from novel_writer.generation.schemas import LONGFORM_REVISION, FrozenGenerationSpec, GenerationSpec
from novel_writer.generation.token_limits import INPUT_TOKEN_LIMIT, TOKEN_LIMIT, with_limits
from novel_writer.services.errors import ConflictError, WorkflowError
from novel_writer.services.provider_profiles import ProviderProfile

if TYPE_CHECKING:
    from novel_writer.generation.service import GenerationService


def eligible(batch: GenerationBatchRecord, calls: list[GenerationCallRecord]) -> bool:
    return (
        batch.revision == LONGFORM_REVISION
        and batch.status in {"needs_attention", "awaiting_plan", "paused"}
        and batch.authorized
        and bool(batch.state.get("plan_id"))
        and not batch.state.get("amendment_authorized_sha256")
        and not batch.state.get("memory_output_authorization_id")
        and bool(calls)
        and all(c.status == "completed" for c in calls)
        and (not batch.state.get("candidate_id") or batch.next_action is not None)
    )


async def effective_spec(
    service: GenerationService, batch: GenerationBatchRecord
) -> GenerationSpec:
    spec: GenerationSpec = FrozenGenerationSpec.model_validate(batch.spec)
    receipt = await service.artifact(batch, "input_authorization")
    if receipt:
        data = receipt.payload
        if data.get(
            "batch_preview_sha256"
        ) != batch.preview_sha256 or receipt.sha256 != fingerprint(data):
            raise ConflictError("输入额度授权与原批次不匹配")
        approved = Decimal(data["max_cost_cny"])
        if approved < spec.max_cost_cny or Decimal(data["total_cost_upper_cny"]) > approved:
            raise ConflictError("输入额度授权超过原阶段费用上限")
        spec = spec.model_copy(update={"input_limit": data["input_limit"]})
        if data.get("all_roles"):
            spec = with_limits(
                spec, input_limit=data["input_limit"], output_limit=data["output_limit"]
            )
        spec = spec.model_copy(update={"max_cost_cny": approved})
    return spec


async def preview_input(
    service: GenerationService,
    batch: GenerationBatchRecord,
    input_limit: int | None,
    *,
    output_limit: int = TOKEN_LIMIT,
    all_roles: bool = True,
    max_cost_cny: Decimal | None = None,
) -> dict[str, Any]:
    await service.assert_current(batch)
    calls = list(
        await service.session.scalars(
            select(GenerationCallRecord).where(GenerationCallRecord.batch_id == batch.id)
        )
    )
    if not eligible(batch, calls):
        raise ConflictError(
            "仅有有效方案且已完成调用均成功的暂停阶段可调整剩余额度；失败调用不能重发"
        )
    plan = await service.artifact(batch, "plan")
    assert plan is not None
    note = await service.artifact(batch, "plan_author_note")
    candidate = await service.artifact(batch, "candidate")
    questions = await service.artifact(batch, "questions")
    spec = await effective_spec(service, batch)
    profile = ProviderProfile.model_validate(batch.snapshot["profile"])
    action = batch.next_action or "write:1"
    slots = slots_for(spec)
    used = {c.action for c in calls}
    remaining_slots = [a for a in slots if a not in used]
    if action not in remaining_slots:
        raise ConflictError("下一步已领取或不属于原阶段剩余槽位")
    proposed = input_limit if input_limit is not None else INPUT_TOKEN_LIMIT
    if not 8000 <= proposed <= INPUT_TOKEN_LIMIT or not 4000 <= output_limit <= TOKEN_LIMIT:
        raise WorkflowError("输入或输出额度超出允许范围")
    approved_budget = max_cost_cny if max_cost_cny is not None else spec.max_cost_cny
    if not approved_budget.is_finite() or not spec.max_cost_cny <= approved_budget <= 10000:
        raise WorkflowError("阶段总预算须不低于原授权预算且不超过10000元")
    proposed_spec = (
        with_limits(spec, input_limit=proposed, output_limit=output_limit)
        if all_roles
        else spec.model_copy(update={"input_limit": proposed})
    )
    blockers: list[str] = []
    from novel_writer.generation.longform import prepared_reports

    reports = await prepared_reports(service, batch, action)
    from novel_writer.generation import chief_context
    from novel_writer.generation.knowledge_binding import bind_preparation_receipt, bind_role_state

    if chief_context.uses_roles(proposed_spec):
        await bind_role_state(service, batch, reports, action)
    await bind_preparation_receipt(service, batch, action, reports)
    scope: dict[str, Any] = {}
    if action == "editor":
        from novel_writer.generation.stage import edit_scope

        scope = await edit_scope(service, batch)
    reports["edit_scope"] = scope
    required: int | None = None
    try:
        required = prepare_request(
            proposed_spec,
            batch.snapshot,
            action,
            plan.payload,
            reports.get("input_body", candidate.payload["body"] if candidate else None),
            note.payload["note"] if note else None,
            reports,
            scope,
        )[1]
    except WorkflowError as error:
        blockers.append(str(error))
    for remaining_action in remaining_slots:
        try:
            request_for(proposed_spec, profile, remaining_action, "容量预检", "不发送")
            model, output, _ = model_for(proposed_spec, remaining_action)
            if output >= (option_for(profile, model).context_window or 0):
                raise WorkflowError("输出额度超过模型上下文容量")
        except WorkflowError as error:
            blockers.append(f"{remaining_action}：{error}")
    if questions and any(
        q["status"] == "pending" and q["scope"] == "current_unit"
        for q in questions.payload["items"]
    ):
        blockers.append("请先在故事方案中答复或延期当前问题并保存，再重新预览")
    spent = sum((c.actual_cost_cny or Decimal(0) for c in calls), Decimal(0))
    if any(c.actual_cost_cny is None for c in calls):
        blockers.append("已完成调用的实际费用尚未确定，不能重新核算剩余额度")
    remaining = sum(
        (
            cost_for(
                option_for(profile, model_for(proposed_spec, action)[0]),
                proposed,
                model_for(proposed_spec, action)[1],
            )
            for action in remaining_slots
        ),
        Decimal(0),
    )
    total = (spent or Decimal(0)) + remaining
    if total > approved_budget:
        blockers.append("已用费用与剩余步骤的保守费用上界超过原阶段预算或待确认预算；请重新核算")
    result: dict[str, Any] = {
        "batch_preview_sha256": batch.preview_sha256,
        "plan_sha256": plan.sha256,
        "plan_id": str(plan.id),
        "author_note_sha256": note.sha256 if note else None,
        "questions_sha256": questions.sha256 if questions else None,
        "completed_call_sha256": fingerprint(
            {
                "id": str(calls[0].id),
                "request": calls[0].request_sha256,
                "response": (calls[0].response or {}).get("sha256"),
            }
        ),
        "previous_authorization_id": batch.state.get("input_authorization_id"),
        "input_preparation_failure_id": batch.state.get("input_preparation_failure_id"),
        "candidate_sha256": candidate.sha256 if candidate else None,
        "unit_chain_sha256": reports["unit_chain_sha256"],
        "calls_sha256": fingerprint(
            [
                {
                    "id": str(c.id),
                    "request": c.request_sha256,
                    "response": (c.response or {}).get("sha256"),
                    "cost": str(c.actual_cost_cny),
                }
                for c in sorted(calls, key=lambda c: c.slot)
            ]
        ),
        "action": action,
        "all_roles": all_roles,
        "output_limit": output_limit if all_roles else None,
        "previous_input_limit": spec.input_limit,
        "input_limit": proposed,
        "writer_input_tokens": required,
        "spent_cost_cny": str(spent) if spent is not None else None,
        "remaining_cost_upper_cny": str(remaining),
        "total_cost_upper_cny": str(total),
        "previous_max_cost_cny": str(spec.max_cost_cny),
        "max_cost_cny": str(approved_budget),
        "remaining_slots": remaining_slots,
        "blockers": blockers,
    }
    return {**result, "preview_sha256": fingerprint(result)}


async def authorize_input(
    service: GenerationService,
    batch: GenerationBatchRecord,
    input_limit: int,
    sha: str,
    key: str,
    *,
    output_limit: int = TOKEN_LIMIT,
    all_roles: bool = True,
    max_cost_cny: Decimal | None = None,
) -> dict[str, Any]:
    preview = await preview_input(
        service,
        batch,
        input_limit,
        output_limit=output_limit,
        all_roles=all_roles,
        max_cost_cny=max_cost_cny,
    )
    if preview["preview_sha256"] != sha or preview["blockers"]:
        raise ConflictError("输入预览已变化或仍有阻塞；请重新核对额度与费用")
    await service.append(batch, "input_authorization", preview)
    batch.status = "awaiting_plan"
    batch.next_action = preview["action"]
    await service.authorize(batch.project_id, batch.id, batch.preview_sha256, key + ":start")
    return await service.detail(batch)
