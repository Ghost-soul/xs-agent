"""Versioned template overlay. No live defaults are read while rendering a saved batch."""

from __future__ import annotations

import inspect
import json
import re
import sys
from collections import Counter
from typing import Any

from novel_writer.generation import event_units, template_catalog
from novel_writer.generation.content import fingerprint, parse_object
from novel_writer.generation.novel import role_for
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.generation.template_catalog import FIELDS, TemplateText
from novel_writer.services.errors import WorkflowError

FORMAT = "template-v1"
TOKEN = re.compile(r"\{\{\s*([a-z][a-z0-9_.]*)\s*\}\}")
KEY = "prompt_templates"


def supported(spec: GenerationSpec) -> bool:
    return (
        spec.workflow == "novel-run-v1"
        and spec.context_policy == "chief-focus-v4"
        and spec.narrative_policy == "plot-led-v3"
        and spec.length_policy == "unit-v1"
        and spec.writing_policy == "guided-v1"
    )


def variant_for(action: str) -> str:
    return action if action in {"rewrite", "title"} else role_for(action)


def validate_template(variant: str, template: TemplateText) -> list[str]:
    if variant not in FIELDS:
        raise WorkflowError("此角色没有活动模板；不会启用历史 Reader")
    if not template.system_text.strip() or not template.task_template.strip():
        raise WorkflowError("系统文本和任务模板不能为空")
    found = TOKEN.findall(template.task_template)
    residue = TOKEN.sub("", template.task_template)
    if "{{" in residue or "}}" in residue:
        raise WorkflowError("占位符格式无效，只支持 {{字段名}}，不执行表达式或模板代码")
    allowed = {key for key, _, _ in FIELDS[variant]}
    unknown = sorted(set(found) - allowed)
    missing = [key for key, _, required in FIELDS[variant] if required and key not in found]
    duplicated = sorted(key for key, count in Counter(found).items() if count > 1)
    if unknown or missing or duplicated:
        raise WorkflowError(
            f"任务模板占位符检查失败：未知 {unknown}；缺少必要资料 {missing}；重复 {duplicated}"
        )
    return [key for key, _, required in FIELDS[variant] if not required and key not in found]


def checked_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(bundle, dict) or not isinstance(bundle.get("templates"), dict):
        raise WorkflowError("Prompt 模板版本记录结构无效")
    if bundle.get("format") != FORMAT or bundle.get("sha256") != fingerprint(
        {k: v for k, v in bundle.items() if k != "sha256"}
    ):
        raise WorkflowError("冻结 Prompt 模板格式或摘要不匹配")
    for variant, data in bundle["templates"].items():
        validate_template(variant, TemplateText.model_validate(data))
    return bundle


def contract_for(spec: GenerationSpec, snapshot: dict[str, Any] | None = None) -> str:
    base = event_units.contract_for(spec)
    bundle = (snapshot or {}).get(KEY)
    if bundle is None:
        return base
    checked_bundle(bundle)
    return fingerprint(
        {
            "base": base,
            "bundle": bundle,
            "renderer": inspect.getsource(sys.modules[__name__]),
            "catalog": inspect.getsource(template_catalog),
        }
    )


def amendment_contract(spec: GenerationSpec, snapshot: dict[str, Any] | None = None) -> str:
    if (snapshot or {}).get(KEY) is not None:
        return contract_for(spec, snapshot)
    return event_units.amendment_contract(spec)


def apply_template(
    variant: str,
    template: TemplateText,
    payload: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    omitted = validate_template(variant, template)
    if variant == "title":
        payload = {
            **payload,
            "title_source": {k: payload[k] for k in ("body", "chapters") if k in payload},
        }
    values: dict[str, Any] = {}
    for key, _, required in FIELDS[variant]:
        value: Any = payload
        for part in key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if required and value is None:
            raise WorkflowError(f"此来源缺少 {key}，请选择有完整对应角色资料的批次")
        values[key] = value
    task = TOKEN.sub(
        lambda match: json.dumps(values[match[1]], ensure_ascii=False, separators=(",", ":")),
        template.task_template,
    )
    # Required interface/context fields are always carried outside the editable layout.
    engine = {
        k: payload[k]
        for k in (
            "output_schema",
            "output_contract",
            "writable_fields",
            "reference_boundary",
            "cast_scope",
            "unit_limit",
            "scene_count",
            "current_plan",
            "written_candidate",
            "completed_units",
        )
        if k in payload
    }
    engine["response_requirement"] = template_catalog.OUTPUTS[variant]
    if variant == "title" and "chapters" in payload:
        engine["response_requirement"] = (
            '返回 JSON {"chapters":[{"id":"给定ID","titles":["标题"]}]}，不修改正文。'
        )
    return (
        template.system_text + "\n\n" + template_catalog.ENGINE_SYSTEM,
        task + "\n\n【程序输出与来源合同】\n" + json.dumps(engine, ensure_ascii=False),
        {"omitted_optional": omitted, "engine_contract": engine},
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
    system, raw = event_units.render_for(spec, snapshot, action, plan, body, author_note, reports)
    bundle = (reports or {}).get(KEY, snapshot.get(KEY))
    if bundle is None:
        return system, raw
    checked_bundle(bundle)
    if not supported(spec):
        raise WorkflowError("此历史策略不支持手动模板，不能静默套用新合同")
    variant = variant_for(action)
    template = bundle["templates"].get(variant)
    if template is None:
        return system, raw
    payload = parse_object(raw)
    system, task, _ = apply_template(variant, TemplateText.model_validate(template), payload)
    if reports is not None:
        reports["prompt_template_source"] = payload
        reports["prompt_template_revision"] = bundle["revision"]
    return system, task
