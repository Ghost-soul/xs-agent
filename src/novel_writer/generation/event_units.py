"""Complete event units and non-reasoning Writer requests for new authorizations."""

from __future__ import annotations

import inspect
import json
import sys
from typing import Any

from novel_writer.generation import budget, narrative_drive
from novel_writer.generation.content import fingerprint, parse_object
from novel_writer.generation.novel import role_for
from novel_writer.generation.schemas import GenerationSpec

POLICY = "plot-led-v3"

CHIEF = """【单元规模：安排能够推进故事的完整事件过程】
一个叙事单元围绕一个阶段性目标，容纳为它服务的连续场景、行动、回应和实际结果。
让人物在本单元内真正做成、改变、发现或失去某件事，下一单元从改变后的条件出发。
同一目标下的见面、传话、准备、办理手续与随后行动通常放在同一单元；只有它们本身形成
足以改变主线的重要结果时才独立成单元。不要把一次简短交谈或一个过渡步骤默认切成一个单元。
例如获知线索、核实线索、据此行动及遇到实际结果，可以属于同一个连贯单元；
具体过程由当前人物与故事决定，不要求每单元套用相同阶段或固定次数的反转。
给当前阶段安排实质推进，不把准备工作层层拆开、把重要行动全部推给后续阶段。
单元上限不是要填满的数量；因果紧密的小段落应合并，无需为凑数量拆分。
event 写完整事件的起点、主要过程和抵达的节点；choice_and_response 写关键选择及回应；
consequence 写已经产生的阶段性结果，以及下一步因此获得或失去的条件。
一个单元可以跨连续场景，但保留必要时间与地点衔接，不跳过决定性选择，不凭空解决矛盾。
不增加字数目标、最低篇幅、固定场景配额或强制危险升级。历史检查点只调整未写部分。
"""

WRITER = """【单元执行：完成计划中的整个事件过程】
current_task 可以包含为同一目标服务的多个连续场景。本次应写到这个完整事件的阶段性结果，
而不是在第一次对话、到达地点或作出决定后就结束。先判断计划中哪些行动仍未实际发生。
在当前单元内完成必要的行动、回应、调整与后果；承接关键过程，重复说明和纯手续可以压缩。
已经得到的结果要改变接下来的行动，避免重复同一轮试探或把行动一直写成打算。
“不抢写后续单元”限制的是下一单元的任务，不限制展开本单元已经规划的连续场景。
保留当前有效计划、作者修改、人物声音和事实边界；不自行增加后续事件或设定字数目标。
"""

CHIEF_TASK = (
    "按完整事件过程设计单元：同一目标下的连续场景、准备和实际行动尽量合并，"
    "让本单元抵达会改变后续条件的结果。单元数是上限；不要用琐碎步骤占满槽位，"
    "也不要把重要行动全部留给下一阶段。"
)
WRITER_TASK = (
    "完成当前有效任务的整个事件过程；必要时连续展开多个场景，写到计划中的阶段性结果。"
    "当前单元内的行动与回应仍须完成，不在准备、到达或首次表态后提前收束。"
)


def enabled(spec: GenerationSpec) -> bool:
    return spec.workflow == "novel-run-v1" and spec.narrative_policy == POLICY


def previous_spec(spec: GenerationSpec) -> GenerationSpec:
    return spec.model_copy(update={"narrative_policy": "plot-led-v2"}) if enabled(spec) else spec


def contract_for(spec: GenerationSpec) -> str:
    base = narrative_drive.contract_for(previous_spec(spec))
    if not enabled(spec):
        return base
    return fingerprint(
        {
            "base": base,
            "event_units": inspect.getsource(sys.modules[__name__]),
            "request_builder": inspect.getsource(budget.request_for),
        }
    )


def amendment_contract(spec: GenerationSpec) -> str:
    return contract_for(spec) if enabled(spec) else narrative_drive.amendment_contract(spec)


def describe_fields(schema: dict[str, Any]) -> None:
    additions = {
        "event": "包含同一目标下相连的主要行动过程和抵达节点，不只列准备或一次传话。",
        "choice_and_response": "呈现推动完整事件的关键选择、回应及必要调整，不反复原地试探。",
        "consequence": "本单元实际产生的阶段性结果，说明它如何改变下一步条件。",
    }
    for name, field in schema.get("properties", {}).items():
        if name in additions:
            field["description"] = field.get("description", "") + additions[name]
    for definition in schema.get("$defs", {}).values():
        describe_fields(definition)


def render_for(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    plan: dict[str, Any] | None = None,
    body: str | None = None,
    author_note: str | None = None,
    reports: dict[str, Any] | None = None,
) -> tuple[str, str]:
    system, raw = narrative_drive.render_for(
        previous_spec(spec), snapshot, action, plan, body, author_note, reports
    )
    role = role_for(action)
    if (
        not enabled(spec)
        or spec.length_policy != "unit-v1"
        or role not in {"chief", "writer"}
        or action in {"title", "rewrite"}
    ):
        return system, raw
    payload = parse_object(raw)
    first = {
        key: payload.pop(key)
        for key in ("story_task", "narrative_design", "plot_execution", "stage_context")
        if key in payload
    }
    first["unit_scope_guidance"] = CHIEF_TASK if role == "chief" else WRITER_TASK
    if role == "chief":
        describe_fields(payload["output_schema"])
    system = (CHIEF if role == "chief" else WRITER) + "\n" + system
    return system, json.dumps({**first, **payload}, ensure_ascii=False, separators=(",", ":"))
