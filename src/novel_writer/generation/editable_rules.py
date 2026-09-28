"""Versioned author-editable additions, separate from published renderers."""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from novel_writer.generation import creative_contract as creative
from novel_writer.generation import output_contract_v2 as language
from novel_writer.generation import output_contract_v3 as scope
from novel_writer.generation.craft_templates import FIELDS
from novel_writer.generation.template_catalog import ENGINE_SYSTEM
from novel_writer.services.errors import WorkflowError

SETTINGS = "program_settings"
RECEIPT = "prompt_program_settings"


class ProgramSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    texts: dict[str, Annotated[str, Field(max_length=80000)]] = Field(default_factory=dict)
    maximum_new_characters: int | None = Field(default=None, ge=0, le=12, strict=True)


def definitions(variant: str) -> list[dict[str, str]]:
    if variant not in FIELDS:
        raise WorkflowError("未知活动模板")
    rules = [
        {
            "key": "engine_system",
            "label": "输出与来源说明",
            "condition": "使用手动模板或单独设置此段时加入；实际输出结构与事实证据另由程序校验",
            "default_text": ENGINE_SYSTEM,
        }
    ]
    action = {"chief": "plan", "writer": "write:1", "editor": "amend"}.get(variant, variant)
    guidance = creative.guidance(action)
    if guidance:
        rules.append(
            {
                "key": "creative_guidance",
                "label": "创作自主范围与新增人物" if variant == "chief" else "新增人物接力要求",
                "condition": "新阶段与其后续调用；独立修订沿用其人物来源范围",
                "default_text": guidance.replace(
                    "最多三人", "不超过 creative_autonomy.maximum_new_characters 所示人数"
                ),
            }
        )
    if variant == "chief":
        rules.extend(
            [
                {
                    "key": "question_guidance",
                    "label": "作者问题说明",
                    "condition": "Chief 请求；问题对象结构由任务合同提供",
                    "default_text": creative.QUESTION_GUIDANCE,
                },
                {
                    "key": "chief_language",
                    "label": "交付语言与完整性",
                    "condition": "Chief 请求；字段名、ID 与实际结构校验保持",
                    "default_text": language.CHIEF_LANGUAGE,
                },
                {
                    "key": "chief_scope",
                    "label": "阶段规模与事件容量",
                    "condition": "阶段篇幅模式；实际字数与单元数来自创作页设置",
                    "default_text": scope.CHIEF_SCOPE,
                },
            ]
        )
    elif variant == "writer":
        rules.append(
            {
                "key": "writer_scope",
                "label": "当前单元的展开要求",
                "condition": "阶段篇幅模式且已有单元参考值；实际数字来自有效计划",
                "default_text": scope.WRITER_SCOPE,
            }
        )
    return rules


def validate(variant: str, value: ProgramSettings) -> ProgramSettings:
    known = {r["key"] for r in definitions(variant)}
    if set(value.texts) - known:
        raise WorkflowError("当前角色含未知附加指导字段")
    if variant != "chief" and value.maximum_new_characters is not None:
        raise WorkflowError("新增人物上限只在 Chief 中设置")
    return value


def checked_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    from novel_writer.generation.craft_templates import checked_bundle as checked_base

    checked_base(bundle)
    for variant, value in bundle.get(SETTINGS, {}).items():
        validate(variant, ProgramSettings.model_validate(value))
    return bundle


def settings(variant: str, bundle: dict[str, Any] | None = None) -> ProgramSettings:
    value = (bundle or {}).get(SETTINGS, {}).get(variant, {})
    return validate(variant, ProgramSettings.model_validate(value))


def effective(variant: str, value: ProgramSettings | None = None) -> ProgramSettings:
    value = validate(variant, value or ProgramSettings())
    return ProgramSettings(
        texts={**{r["key"]: r["default_text"] for r in definitions(variant)}, **value.texts},
        maximum_new_characters=(
            value.maximum_new_characters if value.maximum_new_characters is not None else 3
        )
        if variant == "chief"
        else None,
    )


def rules(variant: str, value: ProgramSettings | None = None) -> list[dict[str, str]]:
    current = effective(variant, value)
    return [{**r, "text": current.texts[r["key"]]} for r in definitions(variant)]


def limit(bundle: dict[str, Any] | None = None) -> int:
    value = effective("chief", settings("chief", bundle)).maximum_new_characters
    assert value is not None
    return value
