"""Opt-in stage automation: creative unknowns are not author approval gates."""

import inspect
import sys
from typing import Any

from novel_writer.generation.content import fingerprint, json_text, parse_object
from novel_writer.generation.context_budget import contract_for as previous_contract
from novel_writer.generation.context_budget import render_for as previous_render
from novel_writer.generation.longform_prompts import parse_stage as previous_parse
from novel_writer.generation.novel import role_for
from novel_writer.generation.schemas import (
    AutomatedStageComparison,
    AutomatedStagePlan,
    GenerationSpec,
    StageComparison,
    StagePlan,
)

INSTRUCTION = """本阶段已授权在有限动作与预算内自动衔接至作者审核。
普通人物选择、如何解释线索、何时回应、后续悬念，由 Chief 设计、Writer 在有效计划内展开。
story_questions 只保存故事内部悬念，不要求作者作答，也不代表已发生事实或待办。
questions 仅放确需作者决定的明确边界冲突或缺少的正式事实，不能用它转交普通创作工作。
author_question_reasons 必须逐项给出 kind、正式资料或作者原话的 source，
以及为何本次无法继续的 why_blocked。
没有作者问题时 questions=[]、question_scopes={}、author_question_reasons={}。
没有悬念时 story_questions=[]。
能在题材与作者边界内通过设计解决的阻力，应在场面中给出处理方式并自动推进，不停下来问作者。
Chief 检查点发现题材发展不足时，使用 revise 修正未写设计，保留已写事实与原题材目标；
只有真实硬冲突或无法可靠继续时才 pause，并说明原因。不得用自动化跨越作者、人物或世界规则边界。
Writer 接续有效计划和完整事实接力；剧情悬念交给事件与人物回应展开，不等待作者逐一决定。"""


def enabled(spec: GenerationSpec) -> bool:
    return spec.stage_mode == "longform-v1" and spec.automation_policy == "stage-auto-v1"


def contract_for(spec: GenerationSpec) -> str:
    base = previous_contract(spec)
    if not enabled(spec):
        return base
    return fingerprint(
        {
            "base": base,
            "automation": inspect.getsource(sys.modules[__name__]),
            "plan": AutomatedStagePlan.model_json_schema(),
            "comparison": AutomatedStageComparison.model_json_schema(),
        }
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
    system, user = previous_render(spec, snapshot, action, plan, body, author_note, reports)
    role = role_for(action)
    if not enabled(spec) or role not in {"chief", "writer"}:
        return system, user
    payload = parse_object(user)
    if role == "chief":
        payload["output_schema"] = (
            AutomatedStagePlan if action == "plan" else AutomatedStageComparison
        ).model_json_schema()
    payload["execution_policy"] = "stage-auto-v1"
    return system + "\n" + INSTRUCTION, json_text(payload)


def parse_stage(raw: str, spec: GenerationSpec, snapshot: dict[str, Any]) -> StagePlan:
    if not enabled(spec):
        return previous_parse(raw, spec, snapshot)
    parsed = AutomatedStagePlan.model_validate(parse_object(raw))
    previous_parse(
        json_text(
            parsed.model_dump(mode="json", exclude={"story_questions", "author_question_reasons"})
        ),
        spec,
        snapshot,
    )
    return parsed


def parse_comparison(raw: dict[str, Any], spec: GenerationSpec) -> StageComparison:
    return (AutomatedStageComparison if enabled(spec) else StageComparison).model_validate(raw)
