"""Outer rendering contract; published templates and normal requests are untouched."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from novel_writer.generation import (
    creative_contract,
    editable_contract,
    editable_rules,
    format_trial,
    unit_delivery,
    unit_delivery_rules,
)
from novel_writer.generation import (
    reliability_contract as previous,
)
from novel_writer.generation.content import fingerprint, json_text, parse_object
from novel_writer.generation.prompt_templates import variant_for
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.generation.template_catalog import TemplateText
from novel_writer.providers.base import ModelRequest
from novel_writer.services.provider_profiles import ProviderProfile


def contract_for(spec: GenerationSpec, snapshot: dict[str, Any] | None = None) -> str:
    base = previous.contract_for(spec, snapshot)
    return (
        fingerprint({"base": base, format_trial.KEY: format_trial.binding()})
        if (format_trial.checked(snapshot or {}))
        else base
    )


def render_for(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    plan: dict[str, Any] | None = None,
    body: str | None = None,
    author_note: str | None = None,
    reports: dict[str, Any] | None = None,
) -> tuple[str, str]:
    if not format_trial.checked(snapshot):
        return previous.render_for(spec, snapshot, action, plan, body, author_note, reports)
    reports = reports if reports is not None else {}
    system, raw = previous.raw_render(spec, snapshot, action, plan, body, author_note, reports)
    source = parse_object(raw)  # Program-owned input, not a model output.
    trial = {
        "mode": format_trial.REVISION,
        "format_required": True,
        "local_output_validation": False,
        "writes_facts": False,
        "chief_response": (plan or {}).get("raw_response"),
        "schedule": (plan or {}).get("trial_schedule"),
        "continuity_notes": deepcopy(reports.get("trial_notes", [])),
        "boundary": "Chief 是未校验的设计，Memory 是未校验的记录；均不是正式事实。"
        "以正式起点、已写正文与作者边界为依据，记录冲突时优先对照原文。",
    }
    if action.startswith("write:"):
        # The previous renderer has no validated handoffs in this experiment.
        # Replace its formal-only ending with actual prose from the last trial unit.
        if reports.get("trial_previous_prose"):
            source["continuity"] = deepcopy(reports["trial_previous_prose"])
        if not (plan or {}).get("scenes", [{}])[int(action.split(":")[1]) - 1].get("character_ids"):
            trial["reference_characters_not_assigned_cast"] = snapshot["context"].get(
                "characters", []
            )
    source["format_trial"] = trial
    variant = variant_for(action)
    bundle = reports.get("prompt_templates", snapshot.get("prompt_templates"))
    value = unit_delivery_rules.effective(variant, editable_rules.settings(variant, bundle))
    task = json_text(source)
    if bundle:
        editable_rules.checked_bundle(bundle)
        text = bundle["templates"].get(variant)
        if text is not None:
            system, task, _ = editable_contract.apply_template(
                variant,
                TemplateText.model_validate(text),
                source,
                value,
            )
        elif value.texts.get("engine_system"):
            system += "\n\n" + value.texts["engine_system"]
        reports["prompt_template_revision"] = bundle["revision"]
    reports.update(
        {
            "prompt_template_source": source,
            creative_contract.KEY: snapshot.get(creative_contract.KEY),
            editable_contract.KEY: editable_contract.binding(),
            unit_delivery.KEY: unit_delivery.binding(),
            editable_rules.RECEIPT: {variant: value.model_dump(exclude_none=True)},
            format_trial.KEY: snapshot[format_trial.KEY],
        }
    )
    system, task = editable_contract.finish(system, task, source, action, value)
    return finish(system, task, source, custom=bool(bundle and bundle["templates"].get(variant)))


def finish(
    system: str,
    task: str,
    source: dict[str, Any],
    *,
    custom: bool,
) -> tuple[str, str]:
    # Custom templates do not yet have a trial placeholder. Include the actual
    # unvalidated sources visibly and count them as part of the final request.
    if custom:
        task += "\n\n【试验资料与来源边界】\n" + json_text(source["format_trial"])
    return system + (
        "\n\n【格式要求保留的试验】仍按本次输出合同与 output_schema 返回内容。"
        "本地不对输出格式、字段和报告结论做验收，不因此要求重试。"
        "程序安排的单元槽位只表示调用进度，不代表模型已给出完整场景或事实。"
        "按 Chief 原文设计和已写进度完成当前完整事件，避免重复已写内容。"
        "Memory 只提供未校验连续性笔记，不更新正式或候选事实状态。"
    ), task


def prepare_output(
    request: ModelRequest,
    profile: ProviderProfile,
    action: str,
    snapshot: dict[str, Any],
    reports: dict[str, Any],
) -> ModelRequest:
    prepared = previous.prepare_output(request, profile, action, snapshot, reports)
    if format_trial.checked(snapshot):
        reports[format_trial.KEY] = snapshot[format_trial.KEY]
        if "output_format" in reports:
            reports["output_format"]["reason"] = str(reports["output_format"]["reason"]).replace(
                "必要字段仍由本地验证", "试验中不做本地输出格式校验"
            )
    return prepared
