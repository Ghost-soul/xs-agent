"""Local template editing and read-only prompt rendering. Never dispatches a model."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from novel_writer.api.dependencies import Session
from novel_writer.db.models import GenerationCallRecord
from novel_writer.generation import craft_templates as template_catalog
from novel_writer.generation import creative_contract as craft_contract
from novel_writer.generation import (
    editable_contract,
    editable_rules,
    format_trial,
    progression_contract,
    progression_rules,
    reliability_contract,
    trial_prompts,
    unit_delivery,
    unit_delivery_rules,
)
from novel_writer.generation.budget import (
    input_tokens,
    request_for,
    request_preview,
    validate_capacity,
)
from novel_writer.generation.content import parse_object
from novel_writer.generation.craft_models import enabled as craft_enabled
from novel_writer.generation.craft_models import read_spec
from novel_writer.generation.craft_templates import apply_template
from novel_writer.generation.prompt_inspection import (
    program_constraints,
    program_rules,
    source_bindings,
)
from novel_writer.generation.prompt_templates import supported, variant_for
from novel_writer.generation.service import GenerationService
from novel_writer.generation.template_catalog import TemplateText, Variant
from novel_writer.providers.base import ModelRequest
from novel_writer.services.craft_template_store import CraftTemplateStore as PromptTemplateStore
from novel_writer.services.errors import NotFoundError, WorkflowError
from novel_writer.services.provider_profiles import ProviderProfile

router = APIRouter(prefix="/api/prompt-templates", tags=["prompt-templates"])


class SaveTemplate(BaseModel):
    expected_revision: str = Field(min_length=1, max_length=80)
    template: TemplateText | None
    note: str = Field(default="手动修改", max_length=300)
    program_settings: editable_rules.ProgramSettings | None = None


class PreviewTemplate(BaseModel):
    project_id: UUID
    batch_id: UUID
    variant: Variant
    template: TemplateText | None = None
    program_settings: editable_rules.ProgramSettings | None = None


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
    source_bindings: dict[str, Any]


def store(request: Request) -> PromptTemplateStore:
    return PromptTemplateStore(request.app.state.provider_profile_store.path)


def catalog(current: PromptTemplateStore) -> dict[str, Any]:
    bundle = current.current()
    return {
        "revision": bundle["revision"],
        "format": bundle["format"],
        "adaptation": bundle.get("adaptation", {}),
        "parent_revision": bundle.get("parent_revision"),
        "engine_system": template_catalog.ENGINE_SYSTEM,
        "entries": [
            {
                "variant": variant,
                "label": template_catalog.LABELS[variant],
                "text": current.text(variant, bundle).model_dump(),
                "default_text": template_catalog.default_text(variant).model_dump(),
                "customized": variant in bundle["templates"],
                "possible_conflicts": template_catalog.possible_conflicts(
                    current.text(variant, bundle)
                ),
                "fields": [
                    {"key": k, "label": label, "required": required}
                    for k, label, required in template_catalog.FIELDS[variant]
                ],
                "output_requirement": template_catalog.OUTPUTS[variant],
                "program_rules": program_rules(variant, bundle),
                "program_settings": progression_rules.effective(
                    variant,
                    editable_rules.settings(variant, bundle),
                ).model_dump(exclude_none=True),
                "default_program_settings": progression_rules.effective(variant).model_dump(
                    exclude_none=True
                ),
                "program_constraints": program_constraints(variant, bundle),
            }
            for variant in template_catalog.FIELDS
        ],
        "history": current.history() + current.legacy.history(),
    }


@router.get("")
def get_templates(request: Request) -> dict[str, Any]:
    return catalog(store(request))


@router.put("/{variant}")
def save_template(variant: Variant, payload: SaveTemplate, request: Request) -> dict[str, Any]:
    current = store(request)
    current.save(
        variant,
        payload.template,
        payload.expected_revision,
        payload.note,
        payload.program_settings,
        "program_settings" in payload.model_fields_set,
    )
    return catalog(current)


@router.get("/versions/{revision}/{variant}")
def get_version(revision: UUID, variant: Variant, request: Request) -> dict[str, Any]:
    current = store(request)
    try:
        bundle = current.version(str(revision))
    except NotFoundError:
        legacy = current.legacy
        bundle = legacy.version(str(revision))
        return {
            "text": legacy.text(variant, bundle).model_dump(),
            "customized": variant in bundle["templates"],
            "revision": bundle["revision"],
            "program_settings": editable_rules.effective(variant).model_dump(exclude_none=True),
        }
    return {
        "text": current.text(variant, bundle).model_dump(),
        "customized": variant in bundle["templates"],
        "revision": bundle["revision"],
        "program_settings": editable_rules.effective(
            variant,
            editable_rules.settings(variant, bundle),
        ).model_dump(exclude_none=True),
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
    spec = read_spec(batch.spec)
    if not supported(spec) or not craft_enabled(spec):
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
        system, raw = await asyncio.to_thread(
            unit_delivery.raw_render, spec, batch.snapshot, "plan"
        )
        source = parse_object(raw)
        if format_trial.enabled(batch.snapshot):
            trial_reports: dict[str, Any] = {}
            system, raw = trial_prompts.render_for(
                spec, batch.snapshot, "plan", reports=trial_reports,
            )
            source = trial_reports["prompt_template_source"]
        model_request = request_for(spec, profile, "plan", system, raw)
        counting = batch.snapshot["counting"]["chief"]
        action = "plan"
        description = "使用该预览冻结的 Chief 资料；没有发起模型调用。"
    else:
        raise WorkflowError("该批次还没有此角色的调用资料，请选择有对应记录的批次进行真实资料预览")

    def render() -> PromptPreview:
        from novel_writer.generation.output_contract import KEY as OUTPUT_KEY
        from novel_writer.generation.output_contract_v3 import binding as output_binding

        original = call.request if call else batch.snapshot
        policy = {
            key: original.get(key)
            for key in (
                craft_contract.KEY,
                editable_contract.KEY,
                OUTPUT_KEY,
                unit_delivery.KEY,
                reliability_contract.KEY,
                format_trial.KEY,
                progression_contract.KEY,
            )
            if key != format_trial.KEY or key in original
        }
        draft_rules = "program_settings" in payload.model_fields_set
        use_rules = draft_rules or editable_contract.checked(policy)
        if draft_rules:
            value = editable_rules.validate(
                payload.variant, payload.program_settings or editable_rules.ProgramSettings()
            )
            policy.update(
                {
                    craft_contract.KEY: craft_contract.binding(),
                    editable_contract.KEY: editable_contract.binding(),
                    OUTPUT_KEY: output_binding(),
                }
            )
        elif call:
            value = editable_rules.ProgramSettings.model_validate(
                call.request.get(editable_rules.RECEIPT, {}).get(payload.variant, {})
            )
        else:
            value = editable_rules.settings(payload.variant, batch.snapshot.get("prompt_templates"))
        preview_source = deepcopy(source)
        if use_rules and payload.variant == "chief":
            maximum = editable_rules.effective(payload.variant, value).maximum_new_characters
            assert maximum is not None
            slots = preview_source.get("creative_autonomy", {}).get("new_character_slots", [])
            # Local examples only, stable across preview requests and never persisted to a batch.
            slots = (
                slots
                + [
                    str(uuid5(NAMESPACE_URL, f"prompt-draft:{batch.id}:{i}"))
                    for i in range(maximum)
                ]
            )[:maximum]
            preview_source = editable_contract.configure_source(preview_source, maximum, slots)
        if unit_delivery.checked(policy):
            preview_source = unit_delivery.project_source(preview_source, action)
            value = unit_delivery_rules.effective(payload.variant, value)
        if progression_contract.checked(policy):
            value = progression_rules.effective(payload.variant, value)
        template = payload.template
        if template is not None:
            system, task, info = (
                editable_contract.apply_template(payload.variant, template, preview_source, value)
                if use_rules
                else apply_template(payload.variant, template, preview_source)
            )
        else:
            system = template_catalog.SYSTEMS[payload.variant]
            if payload.variant == "title":
                system = (
                    '你只为每章提供标题，返回JSON {"chapters":[{"id":"给定ID",'
                    '"titles":["标题"]}]}。不修改正文。'
                    if "chapters" in source
                    else '你负责为给定正文命名，只返回JSON {"titles":["标题"]}，'
                    "一至三项；不改正文。"
                )
            task = json.dumps(preview_source, ensure_ascii=False, separators=(",", ":"))
            info = {
                "omitted_optional": [],
                "engine_contract": {
                    k: preview_source[k]
                    for k in ("output_schema", "output_contract", "writable_fields")
                    if k in preview_source
                },
            }
        if use_rules:
            if template is None:
                normalized, _ = craft_contract.finish(system, task, preview_source, action)
                suffix = craft_contract.guidance(action)
                system = normalized.removesuffix("\n\n" + suffix) if suffix else normalized
                if value.texts.get("engine_system"):
                    system += "\n\n" + value.texts["engine_system"]
            system, task = editable_contract.finish(system, task, preview_source, action, value)
        elif craft_contract.checked(policy):
            system, task = craft_contract.finish(system, task, preview_source, action)
        if format_trial.checked(policy):
            system, task = trial_prompts.finish(
                system, task, preview_source, custom=template is not None,
            )
        proposed = model_request.model_copy(update={"system_prompt": system, "user_prompt": task})
        proposed = progression_contract.prepare_output(
            proposed,
            profile,
            action,
            policy,
            {
                "prompt_template_source": preview_source,
                editable_rules.RECEIPT: {payload.variant: value.model_dump(exclude_none=True)},
            },
        )
        task = proposed.user_prompt
        system = proposed.system_prompt
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
            source_description=description
            + (
                " 当前附加指导与人数设置按编辑草稿模拟；不会修改来源批次的规则、人物 ID 或授权。"
                if draft_rules
                else ""
            ),
            source_bindings={
                **source_bindings(call.request if call else batch.snapshot),
                **({"format_trial": policy[format_trial.KEY]}
                   if policy.get(format_trial.KEY) else {}),
            },
        )

    return await asyncio.to_thread(render)
