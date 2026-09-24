"""New longform contract; the existing single-unit contracts remain frozen."""

from __future__ import annotations

import inspect
import sys
from typing import Any

from novel_writer.generation.casting import CAST_INSTRUCTION
from novel_writer.generation.casting import contract_for as old_contract
from novel_writer.generation.casting import render_for as old_render
from novel_writer.generation.content import fingerprint, json_text, paragraphs, parse_object
from novel_writer.generation.novel import role_for
from novel_writer.generation.reports import MemoryReport
from novel_writer.generation.roles import render_for as report_render
from novel_writer.generation.schemas import GenerationSpec, StageComparison, StagePlan

CHIEF = """你是题材主导的阶段 Chief。完整主导卡与作者方向决定将要发生什么。
先选题材事件、承载人物、选择、自主回应与后果，再设计从既成事实到新发展的可信衔接。
旧事务是事实与压力素材，不自动占用本阶段篇幅。紧迫后果、作者边界、世界规则、人物自主性仍须尊重。
规划整阶段题材份额至少70%，建议75%；每个单元可以承担不同作用，不强迫每章同样占比。
scenes 按叙事单元排列，数量严格等于 unit_limit，单元不是章节。百分比在全阶段合计100%。
原目标与已写单元不可改写；检查点只完整替换未写后缀。评价实际正文中的事件和回应，不以题材词频、提及或照顾冒充关系发展。
百合需要女性之间具有主体性的感受、选择和关系后果；不得自动决定最终CP或跨越作者关系边界。
首单元必须启动题材，首章必须有可辨认的题材变化，后续章承接后果；不得把题材全部推到最后。
检查点明确 decision 和 genre_progress，首章未变化时调整未写设计或暂停解释。
不用全阶段平均比例掩盖缺失。
问题区分 current_unit 与 later；未来提案只是假设，不是事实。仅返回给定 schema 的 JSON。"""

WRITER = """你是题材主导 Writer。完整题材卡和唯一有效阶段计划决定本次事件；只写指定叙事单元。
展开人物在现场的感受、选择、阻力、自主回应及实际后果，让主导题材推动变化。旧事件不自动成为待办。
遵守作者、世界和人物边界，候选接力是尚未正式采用的连续性材料；不得把未来计划写成既成事实。
续接已写正文，不重复复述，不自行写完后续单元。单元不是章节，不加标题。只返回正文。
遇到无法遵守的硬边界，只返回 JSON generation_blocked 及具体冲突，不能伪造正文。"""


def contract_for(spec: GenerationSpec) -> str:
    if spec.stage_mode != "longform-v1":
        return old_contract(spec)
    return fingerprint(
        {
            "base": old_contract(spec),
            "source": inspect.getsource(sys.modules[__name__]),
            "plan": StagePlan.model_json_schema(),
            "comparison": StageComparison.model_json_schema(),
        }
    )


def parse_stage(raw: str, spec: GenerationSpec, snapshot: dict[str, Any]) -> StagePlan:
    plan = StagePlan.model_validate(parse_object(raw))
    if len(plan.scenes) != spec.unit_limit:
        raise ValueError("Chief 单元设计数量与冻结上限不符")
    selection = snapshot.get("cast_selection")
    allowed = {c["id"] for c in selection["characters"]} if selection else set(spec.character_ids)
    chosen = {c for scene in plan.scenes for c in scene.character_ids}
    required = set(selection["required_ids"] if selection else spec.character_ids)
    if not chosen <= allowed or not required <= chosen or len(chosen) > 12:
        raise ValueError("Chief 承载人物越界、超过12人或遗漏固定人物")
    if (
        spec.relationship_scope == "explore"
        and len(chosen & (set(spec.relationship_character_ids) or chosen)) < 2
    ):
        raise ValueError("关系探索需在允许范围选出至少两位人物")
    return plan


def render_for(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    plan: dict[str, Any] | None = None,
    body: str | None = None,
    author_note: str | None = None,
    reports: dict[str, Any] | None = None,
) -> tuple[str, str]:
    if spec.stage_mode != "longform-v1":
        return old_render(spec, snapshot, action, plan, body, author_note, reports)
    reports = reports or {}
    role = role_for(action)
    if reports.get("memory"):
        memory = reports["memory"]
        reports = {
            **reports,
            "memory": {
                **{k: memory.get(k) for k in ("position", "outcome", "status", "unresolved")},
                "changes": [
                    {
                        k: v
                        for k, v in c.items()
                        if k
                        in (
                            {"collection", "object_id", "observation", "paragraph_ids", "value"}
                            if role == "checker"
                            else {"collection", "object_id", "observation"}
                        )
                    }
                    for c in memory.get("changes", [])
                ],
            },
        }
    context = snapshot["context"]
    # Reader is an allowlist: even the longform control data stays out of its input.
    if role == "reader":
        return report_render(
            spec,
            {"context": {"recent_chapters": context.get("recent_chapters", [])}},
            "reader",
            None,
            body,
            None,
            {},
        )
    if action == "title":
        return (
            '你只为每章提供标题，返回JSON {"chapters":[{"id":"给定ID","titles":["标题"]}]}。'
            "不修改正文。",
            json_text({"chapters": reports.get("chapter_bodies", [])}),
        )
    chosen = {i for scene in (plan or {}).get("scenes", []) for i in scene["character_ids"]}
    local = {**context, **reports.get("working_context", {})}
    if plan:
        local["characters"] = [c for c in local["characters"] if c["id"] in chosen]
    elif snapshot.get("cast_selection"):
        local["characters"] = [
            {
                **{
                    k: c.get(k)
                    for k in (
                        "id",
                        "name",
                        "aliases",
                        "description",
                        "current_state",
                        "location_id",
                        "forbidden_behaviors",
                    )
                },
                "portrayal_profile": {
                    k: c.get("portrayal_profile", {}).get(k)
                    for k in ("independent_goal", "value_boundary", "unique_competence")
                },
            }
            for c in local["characters"]
        ]
    if role in {"memory", "checker", "editor"}:
        mapped = "memory" if role == "memory" else "checker" if role == "checker" else "editor"
        system, user = report_render(
            spec,
            {
                **snapshot,
                "context": local,
                "candidate_chapter": reports.get("unit_chapter", snapshot.get("candidate_chapter")),
            },
            mapped,
            None,
            body,
            author_note,
            reports,
        )
        if reports.get("working_context") and role in {"memory", "checker"}:
            payload = parse_object(user)
            payload["candidate_working_reference"] = payload.pop("formal_facts")
            payload["reference_boundary"] = "正式起点叠加已完成单元的候选接力，尚未正式采用"
            user = json_text(payload)
        if role == "memory" and action.startswith("memory:"):
            payload = parse_object(user)
            schema = MemoryReport.model_json_schema()
            schema["properties"].update(
                {
                    "stage_complete": {
                        "type": "boolean",
                        "description": "阶段目标已经完成，可提前停止续写",
                    },
                    "correction_needed": {"type": "boolean", "description": "需Chief调整未写设计"},
                    "continuity_blocked": {
                        "type": "boolean",
                        "description": "存在不能可靠接力的事实冲突",
                    },
                    "progress_reason": {"type": "string"},
                }
            )
            schema["required"] += [
                "stage_complete",
                "correction_needed",
                "continuity_blocked",
                "progress_reason",
            ]
            payload["output_schema"] = schema
            payload["stage_goal_not_fact"] = (plan or {}).get("chapter_goal")
            return (
                system + "\n只提取本单元事实。报告阶段进展和事实接力风险，题材意图不是事实。",
                json_text(payload),
            )
        return system, user
    payload = {
        "author_direction": spec.direction,
        "author_boundaries": spec.author_boundaries,
        "viewpoint": spec.viewpoint,
        "relationship_scope": spec.relationship_scope,
        "relationship_character_ids": spec.relationship_character_ids,
        "cards": snapshot["cards"],
        "focus_card_id": spec.focus_card_id,
        "formal_reference": local,
        "author_decisions": author_note,
        "unit_limit": spec.unit_limit,
        "estimated_chapters": spec.chapter_count,
        "total_target_characters": spec.chapter_count * spec.target_characters,
    }
    if role == "chief":
        payload["macro_diagnostic"] = snapshot.get("macro_diagnostic")
        payload["future_proposal_not_fact"] = snapshot.get("previous_proposal")
        payload["output_schema"] = StagePlan.model_json_schema()
        if action != "plan":
            payload.update(
                {
                    "written_candidate": [
                        {"id": p["id"], "text": p["text"]} for p in paragraphs(body or "")
                    ],
                    "current_plan": plan,
                    "completed_units": reports.get("completed_units", 0),
                    "memory_observations": reports.get("memory"),
                    "early_reader": reports.get("early_review"),
                    "output_schema": StageComparison.model_json_schema(),
                }
            )
        return CHIEF + (
            "\n" + CAST_INSTRUCTION if snapshot.get("cast_selection") else ""
        ), json_text(payload)
    ordinal = int(action.split(":")[1]) if action.startswith("write:") else 1
    payload.update(
        {
            "already_written": body,
            "candidate_handoff": reports.get("memory"),
            "effective_plan": plan,
            "current_unit": ordinal,
            "unit_target_characters": round(
                spec.chapter_count * spec.target_characters / spec.unit_limit
            ),
            "current_task": (plan or {}).get("scenes", [])[ordinal - 1] if plan else None,
        }
    )
    if action == "rewrite":
        payload["current_task"] = "作者独立授权改写整阶段正文，按阶段原目标和明确修改要求重写"
        payload["unit_target_characters"] = spec.chapter_count * spec.target_characters
        return (
            WRITER + "\n本次是作者独立授权的整阶段改写；current_task 替代普通单元范围。",
            json_text(payload),
        )
    return WRITER, json_text(payload)
