"""Author-confirmed replay of one failed step, retaining its original evidence."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select

from novel_writer.db.models import (
    GenerationArtifactRecord,
    GenerationBatchRecord,
    GenerationCallRecord,
)
from novel_writer.generation.budget import (
    cost_for,
    option_for,
    request_preview,
    validate_capacity,
)
from novel_writer.generation.content import fingerprint, parse_object
from novel_writer.generation.novel import model_for
from novel_writer.generation.schemas import LONGFORM_REVISION, GenerationSpec
from novel_writer.providers.base import ModelRequest
from novel_writer.services.errors import ConflictError
from novel_writer.services.provider_profiles import ProviderProfile

if TYPE_CHECKING:
    from novel_writer.generation.service import GenerationService

KIND = "step_recovery_authorization"
TRANSIENT = {
    "message",
    "transport",
    "compiled",
    "compilation_id",
    "uncertain_resolution_id",
    f"{KIND}_id",
}
FAILURES = {"local_failure", "outcome_uncertain", "uncertain_closed", "failed", "not_dispatched"}


def checkpoint(state: dict[str, Any]) -> dict[str, Any]:
    return deepcopy({k: v for k, v in state.items() if k not in TRANSIENT})


def eligible(batch: GenerationBatchRecord, calls: list[GenerationCallRecord]) -> bool:
    return bool(
        batch.revision == LONGFORM_REVISION
        and batch.status in {"needs_attention", "failed", "outcome_uncertain", "paused"}
        and calls
        and not any(c.status in {"executing", "response_saved"} for c in calls)
        and max(calls, key=lambda c: (c.started_at, c.slot)).status in FAILURES
    )


async def call_list(
    service: GenerationService, batch: GenerationBatchRecord
) -> list[GenerationCallRecord]:
    return list(
        await service.session.scalars(
            select(GenerationCallRecord)
            .where(GenerationCallRecord.batch_id == batch.id)
            .order_by(GenerationCallRecord.started_at, GenerationCallRecord.slot)
        )
    )


async def checked_artifact(
    service: GenerationService, batch: GenerationBatchRecord, pointer: str, kind: str
) -> GenerationArtifactRecord:
    item = await service.session.get(GenerationArtifactRecord, UUID(pointer))
    if (
        not item
        or item.batch_id != batch.id
        or item.kind != kind
        or item.sha256 != fingerprint(item.payload)
    ):
        raise ConflictError("恢复检查点的工件来源或校验已改变")
    return item


async def restore_point(
    service: GenerationService, batch: GenerationBatchRecord, failed: GenerationCallRecord
) -> dict[str, Any]:
    saved = failed.request.get("resume_checkpoint")
    current = checkpoint(batch.state)
    state = deepcopy(saved) if isinstance(saved, dict) else current
    if saved is not None:
        if fingerprint(saved) != failed.request.get("resume_checkpoint_sha256"):
            raise ConflictError("恢复检查点校验不一致")
        compilation = await service.artifact(batch, "compilation")
        expected = (
            compilation.payload.get("resume_state", saved)
            if compilation and compilation.payload["call_id"] == str(failed.id)
            else saved
        )
        if current != expected:
            raise ConflictError("失败后资料或授权已修改，不能覆盖作者的新版本")
    for key, pointer in state.items():
        if key.endswith("_id") and isinstance(pointer, str):
            await checked_artifact(service, batch, pointer, key[:-3])
    for kind, request_key in (
        ("plan", "plan_sha256"),
        ("candidate", "candidate_sha256"),
        ("plan_author_note", "author_note_sha256"),
    ):
        pointer = state.get(f"{kind}_id")
        item = await checked_artifact(service, batch, pointer, kind) if pointer else None
        if (item.sha256 if item else None) != failed.request.get(request_key):
            raise ConflictError("失败步骤的方案或正文来源已改变，不能从原位置重发")
    pointer = state.get("units_id")
    units = await checked_artifact(service, batch, pointer, "units") if pointer else None
    if fingerprint(units.payload["items"] if units else []) != failed.request.get(
        "unit_chain_sha256"
    ):
        raise ConflictError("失败步骤的单元链已改变，不能从原位置重发")
    for key in (
        "input_authorization_id",
        "memory_output_authorization_id",
        "amendment_authorized_sha256",
    ):
        if state.get(key) != batch.state.get(key) or state.get(key) != failed.request.get(key):
            raise ConflictError("失败后授权范围已改变，请重新核对")
    return state


def verify_call(batch: GenerationBatchRecord, call: GenerationCallRecord) -> ModelRequest:
    data = call.request
    if data.get("batch_preview_sha256") != batch.preview_sha256:
        raise ConflictError("原调用不属于当前冻结预览")
    bound = (
        {"wire_body": data["wire_body"], "batch": batch.preview_sha256}
        if "wire_body" in data
        else data["model_request"]
    )
    if fingerprint(bound) != call.request_sha256:
        raise ConflictError("原调用请求校验不一致")
    raw = call.response
    if raw and raw.get("sha256") != fingerprint({k: v for k, v in raw.items() if k != "sha256"}):
        raise ConflictError("原调用响应校验不一致")
    return ModelRequest.model_validate(data["model_request"])


async def authorized_spec(
    service: GenerationService, batch: GenerationBatchRecord, spec: GenerationSpec
) -> GenerationSpec:
    receipt = await service.artifact(batch, KIND)
    if not receipt or receipt.payload["amendment_sha256"] != batch.state.get(
        "amendment_authorized_sha256"
    ):
        return spec
    data = receipt.payload
    budget = Decimal(data["max_cost_cny"])
    if (
        data["batch_preview_sha256"] != batch.preview_sha256
        or not spec.max_cost_cny <= budget <= 10000
        or Decimal(data["total_cost_upper_cny"]) > budget
    ):
        raise ConflictError("失败恢复授权的来源或费用上限不一致")
    return spec.model_copy(update={"max_cost_cny": budget})


async def preview_step(
    service: GenerationService, batch: GenerationBatchRecord, max_cost_cny: Decimal | None = None
) -> dict[str, Any]:
    from novel_writer.generation.amendments import authorized_spec as amendment_spec
    from novel_writer.generation.input_recovery import effective_spec
    from novel_writer.generation.memory_recovery import authorized_spec as memory_spec
    from novel_writer.generation.memory_recovery import resolved_failures as all_resolved
    from novel_writer.generation.memory_recovery import scoped_calls
    from novel_writer.generation.stage import active_slots

    await service.assert_current(batch)
    calls = await call_list(service, batch)
    if not eligible(batch, calls):
        raise ConflictError("当前没有可恢复的失败步骤，或调用仍在处理中")
    failed = calls[-1]
    receipt = await service.artifact(batch, KIND)
    if receipt and receipt.payload["failed_call_id"] == str(failed.id):
        raise ConflictError("该失败步骤已有恢复授权，请继续已授权流程")
    resolved = await all_resolved(service, batch, calls)
    resolved |= await ancestors(service, batch, failed, calls)
    if any(
        c != failed and c.status != "completed" and str(c.id) not in resolved
        for c in scoped_calls(batch, calls)
    ):
        raise ConflictError("尚有其他未恢复的失败步骤，不能跳过依赖继续")
    request = verify_call(batch, failed)
    restore = await restore_point(service, batch, failed)
    slots = await active_slots(service, batch)
    if failed.action not in slots or failed.request.get("action_slots") != slots:
        raise ConflictError("失败步骤不属于当前授权范围")
    spec = await effective_spec(service, batch)
    spec = await amendment_spec(service, batch, spec)
    spec = await memory_spec(service, batch, spec)
    spec = await authorized_spec(service, batch, spec)
    profile = ProviderProfile.model_validate(batch.snapshot["profile"])
    budget = spec.max_cost_cny if max_cost_cny is None else max_cost_cny
    if not budget.is_finite() or not spec.max_cost_cny <= budget <= 10000:
        raise ConflictError("恢复总预算须不低于已有预算且不超过10000元")
    count = validate_capacity(
        failed.request.get("wire_body") or request_preview(request),
        request,
        spec,
        profile,
        failed.request["counting"],
    )
    known = sum((c.actual_cost_cny or Decimal(0) for c in calls), Decimal(0))
    unknown = []
    for call in calls:
        if call.actual_cost_cny is None and call.status != "not_dispatched":
            old = verify_call(batch, call)
            reserve = cost_for(
                option_for(profile, old.model),
                call.request.get("effective_input_limit", spec.input_limit),
                old.max_output_tokens,
            )
            unknown.append({"call_id": str(call.id), "reserved_cost_cny": str(reserve)})
    used = {c.action for c in calls}
    remaining = [
        failed.action,
        *(a for a in slots[slots.index(failed.action) + 1 :] if a not in used),
    ]
    retry_upper = cost_for(
        option_for(profile, request.model), spec.input_limit, request.max_output_tokens
    )
    upper = retry_upper + sum(
        (
            cost_for(
                option_for(profile, model_for(spec, a)[0]), spec.input_limit, model_for(spec, a)[1]
            )
            for a in remaining[1:]
        ),
        Decimal(0),
    )
    reserved = sum((Decimal(c["reserved_cost_cny"]) for c in unknown), Decimal(0))
    total = known + reserved + upper
    blockers = []
    if total > budget:
        blockers.append("已记录费用、未知费用预留及恢复后的剩余费用超过总预算，请调整预算再核算")
    questions = await service.artifact(batch, "questions")
    if questions and any(
        q["status"] == "pending" and q["scope"] == "current_unit"
        for q in questions.payload["items"]
    ):
        blockers.append("仍有需要作者答复的当前问题，请先处理")
    raw = failed.response or {}
    try:
        issue = parse_object(raw.get("text", ""))
    except ValueError:
        issue = {}
    if "generation_blocked" in issue:
        blockers.append("Writer 报告了创作边界冲突，需先处理方案问题")
    result = {
        "batch_preview_sha256": batch.preview_sha256,
        "failed_call_id": str(failed.id),
        "action": failed.action,
        "amendment_sha256": batch.state.get("amendment_authorized_sha256"),
        "replacement_slot": max(400, max(c.slot for c in calls) + 1),
        "restore_state": restore,
        "state_sha256": fingerprint(batch.state),
        "calls_sha256": fingerprint(
            [
                {
                    "id": str(c.id),
                    "status": c.status,
                    "request": c.request_sha256,
                    "response": c.response,
                    "cost": str(c.actual_cost_cny),
                }
                for c in calls
            ]
        ),
        "request_sha256": fingerprint(request.model_dump(mode="json")),
        "input_tokens": count,
        "input_limit": spec.input_limit,
        "output_limit": request.max_output_tokens,
        "model": request.model,
        "additional_calls": 1,
        "remaining_slots": remaining,
        "known_cost_cny": str(known),
        "unknown_calls": unknown,
        "unknown_cost_reserve_cny": str(reserved),
        "retry_cost_upper_cny": str(retry_upper),
        "remaining_cost_upper_cny": str(upper),
        "total_cost_upper_cny": str(total),
        "previous_max_cost_cny": str(spec.max_cost_cny),
        "max_cost_cny": str(budget),
        "requires_uncertain_confirmation": bool(unknown)
        or failed.status in {"outcome_uncertain", "uncertain_closed"}
        or raw.get("error_code") == "outcome_uncertain",
        "partial_response_characters": len(raw.get("text") or ""),
        "blockers": blockers,
    }
    return {**result, "preview_sha256": fingerprint(result)}


async def authorize_step(
    service: GenerationService,
    batch: GenerationBatchRecord,
    sha: str,
    key: str,
    max_cost_cny: Decimal,
    uncertain_confirmed: bool,
) -> dict[str, Any]:
    preview = await preview_step(service, batch, max_cost_cny)
    if preview["preview_sha256"] != sha or preview["blockers"]:
        raise ConflictError("恢复预览已变化或仍有阻塞，请重新核算")
    if preview["requires_uncertain_confirmation"] and not uncertain_confirmed:
        raise ConflictError("请确认原调用已停止，并接受原调用可能已计费和重复计费的风险")
    failed = await service.session.get(GenerationCallRecord, UUID(preview["failed_call_id"]))
    assert failed is not None
    previous_status = failed.status
    if failed.status == "outcome_uncertain":
        failed.status = "uncertain_closed"
        await service.session.flush()
    compiled = batch.state.get("compiled", [])
    batch.state = {**preview["restore_state"], "compiled": compiled}
    await service.append(
        batch,
        KIND,
        {
            **preview,
            "uncertain_confirmed": uncertain_confirmed,
            "previous_call_status": previous_status,
        },
    )
    batch.status, batch.next_action = "awaiting_plan", preview["action"]
    await service.authorize(batch.project_id, batch.id, batch.preview_sha256, key + ":start")
    return await service.detail(batch)


async def pending_replay(
    service: GenerationService, batch: GenerationBatchRecord, action: str
) -> GenerationCallRecord | None:
    receipt = await service.artifact(batch, KIND)
    if (
        not receipt
        or receipt.payload["action"] != action
        or receipt.payload["amendment_sha256"] != batch.state.get("amendment_authorized_sha256")
    ):
        return None
    data = receipt.payload
    if await service.session.scalar(
        select(GenerationCallRecord.id).where(
            GenerationCallRecord.batch_id == batch.id,
            GenerationCallRecord.slot == data["replacement_slot"],
        )
    ):
        raise ConflictError("此恢复调用已领取，不能重复发送")
    if checkpoint(batch.state) != data["restore_state"]:
        raise ConflictError("恢复检查点已改变，未发送请求")
    failed = await service.session.get(GenerationCallRecord, UUID(data["failed_call_id"]))
    assert failed is not None
    request = verify_call(batch, failed)
    if fingerprint(request.model_dump(mode="json")) != data["request_sha256"]:
        raise ConflictError("恢复请求与费用预览不一致")
    state = checkpoint(batch.state)
    call = GenerationCallRecord(
        project_id=batch.project_id,
        batch_id=batch.id,
        slot=data["replacement_slot"],
        action=action,
        provider=failed.provider,
        model=failed.model,
        status="executing",
        request={
            **{
                k: deepcopy(v)
                for k, v in failed.request.items()
                if k not in {"wire_body", "wire_input_tokens"}
            },
            "resume_checkpoint": state,
            "resume_checkpoint_sha256": fingerprint(state),
            "step_recovery_authorization_id": str(receipt.id),
            "retry_of_call_id": str(failed.id),
            "replay_wire_sha256": fingerprint(failed.request["wire_body"])
            if "wire_body" in failed.request
            else None,
        },
        request_sha256=fingerprint(request.model_dump(mode="json")),
    )
    service.session.add(call)
    await service.session.flush()
    return call


async def ancestors(
    service: GenerationService,
    batch: GenerationBatchRecord,
    call: GenerationCallRecord,
    calls: list[GenerationCallRecord],
) -> set[str]:
    result: set[str] = set()
    by_id = {str(c.id): c for c in calls}
    child = call
    while child.request.get("retry_of_call_id"):
        receipt = await checked_artifact(
            service, batch, child.request["step_recovery_authorization_id"], KIND
        )
        parent_id = child.request["retry_of_call_id"]
        if (
            receipt.payload["failed_call_id"] != parent_id
            or receipt.payload["replacement_slot"] != child.slot
            or parent_id not in by_id
            or parent_id in result
            or parent_id == str(call.id)
        ):
            raise ConflictError("恢复调用链来源不一致")
        result.add(parent_id)
        child = by_id[parent_id]
    return result


async def resolved_failures(
    service: GenerationService, batch: GenerationBatchRecord, calls: list[GenerationCallRecord]
) -> set[str]:
    result: set[str] = set()
    for call in calls:
        if call.status == "completed":
            result |= await ancestors(service, batch, call, calls)
    return result
