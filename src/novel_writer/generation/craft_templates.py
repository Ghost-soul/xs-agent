"""Template catalog v2, isolated from template-v1's source fingerprint."""

from __future__ import annotations

import json
import re
from collections import Counter
from copy import deepcopy
from typing import Any

from novel_writer.generation import craft_prompts, prompt_templates, template_catalog
from novel_writer.generation.content import fingerprint
from novel_writer.generation.template_catalog import TemplateText
from novel_writer.services.errors import WorkflowError

FORMAT = "stage-craft-template-v1"
FIELDS = deepcopy(template_catalog.FIELDS)
FIELDS["chief"] += [("stage_scale", "阶段规模", True), ("macro_reference", "长线参考", True)]
FIELDS["writer"] += [("stage_scale", "阶段规模与当前份量", True)]
SYSTEMS = craft_prompts.SYSTEMS
LABELS = template_catalog.LABELS
OUTPUTS = template_catalog.OUTPUTS
ENGINE_SYSTEM = template_catalog.ENGINE_SYSTEM


def possible_conflicts(template: TemplateText) -> list[dict[str, Any]]:
    return [
        {
            "field": field,
            "line": n,
            "text": line,
            "notice": "此作者文字可能与阶段规模冲突，请自行核对；未自动改写。",
        }
        for field, value in template.model_dump().items()
        for n, line in enumerate(value.splitlines(), 1)
        if re.search(r"不设.{0,3}(?:字数|篇幅)目标|无字数目标", line)
    ]


def default_text(variant: str) -> TemplateText:
    return TemplateText(
        system_text=SYSTEMS[variant],
        task_template="\n\n".join(label + "\n{{" + key + "}}" for key, label, _ in FIELDS[variant]),
    )


def validate_template(variant: str, template: TemplateText) -> list[str]:
    if variant not in FIELDS:
        raise WorkflowError("此角色没有活动模板")
    found = prompt_templates.TOKEN.findall(template.task_template)
    residue = prompt_templates.TOKEN.sub("", template.task_template)
    if "{{" in residue or "}}" in residue:
        raise WorkflowError("占位符格式无效")
    allowed = {key for key, _, _ in FIELDS[variant]}
    missing = [k for k, _, required in FIELDS[variant] if required and k not in found]
    unknown = sorted(set(found) - allowed)
    duplicate = [k for k, count in Counter(found).items() if count > 1]
    if missing or unknown or duplicate:
        raise WorkflowError(f"占位符检查失败：缺少 {missing}；未知 {unknown}；重复 {duplicate}")
    if not template.system_text.strip() or not template.task_template.strip():
        raise WorkflowError("系统文本和任务模板不能为空")
    return [k for k, _, required in FIELDS[variant] if not required and k not in found]


def checked_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    if bundle.get("format") != FORMAT or bundle.get("sha256") != fingerprint(
        {k: v for k, v in bundle.items() if k != "sha256"}
    ):
        raise WorkflowError("新版模板格式或 SHA 不匹配")
    for variant, value in bundle["templates"].items():
        validate_template(variant, TemplateText.model_validate(value))
    return bundle


def adapt(variant: str, text: TemplateText) -> tuple[TemplateText, list[dict[str, str]]]:
    if text == template_catalog.default_text(variant):
        return default_text(variant), [{"reason": "已知完整内置模板升级为独立创作合同"}]
    edits = []
    system, task = text.system_text, text.task_template
    # Exact known released phrases only; arbitrary author prose is never inferred.
    replacements = {
        (
            "因果需要多少单元就设计多少，不超过 unit_limit 或 \n"
            "scene_count，不设字数目标或强制关系进度。"
        ): (
            "依 stage_scale 的首选单元数及可接受区间设计，不超过 unit_limit；"
            "单单元遵守 scene_count，不强制关系进度。"
        ),
        "不自行增加后续事件或设定字数目标。": (
            "不抢写后续事件；按本次 stage_scale 参考份量充分展开当前过程。"
        ),
        "不设字数目标，": "参考篇幅不是拒收下限或截断线，",
        "单元数是上限；": "unit_limit 是授权上限，另按 stage_scale 设计首选数量与展开份量；",
    }
    for old, new in replacements.items():
        for field in ("system_text", "task_template"):
            value = system if field == "system_text" else task
            if old in value:
                value = value.replace(old, new)
                edits.append({"field": field, "before": old, "after": new})
                if field == "system_text":
                    system = value
                else:
                    task = value
    if variant in {"chief", "writer"}:
        system += "\n\n" + (craft_prompts.CHIEF if variant == "chief" else craft_prompts.WRITER)
        edits.append({"field": "system_text", "reason": "补充阶段规模、过程和创作权限"})
    found = prompt_templates.TOKEN.findall(task)
    for key, label, required in FIELDS[variant]:
        if required and key not in found:
            task += "\n\n" + label + "\n{{" + key + "}}"
            edits.append({"field": "task_template", "after": "{{" + key + "}}"})
    return TemplateText(system_text=system, task_template=task), edits


def apply_template(
    variant: str, template: TemplateText, payload: dict[str, Any]
) -> tuple[str, str, dict[str, Any]]:
    omitted = validate_template(variant, template)
    if variant == "title":
        payload = {
            **payload,
            "title_source": {k: payload[k] for k in ("body", "chapters") if k in payload},
        }
    values = {}
    for key, _, required in FIELDS[variant]:
        value: Any = payload
        for part in key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if required and value is None:
            raise WorkflowError(f"新版请求缺少必要资料 {key}")
        values[key] = value
    task = prompt_templates.TOKEN.sub(
        lambda m: json.dumps(values[m[1]], ensure_ascii=False, separators=(",", ":")),
        template.task_template,
    )
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
    engine["response_requirement"] = OUTPUTS[variant]
    if variant == "title" and "chapters" in payload:
        engine["response_requirement"] = (
            '返回 JSON {"chapters":[{"id":"给定ID","titles":["标题"]}]}。'
        )
    return (
        template.system_text + "\n\n" + ENGINE_SYSTEM,
        task + "\n\n【程序输出与来源合同】\n" + json.dumps(engine, ensure_ascii=False),
        {
            "omitted_optional": omitted,
            "engine_contract": engine,
        },
    )
