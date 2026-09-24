"""One explicitly authorized Memory replacement; preserve every original call and unit."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from novel_writer.db.models import GenerationBatchRecord, GenerationCallRecord
from novel_writer.generation.budget import cost_for, option_for
from novel_writer.generation.content import fingerprint
from novel_writer.generation.diagnostics import output_diagnostic
from novel_writer.generation.input_recovery import effective_spec
from novel_writer.generation.longform import prepared_reports, units_for
from novel_writer.generation.novel import model_for
from novel_writer.generation.request_preparation import prepare_request
from novel_writer.generation.schemas import LONGFORM_REVISION, GenerationSpec, RoleModel
from novel_writer.generation.token_limits import TOKEN_LIMIT, with_limits
from novel_writer.services.errors import ConflictError, WorkflowError
from novel_writer.services.provider_profiles import ProviderProfile

if TYPE_CHECKING:
    from novel_writer.generation.service import GenerationService

KIND = "memory_output_authorization"
AMENDMENT_ACTIONS = {"amend", "rewrite", "memory_amend", "checker_amend", "reader_amend"}


def scoped_calls(
    batch: GenerationBatchRecord, calls: list[GenerationCallRecord]
) -> list[GenerationCallRecord]:
    if batch.state.get("amendment_authorized_sha256"):
        return [c for c in calls if c.action in AMENDMENT_ACTIONS]
    return calls


def eligible(batch: GenerationBatchRecord, calls: list[GenerationCallRecord]) -> bool:
    failures = [c for c in scoped_calls(batch, calls) if c.status != "completed"]
    return bool(
        batch.revision == LONGFORM_REVISION
        and batch.status in {"needs_attention", "paused"}
        and batch.authorized
        and not batch.state.get(f"{KIND}_id")
        and not any(c.status in {"executing", "outcome_uncertain"} for c in calls)
        and len(failures) == 1
        and failures[0].status == "local_failure"
        and (
            failures[0].action == "memory_amend"
            if batch.state.get("amendment_authorized_sha256")
            else failures[0].action.startswith("memory:")
        )
        and output_diagnostic(failures[0])
        and 0
        < failures[0].request.get("model_request", {}).get("max_output_tokens", 0)
        < TOKEN_LIMIT
        and failures[0] == max(calls, key=lambda c: c.started_at)
    )


def with_output(
    spec: GenerationSpec, action: str, limit: int, *, all_roles: bool = False
) -> GenerationSpec:
    if all_roles:
        return with_limits(spec, output_limit=limit)
    model, _, tokenizer = model_for(spec, action)
    return spec.model_copy(
        update={
            "roles": {
                **spec.roles,
                "memory": RoleModel(model=model, tokenizer_id=tokenizer, output_limit=limit),
            }
        }
    )


async def bindings(service: GenerationService, batch: GenerationBatchRecord) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for kind in ("plan", "candidate", "units", "plan_author_note", "questions"):
        artifact = await service.artifact(batch, kind)
        result[kind] = {"id": str(artifact.id), "sha256": artifact.sha256} if artifact else None
    result["input_authorization_id"] = batch.state.get("input_authorization_id")
    if batch.state.get("amendment_authorized_sha256"):
        amendment = await service.artifact(batch, "amendment")
        result["amendment"] = (
            {"id": str(amendment.id), "sha256": amendment.sha256} if amendment else None
        )
        result["amendment_authorized_sha256"] = batch.state["amendment_authorized_sha256"]
    return result


async def preview_memory(
    service: GenerationService,
    batch: GenerationBatchRecord,
    output_limit: int | None,
    *,
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
        raise ConflictError("仅当前 Memory 明确输出耗尽可补全一次；未知结果或重复补全不可用")
    active_calls = scoped_calls(batch, calls)
    failed = next(c for c in active_calls if c.status == "local_failure")
    amending = bool(batch.state.get("amendment_authorized_sha256"))
    raw = failed.response or {}
    if raw.get("sha256") != fingerprint({k: v for k, v in raw.items() if k != "sha256"}):
        raise ConflictError("原 Memory 响应校验不一致，不能补全")
    candidate = await service.artifact(batch, "candidate")
    plan = await service.artifact(batch, "plan")
    note = await service.artifact(batch, "plan_author_note")
    units = await units_for(service, batch)
    if (
        not candidate
        or candidate.payload.get("complete") is not True
        or not plan
        or not units
        or (
            not amending
            and (
                units[-1].get("memory_id")
                or any(not u.get("memory_id") for u in units[:-1])
                or failed.action != f"memory:{len(units)}"
            )
        )
        or failed.request.get("candidate_sha256") != candidate.sha256
        or failed.request.get("plan_sha256") != plan.sha256
        or failed.request.get("unit_chain_sha256") != fingerprint(units)
        or failed.request.get("author_note_sha256") != (note.sha256 if note else None)
        or failed.request.get("batch_preview_sha256") != batch.preview_sha256
    ):
        raise ConflictError("已写正文、方案或单元来源已改变，不能沿旧 Memory 调用补全")
    spec = await effective_spec(service, batch)
    from novel_writer.generation.amendments import authorized_spec as amendment_spec
    from novel_writer.generation.stage import active_slots

    spec = await amendment_spec(service, batch, spec)
    slots = await active_slots(service, batch)
    if amending and failed.request.get("action_slots") != slots:
        raise ConflictError("失败 Memory 与当前修订授权的动作范围不匹配")
    profile = ProviderProfile.model_validate(batch.snapshot["profile"])
    model, old_limit, _ = model_for(spec, failed.action)
    maximum = min(TOKEN_LIMIT, option_for(profile, model).max_output_tokens or 0)
    proposed = output_limit if output_limit is not None else TOKEN_LIMIT
    if not old_limit < proposed <= maximum:
        raise WorkflowError(f"Memory 新输出额度须高于 {old_limit} 且不超过模型允许的 {maximum}")
    if all_roles and proposed < 4000:
        raise WorkflowError("统一角色输出上限不能低于 Writer 的 4000 tokens")
    approved_budget = max_cost_cny if max_cost_cny is not None else spec.max_cost_cny
    if not approved_budget.is_finite() or not spec.max_cost_cny <= approved_budget <= 10000:
        raise WorkflowError("本次总预算须不低于原授权预算且不超过10000元")
    proposed_spec = with_output(spec, failed.action, proposed, all_roles=all_roles)
    reports = await prepared_reports(service, batch, failed.action)
    request, count, _, _ = prepare_request(
        proposed_spec,
        batch.snapshot,
        failed.action,
        plan.payload,
        reports.get("input_body", candidate.payload["body"]),
        note.payload["note"] if note else None,
        reports,
        {},
    )
    blockers = []
    if any(c.actual_cost_cny is None for c in calls):
        blockers.append("存在费用未知的调用，不能核算剩余额度")
    questions = await service.artifact(batch, "questions")
    if questions and any(
        q["status"] == "pending" and q["scope"] == "current_unit"
        for q in questions.payload["items"]
    ):
        blockers.append("仍有当前作者问题，需先处理")
    spent = sum((c.actual_cost_cny or Decimal(0) for c in active_calls), Decimal(0))
    used = {c.action for c in active_calls}
    remaining = [failed.action, *(a for a in slots if a not in used)]
    for action in remaining:
        model, output, _ = model_for(proposed_spec, action)
        option = option_for(profile, model)
        if output > (option.max_output_tokens or 0) or output >= (option.context_window or 0):
            blockers.append(f"{action} 的输出额度超过模型容量，不能发送")
    upper = sum(
        (
            cost_for(
                option_for(profile, model_for(proposed_spec, a)[0]),
                proposed_spec.input_limit,
                model_for(proposed_spec, a)[1],
            )
            for a in remaining
        ),
        Decimal(0),
    )
    total = spent + upper
    if total > approved_budget:
        blockers.append(
            "已用费用与补全、剩余步骤的费用上界超过原授权预算或待确认预算；请调整预算后重新核算"
        )
    result = {
        "batch_preview_sha256": batch.preview_sha256,
        "failed_call_id": str(failed.id),
        "action": failed.action,
        "scope": "amendment" if amending else "stage",
        "replacement_slot": (300 if amending else 200) + slots.index(failed.action),
        "source_bindings": await bindings(service, batch),
        "calls_sha256": fingerprint(
            [
                {
                    "id": str(c.id),
                    "status": c.status,
                    "request": c.request_sha256,
                    "response": (c.response or {}).get("sha256"),
                    "cost": str(c.actual_cost_cny),
                }
                for c in sorted(calls, key=lambda c: c.slot)
            ]
        ),
        "request_sha256": fingerprint(request.model_dump(mode="json")),
        "input_tokens": count,
        "previous_input_limit": spec.input_limit,
        "input_limit": proposed_spec.input_limit,
        "all_roles": all_roles,
        "previous_output_limit": old_limit,
        "output_limit": proposed,
        "remaining_slots": remaining,
        "additional_calls": 1,
        "spent_cost_cny": str(spent),
        "remaining_cost_upper_cny": str(upper),
        "total_cost_upper_cny": str(total),
        "previous_max_cost_cny": str(spec.max_cost_cny),
        "max_cost_cny": str(approved_budget),
        "blockers": blockers,
    }
    return {**result, "preview_sha256": fingerprint(result)}


async def authorize_memory(
    service: GenerationService,
    batch: GenerationBatchRecord,
    output_limit: int,
    sha: str,
    key: str,
    *,
    all_roles: bool = True,
    max_cost_cny: Decimal | None = None,
) -> dict[str, Any]:
    preview = await preview_memory(
        service, batch, output_limit, all_roles=all_roles, max_cost_cny=max_cost_cny
    )
    if preview["preview_sha256"] != sha or preview["blockers"]:
        raise ConflictError("Memory 补全预览已变化或仍有阻塞，请重新核算")
    await service.append(batch, KIND, preview)
    batch.status, batch.next_action = "awaiting_plan", preview["action"]
    await service.authorize(batch.project_id, batch.id, batch.preview_sha256, key + ":start")
    return await service.detail(batch)


async def authorized_spec(
    service: GenerationService, batch: GenerationBatchRecord, spec: GenerationSpec
) -> GenerationSpec:
    receipt = await service.artifact(batch, KIND)
    if not receipt:
        return spec
    amending = bool(batch.state.get("amendment_authorized_sha256"))
    if (receipt.payload.get("scope", "stage") == "amendment") != amending:
        return spec
    data = receipt.payload
    approved_budget = Decimal(data["max_cost_cny"])
    if (
        data["batch_preview_sha256"] != batch.preview_sha256
        or data.get("previous_input_limit", data["input_limit"]) != spec.input_limit
        or Decimal(data.get("previous_max_cost_cny", data["max_cost_cny"])) != spec.max_cost_cny
        or not spec.max_cost_cny <= approved_budget <= 10000
        or Decimal(data["total_cost_upper_cny"]) > approved_budget
    ):
        raise ConflictError("Memory 补全授权与原批次、输入或预算不匹配")
    if amending and data["source_bindings"].get("amendment_authorized_sha256") != batch.state.get(
        "amendment_authorized_sha256"
    ):
        raise ConflictError("Memory 补全授权与当前独立修订不匹配")
    return with_output(
        spec, data["action"], data["output_limit"], all_roles=data.get("all_roles", False)
    ).model_copy(update={"max_cost_cny": approved_budget, "input_limit": data["input_limit"]})


async def recovery_slot(
    service: GenerationService, batch: GenerationBatchRecord, action: str, original: int
) -> int:
    receipt = await service.artifact(batch, KIND)
    if not receipt or receipt.payload["action"] != action:
        return original
    if receipt.payload["source_bindings"] != await bindings(service, batch):
        raise ConflictError("Memory 补全来源已改变，请勿沿旧授权派发")
    return int(receipt.payload["replacement_slot"])


async def resolved_failures(
    service: GenerationService, batch: GenerationBatchRecord, calls: list[GenerationCallRecord]
) -> set[str]:
    from novel_writer.generation.step_recovery import resolved_failures as step_resolved

    resolved = await step_resolved(service, batch, calls)
    receipt = await service.artifact(batch, KIND)
    if receipt and any(
        (c.status == "completed" or str(c.id) in resolved)
        and c.slot == receipt.payload["replacement_slot"]
        and c.action == receipt.payload["action"]
        and c.request.get("memory_output_authorization_id") == str(receipt.id)
        for c in calls
    ):
        resolved.add(receipt.payload["failed_call_id"])
    return resolved
