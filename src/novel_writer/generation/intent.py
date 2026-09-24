"""Author intent replaces the redundant relationship gate for new requests only."""

from __future__ import annotations

import inspect
import sys
from typing import Any

from novel_writer.generation.casting import prepare_cast as previous_cast
from novel_writer.generation.content import fingerprint, json_text, parse_object
from novel_writer.generation.longform_prompts import contract_for as previous_contract
from novel_writer.generation.longform_prompts import render_for as previous_render
from novel_writer.generation.novel import role_for
from novel_writer.generation.schemas import GenerationSpec

REVISION = "author-intent-v1"
INTENT = """题材与作者描述共同决定关系发展，不另设关系方向或关系对象授权表。
作者没有指定关系对象、关系阶段或视角时，由 Chief 根据完整主导卡、人物事实和当前故事自主设计，
在原场面设计中写明承载人物、叙述视角、具体行动、感受、自主回应和关系后果；Writer 据此展开。
作者选择百合卡已表达女性关系成为剧情动力的意图，不需要再次询问是否允许恋爱。
不得把百合降格为泛泛互助或陪伴；主导卡要求的特殊在意、吸引和关系变化应推动实际选择。
其他题材同样从卡文确定核心事件，不机械套用爱情任务。
尊重作者在方向、边界和后续答复中明确指定的对象、进度与禁区；明确边界优先。
没有指定最终配对不等于禁止当下吸引、双向回应或合乎剧情的关系进展，也不等于开场已是情侣。
人物身份以正式档案为准，不凭姓名推断性别或性取向，不改写已发生事实来凑配对。
普通选角、视角和关系推进属于创作判断，不提交作者问题；只有资料不足或明确硬冲突使当前单元
无法可靠创作时才提出具体问题，并说明冲突来源。计划、未来承诺和推测仍不是已发生事实。
视角未指定时优先保持既有叙述方式，必要变化在场面设计中交代；Writer 避免无依据跳视角。"""


def recall_text(spec: GenerationSpec) -> str:
    if spec.relationship_scope != "genre-led":
        return spec.direction
    return "\n".join([spec.direction, spec.author_boundaries, spec.viewpoint])


def prepare_cast(spec: GenerationSpec, state: dict[str, Any]) -> dict[str, Any]:
    if spec.relationship_scope != "genre-led":
        return previous_cast(spec, state)
    # Mentions recall candidates, including negative mentions; only Chief interprets intent.
    selection = previous_cast(spec.model_copy(update={"direction": recall_text(spec)}), state)
    for candidate in selection["candidates"]:
        candidate["reasons"] = [
            "作者描述或视角提及" if reason == "方向或视角提及" else reason
            for reason in candidate["reasons"]
        ]
    selection["author_input_sha256"] = fingerprint(recall_text(spec))
    return selection


def contract_for(spec: GenerationSpec) -> str:
    base = previous_contract(spec)
    if spec.relationship_scope != "genre-led":
        return base
    return fingerprint({"base": base, "intent": inspect.getsource(sys.modules[__name__])})


def render_for(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    plan: dict[str, Any] | None = None,
    body: str | None = None,
    author_note: str | None = None,
    reports: dict[str, Any] | None = None,
) -> tuple[str, str]:
    system, user = previous_render(spec, snapshot, action, plan, body, author_note, reports)
    if spec.relationship_scope != "genre-led" or role_for(action) not in {"chief", "writer"}:
        return system, user
    if spec.stage_mode == "longform-v1":
        payload = parse_object(user)
        remainder = ""
    else:
        author_section, separator, remainder = user.partition("\n\n")
        if not separator or not author_section.startswith("作者明确输入\n"):
            raise ValueError("题材自主设计的作者输入布局失配")
        payload = parse_object(author_section.split("\n", 1)[1])
    payload.pop("relationship_scope", None)
    payload.pop("relationship_character_ids", None)
    payload["creative_policy"] = REVISION
    payload["viewpoint"] = spec.viewpoint or "由 Chief 随场面设计确定，优先延续已有叙述方式"
    system = system.replace(
        "不得自动决定最终CP或跨越作者关系边界。", "尊重作者明确的关系边界。"
    )
    rendered = json_text(payload)
    if remainder:
        rendered = "作者明确输入\n" + rendered + "\n\n" + remainder
    return system + "\n" + INTENT, rendered
