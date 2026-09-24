from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select

from novel_writer.db.models import GenerationBatchRecord, GenerationCallRecord
from novel_writer.generation.budget import cost_for, option_for, request_for
from novel_writer.generation.content import fingerprint
from novel_writer.generation.input_recovery import effective_spec
from novel_writer.generation.logic import advisory
from novel_writer.generation.novel import model_for
from novel_writer.generation.reports import evidence
from novel_writer.generation.schemas import (
    LONGFORM_REVISION,
    NOVEL_REVISION,
    AmendmentRequest,
    GenerationSpec,
)
from novel_writer.generation.service import GenerationService
from novel_writer.generation.token_limits import with_limits as uniform_limits
from novel_writer.generation.world_context import amendment_contract as contract_for
from novel_writer.services.errors import ConflictError, WorkflowError
from novel_writer.services.provider_profiles import ProviderProfile


def with_limits(spec: GenerationSpec, request: dict[str, Any]) -> GenerationSpec:
    # Older saved authorizations retain exactly the limits they approved.
    if "output_limit" in request:
        spec = uniform_limits(
            spec, input_limit=request["input_limit"], output_limit=request["output_limit"]
        )
    if "feedback_policy" in request:
        spec = spec.model_copy(
            update={k: request[k] for k in ("feedback_policy", "enable_checker", "enable_reader")}
        )
    if "context_policy" in request:
        spec = spec.model_copy(update={"context_policy": request["context_policy"]})
    if "writing_policy" in request:
        spec = spec.model_copy(update={"writing_policy": request["writing_policy"]})
    if "narrative_policy" in request:
        spec = spec.model_copy(update={"narrative_policy": request["narrative_policy"]})
    if "length_policy" in request:
        spec = spec.model_copy(update={"length_policy": request["length_policy"]})
    return spec.model_copy(update={"max_cost_cny": Decimal(request["max_cost_cny"])})


async def authorized_spec(
    service: GenerationService, batch: GenerationBatchRecord, spec: GenerationSpec
) -> GenerationSpec:
    sha = batch.state.get("amendment_authorized_sha256")
    if not sha:
        return spec
    amendment = await service.artifact(batch, "amendment")
    if (
        not amendment
        or amendment.sha256 != sha
        or amendment.sha256 != fingerprint(amendment.payload)
        or amendment.payload["base_preview_sha256"] != batch.preview_sha256
    ):
        raise ConflictError("修订授权与原批次或预览来源失配")
    spec = with_limits(spec, amendment.payload["request"])
    if amendment.payload.get("prompt_contract_sha256", contract_for(spec)) != contract_for(spec):
        raise ConflictError("修订提示词已变化，请重新预览；原授权不升级")
    return spec


async def preview_amendment(
    service: GenerationService,
    batch: GenerationBatchRecord,
    request: AmendmentRequest,
) -> dict[str, Any]:
    await service.assert_current(batch)
    if batch.revision not in {NOVEL_REVISION, LONGFORM_REVISION} or batch.status not in {
        "ready",
        "needs_attention",
        "paused",
    }:
        raise ConflictError("仅暂停或审核中的新版阶段可提出修订")
    if batch.state.get("amendment_authorized_sha256"):
        raise ConflictError("本阶段已经使用过一次修订授权；不循环润色或自动重试")
    candidate = await service.artifact(batch, "candidate")
    if not candidate or candidate.sha256 != request.candidate_sha256:
        raise ConflictError("修订来源候选已改变")
    if candidate.payload.get("complete") is False:
        raise WorkflowError("截断稿须先由作者补齐；修订不能冒充供应商续写")
    if request.mode == "local":
        evidence(request.paragraph_ids, candidate.payload["body"])
        if set(request.paragraph_ids) & set(request.protected_paragraph_ids):
            raise WorkflowError("修改范围与保护范围冲突")
    if request.protected_paragraph_ids:
        evidence(request.protected_paragraph_ids, candidate.payload["body"])
    if request.mode == "rewrite" and request.protected_paragraph_ids:
        raise WorkflowError("全文结构改写不能承诺保护区原文不变，请用局部修订")
    slots = {"verify": [], "local": ["amend"], "rewrite": ["rewrite"]}[request.mode]
    spec = with_limits(await effective_spec(service, batch), request.model_dump(mode="json"))
    slots += ["memory_amend"]
    if not advisory(spec) or spec.enable_checker:
        slots += ["checker_amend"]
    if not advisory(spec) or spec.enable_reader:
        slots += ["reader_amend"]
    profile = ProviderProfile.model_validate(batch.snapshot["profile"])
    for action in slots:
        request_for(spec, profile, action, "本地容量预检", "仅核对配置，不发送")
        model, output, _ = model_for(spec, action)
        if output >= (option_for(profile, model).context_window or 0):
            raise WorkflowError("修订输出额度超过模型上下文容量")
    cost = sum(
        (
            cost_for(
                option_for(profile, model_for(spec, a)[0]), spec.input_limit, model_for(spec, a)[1]
            )
            for a in slots
        ),
        Decimal(0),
    )
    if cost > request.max_cost_cny:
        raise WorkflowError("修订和证据重建的费用上界超过本次预算")
    payload = {
        "request": request.model_dump(mode="json"),
        "slots": slots,
        "maximum_cost_cny": str(cost),
        "base_preview_sha256": batch.preview_sha256,
        "input_limit": spec.input_limit,
        "output_limit": request.output_limit,
        "prompt_contract_sha256": contract_for(spec),
        "feedback_policy": spec.feedback_policy,
        "writing_policy": spec.writing_policy,
        "enable_checker": spec.enable_checker,
        "enable_reader": spec.enable_reader,
    }
    artifact = await service.append(batch, "amendment", payload)
    return {**payload, "preview_sha256": artifact.sha256}


async def authorize_amendment(
    service: GenerationService,
    batch: GenerationBatchRecord,
    sha: str,
) -> dict[str, Any]:
    await service.assert_current(batch)
    amendment = await service.artifact(batch, "amendment")
    candidate = await service.artifact(batch, "candidate")
    if batch.state.get("amendment_authorized_sha256"):
        raise ConflictError("该修订授权已使用")
    if batch.status not in {"ready", "needs_attention", "paused"} or not amendment or not candidate:
        raise ConflictError("当前阶段不能开始修订")
    if (
        amendment.sha256 != sha
        or amendment.payload["request"]["candidate_sha256"] != candidate.sha256
    ):
        raise ConflictError("修订预览与当前候选失配")
    active = await service.session.scalar(
        select(GenerationCallRecord.id)
        .where(
            GenerationCallRecord.project_id == batch.project_id,
            GenerationCallRecord.status.in_(("executing", "outcome_uncertain")),
        )
        .limit(1)
    )
    queued = await service.session.scalar(
        select(GenerationBatchRecord.id)
        .where(
            GenerationBatchRecord.project_id == batch.project_id,
            GenerationBatchRecord.id != batch.id,
            GenerationBatchRecord.status.in_(("queued", "running")),
        )
        .limit(1)
    )
    if active or queued:
        raise ConflictError("本作品仍有执行中、排队或结果未知的动作")
    batch.state = {
        **batch.state,
        "amendment_authorized_sha256": sha,
        "amendment_authorization": fingerprint(amendment.payload),
    }
    batch.authorized = True
    batch.pause_requested = False
    batch.status = "queued"
    batch.next_action = amendment.payload["slots"][0]
    return {"id": str(batch.id), "status": batch.status}
