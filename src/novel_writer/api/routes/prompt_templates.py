"""Local template editing and read-only prompt rendering. Never dispatches a model."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from novel_writer.api.dependencies import Session
from novel_writer.db.models import GenerationCallRecord
from novel_writer.generation import event_units, template_catalog
from novel_writer.generation.budget import (
    input_tokens,
    request_for,
    request_preview,
    validate_capacity,
)
from novel_writer.generation.content import parse_object
from novel_writer.generation.prompt_templates import apply_template, supported, variant_for
from novel_writer.generation.schemas import FrozenGenerationSpec
from novel_writer.generation.service import GenerationService
from novel_writer.generation.template_catalog import TemplateText, Variant
from novel_writer.providers.base import ModelRequest
from novel_writer.services.errors import WorkflowError
from novel_writer.services.prompt_template_store import PromptTemplateStore
from novel_writer.services.provider_profiles import ProviderProfile

router = APIRouter(prefix="/api/prompt-templates", tags=["prompt-templates"])


class SaveTemplate(BaseModel):
    expected_revision: str = Field(min_length=1, max_length=80)
    template: TemplateText | None
    note: str = Field(default="手动修改", max_length=300)


class PreviewTemplate(BaseModel):
    project_id: UUID
    batch_id: UUID
    variant: Variant
    template: TemplateText | None = None


class PromptPreview(BaseModel):
    system_prompt: str
    task_prompt: str
    input_count: int
    counting_method: str
    blockers: list[str]
    omitted_optional: list[str]
    engine_contract: dict[str, Any]
    source_action: str
    source_call_id: str | None
    source_description: str


def store(request: Request) -> PromptTemplateStore:
    return PromptTemplateStore(request.app.state.provider_profile_store.path)


def catalog(current: PromptTemplateStore) -> dict[str, Any]:
    bundle = current.current()
    return {
        "revision": bundle["revision"],
        "engine_system": template_catalog.ENGINE_SYSTEM,
        "entries": [
            {
                "variant": variant,
                "label": template_catalog.LABELS[variant],
                "text": current.text(variant, bundle).model_dump(),
                "default_text": template_catalog.default_text(variant).model_dump(),
                "customized": variant in bundle["templates"],
                "fields": [
                    {"key": k, "label": label, "required": required}
                    for k, label, required in template_catalog.FIELDS[variant]
                ],
                "output_requirement": template_catalog.OUTPUTS[variant],
            }
            for variant in template_catalog.FIELDS
        ],
        "history": current.history(),
    }


@router.get("")
def get_templates(request: Request) -> dict[str, Any]:
    return catalog(store(request))


@router.put("/{variant}")
def save_template(variant: Variant, payload: SaveTemplate, request: Request) -> dict[str, Any]:
    current = store(request)
    current.save(variant, payload.template, payload.expected_revision, payload.note)
    return catalog(current)


@router.get("/versions/{revision}/{variant}")
def get_version(revision: UUID, variant: Variant, request: Request) -> dict[str, Any]:
    current = store(request)
    bundle = current.version(str(revision))
    return {
        "text": current.text(variant, bundle).model_dump(),
        "customized": variant in bundle["templates"],
        "revision": bundle["revision"],
    }


@router.post("/preview", response_model=PromptPreview)
async def preview_template(
    payload: PreviewTemplate,
    request: Request,
    session: Session,
) -> PromptPreview:
    service = GenerationService(session, request.app.state.provider_profile_store)
    batch = await service.batch(payload.project_id, payload.batch_id)
    await service.assert_current(batch, dispatch=False)
    spec = FrozenGenerationSpec.model_validate(batch.spec)
    if not supported(spec):
        raise WorkflowError("所选批次使用历史 Prompt 结构，请选择当前策略建立的预览或批次")
    calls = list(
        await session.scalars(
            select(GenerationCallRecord)
            .where(
                GenerationCallRecord.batch_id == batch.id,
            )
            .order_by(GenerationCallRecord.slot.desc())
        )
    )
    call = next((c for c in calls if variant_for(c.action) == payload.variant), None)
    profile = ProviderProfile.model_validate(batch.snapshot["profile"])
    if call is not None:
        model_request = ModelRequest.model_validate(call.request["model_request"])
        source = call.request.get("prompt_template_source")
        if source is None:
            source = parse_object(model_request.user_prompt)
        counting = call.request["counting"]
        spec = spec.model_copy(
            update={
                "input_limit": call.request.get("effective_input_limit", spec.input_limit),
            }
        )
        action = call.action
        description = "使用该批次最近一次对应角色调用的原始资料；不重新检索或修改原调用。"
    elif payload.variant == "chief":
        system, raw = await asyncio.to_thread(event_units.render_for, spec, batch.snapshot, "plan")
        source = parse_object(raw)
        model_request = request_for(spec, profile, "plan", system, raw)
        counting = batch.snapshot["counting"]["chief"]
        action = "plan"
        description = "使用该预览冻结的 Chief 资料；没有发起模型调用。"
    else:
        raise WorkflowError("该批次还没有此角色的调用资料，请选择有对应记录的批次进行真实资料预览")

    def render() -> PromptPreview:
        template = payload.template
        if template is not None:
            system, task, info = apply_template(payload.variant, template, source)
        else:
            system = template_catalog.SYSTEMS[payload.variant]
            if payload.variant == "title":
                system = (
                    '你只为每章提供标题，返回JSON {"chapters":[{"id":"给定ID",'
                    '"titles":["标题"]}]}。不修改正文。'
                    if "chapters" in source
                    else '你负责为给定正文命名，只返回JSON {"titles":["标题"]}，'
                    '一至三项；不改正文。'
                )
            task = json.dumps(source, ensure_ascii=False, separators=(",", ":"))
            info = {
                "omitted_optional": [],
                "engine_contract": {
                    k: source[k]
                    for k in ("output_schema", "output_contract", "writable_fields")
                    if k in source
                },
            }
        proposed = model_request.model_copy(update={"system_prompt": system, "user_prompt": task})
        text = request_preview(proposed)
        count = input_tokens(text, counting)
        blockers = []
        try:
            validate_capacity(text, proposed, spec, profile, counting)
        except WorkflowError as error:
            blockers.append(str(error))
        return PromptPreview(
            system_prompt=system,
            task_prompt=task,
            input_count=count,
            counting_method=counting["method"],
            blockers=blockers,
            omitted_optional=info["omitted_optional"],
            engine_contract=info["engine_contract"],
            source_action=action,
            source_call_id=str(call.id) if call else None,
            source_description=description,
        )

    return await asyncio.to_thread(render)
