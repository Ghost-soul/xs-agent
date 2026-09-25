"""Plot-led narrative guidance layered over frozen context and role contracts."""

from __future__ import annotations

import inspect
import json
import sys
from typing import Any

from novel_writer.generation import chief_context
from novel_writer.generation.content import fingerprint, parse_object
from novel_writer.generation.novel import role_for
from novel_writer.generation.schemas import GenerationSpec

POLICY = "plot-led-v2"

CHIEF = """【核心创作任务：让选定叙事卡主导本阶段情节】
在作者明确要求、边界和正式事实内，全部选定叙事卡共同决定本阶段主要讲什么故事。
先从卡的核心内容确定主线、核心矛盾或主要互动，再选择能使它们发生的人物、行动、转折与结果。
人物当前处境和历史线索用于建立这条主线的可信起因；世界背景、日常事务和支线服务它的展开。
不能先安排一条通用冒险或处理旧事务的主线，再把叙事卡作为称呼、气氛、背景或点缀贴上去。

每张选定卡都要实质改变本阶段的重要事件、人物选择或后果。多卡共同作用于同一条因果链：
一张卡建立的处境改变另一张卡中的行动条件，人物的回应再推动后续变化；卡片顺序不代表主次。
每个单元都推进这条主线的一步，或展开前一步已经产生的直接后果。关键内容在本阶段实际展开，
不能把全部兑现推到未来；可只完成可信的一步，不强求卡内终局、固定关系进度或危险升级。
保留人物各自目标与自主回应，按经历建立关系和身份变化；不能为用卡推翻既有事实或跳过适用前提。

把叙事卡的故事作用直接落实到 chapter_goal、bridge、event、choice_and_response、consequence
和 major_turn，让 Writer 仅凭有效计划就能写出这些内容，不把关键情节藏进 world_context。
提交前自行推演：如果去掉任意一张选定卡，主要事件、选择和后果仍基本不变，说明它还没有进入
主线，应在设计中重新建立它的因果作用。此推演只用于创作，不输出评分或逐卡验收表。
不按卡数分配单元，不要求每个场景同时表现所有卡，不照抄卡内示例或把未来设计当作事实。
没有选定叙事卡时按作者方向设计，不自行补卡。历史检查点仅调整未写部分，保留已写事件及作者修改。
以下角色合同继续约束人物范围、来源、单元容量和输出格式。"""

WRITER = """【核心创作任务：写出由叙事卡主导的情节推进】
当前有效计划已经把选定叙事卡转成具体事件链。把 plot_execution.current_task 的事件、选择、
回应与后果作为本单元的创作中心，让主要场景围绕它们展开，并在正文中发生可感知的变化。
先理解本次事件如何推进阶段主线，再安排动作、对话、细节与节奏；资料说明、日常活动与过渡
为这次变化服务，不用无关的新危机、通用冒险或泛泛的关系暗示替代计划的关键情节。
重要行动和回应要让读者亲历，写清谁作出什么选择、对方怎样回应、局面因此如何改变，
不能只在末尾宣告结果或把关键过程推到下一单元。安静互动、犹疑与失败也可以切实改变后续条件。
接住已经发生的后果，不重演已完成事件，不把已确认的变化退回模糊可能。
选卡名称只标明创作方向，具体任务以当前有效计划和作者修改为准；不凭卡名新增身份、
强行配对、改变事实或扩写后续单元。保留人物声音、自主回应与场景表达空间。
只完成当前单元，不因强调叙事卡增加字数目标、逐卡打卡、自评或额外输出。
以下角色合同继续约束事实来源、授权范围和交付。"""

CHIEF_MANDATE = (
    "以下全部叙事卡是本阶段主线的共同设计要求：由它们决定主要事件、核心互动、关键选择与后果，"
    "再从人物处境与正式事实建立因果。每张卡都应实质改变主线；将它们组合为相互推动的事件链，"
    "通过现有剧情字段交给 Writer，在本阶段展开可信的一步。作者明确要求、边界和正式事实优先。"
    "未选卡时只按作者方向设计。"
)
WRITER_MANDATE = (
    "将当前任务中由叙事卡设计的事件、人物选择、自主回应和直接后果写成主要场景；"
    "按有效计划推进主线，接续已有后果。卡名是方向提示，不能代替具体计划或扩大本单元范围。"
)
FIELD_GUIDANCE = {
    "chapter_goal": "由全部选定叙事内容共同推动的主线及本阶段实际要发生的变化，具体到人物和处境。",
    "bridge": "当前处境、已写事件及未完约定如何引出这条叙事主线；旧事务作为因果起点。",
    "event": "本单元如何推进叙事主线：谁在何种处境下因何行动，触发什么具体事件。",
    "choice_and_response": "叙事处境促使人物作出的关键选择与对方的自主回应，写清相互影响。",
    "consequence": "本次选择与回应使关系、身份、认知、资源或局势怎样改变，如何推动主线下一步。",
    "major_turn": "选定叙事内容通过哪次关键选择、回应或揭示改变主线方向，产生什么转折。",
    "world_context": (
        "Writer 理解本阶段所需的世界条件；关键叙事情节放入事件字段，背景不是主线任务。"
    ),
}


def enabled(spec: GenerationSpec) -> bool:
    return (
        spec.narrative_policy == POLICY
        and spec.workflow == "novel-run-v1"
        and spec.writing_policy == "guided-v1"
        and spec.card_selection_policy == "separate-v1"
    )


def previous_spec(spec: GenerationSpec) -> GenerationSpec:
    # Keep legacy selection modes usable for independent revisions. Those modes
    # have no separate narrative cards and retain their existing role instructions.
    return (
        spec.model_copy(update={"narrative_policy": "causal-v1"})
        if spec.narrative_policy == POLICY
        else spec
    )


def contract_for(spec: GenerationSpec) -> str:
    base = chief_context.contract_for(previous_spec(spec))
    return (
        fingerprint({"base": base, "narrative_drive": inspect.getsource(sys.modules[__name__])})
        if enabled(spec)
        else base
    )


def amendment_contract(spec: GenerationSpec) -> str:
    return (
        contract_for(spec)
        if enabled(spec)
        else chief_context.amendment_contract(previous_spec(spec))
    )


def describe_fields(schema: dict[str, Any]) -> None:
    for name, field in schema.get("properties", {}).items():
        if name in FIELD_GUIDANCE:
            field["description"] = FIELD_GUIDANCE[name]
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
    system, raw = chief_context.render_for(
        previous_spec(spec), snapshot, action, plan, body, author_note, reports
    )
    role = role_for(action)
    if not enabled(spec) or role not in {"chief", "writer"} or action in {"title", "rewrite"}:
        return system, raw
    payload = parse_object(raw)
    if role == "chief":
        design = payload.pop("narrative_design")
        design = {"plot_mandate": CHIEF_MANDATE, **design}
        task = payload.pop("story_task")
        # Full cards precede cast, background and history; do not summarize or edit them.
        payload = {"story_task": task, "narrative_design": design, **payload}
        describe_fields(payload["output_schema"])
        system = CHIEF + "\n\n" + system
    else:
        cards = {card["id"]: card for card in snapshot.get("cards", [])}
        execution = payload["plot_execution"]
        payload["plot_execution"] = {
            "narrative_mandate": WRITER_MANDATE,
            "selected_narratives": [
                {"id": card_id, "name": cards[card_id]["name"]}
                for card_id in spec.narrative_card_ids
            ],
            **execution,
        }
        system = WRITER + "\n\n" + system
    return system, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
