"""Bounded character recall and Chief selection within an existing paid planning slot."""

from __future__ import annotations

import inspect
import re
import sys
from typing import Any

from novel_writer.generation.content import fingerprint, json_text, parse_novel_plan
from novel_writer.generation.context import terms
from novel_writer.generation.roles import contract_for as base_contract
from novel_writer.generation.roles import render_for as base_render
from novel_writer.generation.schemas import GenerationSpec, NovelStoryPlan
from novel_writer.services.errors import WorkflowError

MAX_CANDIDATES = 24
MAX_CAST = 12

CAST_INSTRUCTION = """本次作者授权你自动选角，不要求作者先逐个勾选人物。
先依据完整主导题材和作者方向设计未来事件，再从冻结候选人物中选出能承担选择、阻力、自主回应和后果的人。
当前在场或最近出场只是连续性依据，不自动成为主角；允许合理换场引入候选人物，但必须解释衔接，不能瞬移。
候选排序是本地召回，不是人物重要性或题材适配的判断；逐项比较身份、目标、关系与自主性。
人物资料是正式事实；不可根据名字猜性别、年龄、性向或默认配对。资料不足时保留问题及其影响范围。
各场面的 character_ids 明确列出实际承载人物，整章去重后最多12人，包含固定出场人物。
无关人物不必出场。
关系探索范围未指定对象时，可在候选中选择探索人物，但不得将选角当作最终CP或已成立关系。
作者指定双方与关系限制不得替换。人物需要超出冻结候选时提出当前问题，不擅自新增身份或扩大范围。
选角理由写入场面事件、选择回应及后果，让作者能看出为什么是这些人。"""


def automatic(spec: GenerationSpec) -> bool:
    return spec.character_selection == "chief-auto-v1"


def mentioned(character: dict[str, Any], text: str) -> bool:
    for name in [character["name"], *character.get("aliases", [])]:
        # Latin aliases need word boundaries; Chinese names use exact text matches.
        pattern = re.escape(name)
        if name.isascii():
            pattern = rf"(?<!\w){pattern}(?!\w)"
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def prepare_cast(spec: GenerationSpec, state: dict[str, Any]) -> dict[str, Any]:
    all_characters = state["characters"]
    active = {c["id"]: c for c in all_characters if c.get("library_status", "active") == "active"}
    required = list(
        dict.fromkeys(
            [
                *spec.character_ids,
                *(
                    spec.relationship_character_ids
                    if spec.relationship_scope == "specified_pair"
                    else []
                ),
            ]
        )
    )
    guaranteed = set(required) | set(spec.relationship_character_ids)
    if len(required) > MAX_CAST:
        raise WorkflowError("固定人物和关系对象合计不能超过12人")
    if not guaranteed <= active.keys():
        raise WorkflowError("自动选角的固定人物或关系对象不存在或已退役，请核对故事资料")
    if not active:
        raise WorkflowError("没有可选的正式人物，请先在故事资料中添加人物")
    if spec.relationship_scope == "explore" and len(active) < 2:
        raise WorkflowError("关系探索至少需要两位可用正式人物；自动选角不会新增人物")
    position = state["narrative_position"]
    current = set(position.get("current_characters", []))
    recent = {p for e in state.get("events", [])[-12:] for p in e.get("participants", [])}
    intent = spec.direction + " " + spec.viewpoint
    anchors = guaranteed | {
        i
        for i, c in active.items()
        if mentioned(c, intent)
        or i in current
        or c["name"] in current
        or set(c.get("aliases", [])) & current
    }
    linked = {
        i
        for r in state.get("relationships", [])
        if {r["source_character_id"], r["target_character_id"]} & anchors
        for i in (r["source_character_id"], r["target_character_id"])
    }
    query = terms(intent + " " + json_text(position))
    ranked = []
    for index, (identifier, c) in enumerate(active.items()):
        explicit = mentioned(c, intent)
        present = bool({identifier, c["name"], *c.get("aliases", [])} & current)
        relevance = len(
            query
            & terms(
                json_text(
                    {
                        k: c.get(k)
                        for k in (
                            "description",
                            "current_state",
                            "portrayal_profile",
                        )
                    }
                )
            )
        )
        reasons = [
            reason
            for enabled, reason in (
                (identifier in guaranteed, "作者固定人物或关系对象"),
                (explicit, "方向或视角提及"),
                (present, "当前现场"),
                (identifier in linked, "与相关人物有正式关系"),
                (identifier in recent, "近期事件参与者"),
                (relevance > 0, "剧情文字相关"),
            )
            if enabled
        ]
        rank = (
            identifier in guaranteed,
            explicit,
            present,
            identifier in linked,
            relevance,
            identifier in recent,
            -index,
        )
        ranked.append((rank, c, reasons or ["正式人物候选"]))
    ranked.sort(key=lambda item: item[0], reverse=True)
    selected = ranked[:MAX_CANDIDATES]
    ids = {c["id"] for _, c, _ in selected}
    return {
        "policy": "chief-auto-v1",
        "characters": [c for _, c, _ in selected],
        "candidates": [
            {"id": c["id"], "name": c["name"], "reasons": reasons} for _, c, reasons in selected
        ],
        "required_ids": required,
        "omitted_ids": [c["id"] for c in all_characters if c["id"] not in ids],
        "source_sha256": fingerprint(all_characters),
        "recall_complete": len(ids) == len(active),
        "selection_is_final_cast": False,
    }


def selected_plan(raw: str, spec: GenerationSpec, snapshot: dict[str, Any]) -> NovelStoryPlan:
    allowed = spec.character_ids
    if automatic(spec):
        allowed = [c["id"] for c in snapshot["cast_selection"]["characters"]]
    plan = parse_novel_plan(raw, allowed)
    if automatic(spec):
        chosen = {i for scene in plan.scenes for i in scene.character_ids}
        if len(chosen) > MAX_CAST:
            raise ValueError("Chief 整章承载人物超过12人，请收束场面")
        if not set(snapshot["cast_selection"]["required_ids"]) <= chosen:
            raise ValueError("Chief 遗漏作者固定人物或关系对象")
        if spec.relationship_scope == "explore" and len(chosen) < 2:
            raise ValueError("关系探索不能仅选择一位人物")
        if (
            spec.relationship_scope == "explore"
            and spec.relationship_character_ids
            and len(chosen & set(spec.relationship_character_ids)) < 2
        ):
            raise ValueError("Chief 未在作者允许的关系范围内选出至少两位人物")
    return plan


def contract_for(spec: GenerationSpec) -> str:
    original = base_contract(spec)
    if not automatic(spec):
        return original
    return fingerprint({"base": original, "casting": inspect.getsource(sys.modules[__name__])})


def render_for(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    plan: dict[str, Any] | None = None,
    body: str | None = None,
    author_note: str | None = None,
    reports: dict[str, Any] | None = None,
) -> tuple[str, str]:
    if not automatic(spec) or action in {"reader", "reader_amend", "title"}:
        return base_render(spec, snapshot, action, plan, body, author_note, reports)
    selection = snapshot["cast_selection"]
    if action == "plan":
        ids = [c["id"] for c in selection["characters"]]
    else:
        if plan is None:
            raise WorkflowError("自动选角需要当前有效 Chief 方案")
        validated = selected_plan(json_text(plan), spec, snapshot)
        ids = list(dict.fromkeys(i for scene in validated.scenes for i in scene.character_ids))
    characters = [c for c in selection["characters"] if c["id"] in ids]
    if action == "plan":
        # Whole selected fields, no fabricated summaries or clipped identity boundaries.
        characters = [
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
                    for k in (
                        "independent_goal",
                        "value_boundary",
                        "unique_competence",
                    )
                },
            }
            for c in characters
        ]
    context = {**snapshot["context"], "characters": characters}
    context["beliefs"] = [b for b in context.get("beliefs", []) if b["character_id"] in ids]
    context["relationships"] = [
        r
        for r in context.get("relationships", [])
        if {r["source_character_id"], r["target_character_id"]} & set(ids)
    ]
    context["preflight"] = {
        **context.get("preflight", {}),
        "required_character_ids": selection["required_ids"] if action == "plan" else ids,
    }
    context["cast_scope"] = {
        "mode": "candidate_pool" if action == "plan" else "selected_by_current_plan",
        "allowed_ids": ids,
        "required_ids": selection["required_ids"],
        "omitted_characters_are_not_absent": True,
    }
    relationships = spec.relationship_character_ids
    if spec.relationship_scope == "explore" and not relationships:
        relationships = ids
    effective = spec.model_copy(
        update={"character_ids": ids, "relationship_character_ids": relationships}
    )
    system, user = base_render(
        effective, {**snapshot, "context": context}, action, plan, body, author_note, reports
    )
    if action == "plan":
        system += "\n" + CAST_INSTRUCTION
    elif action in {"write", "rewrite"}:
        system += "\n本章人物范围已由有效方案选定；展开这些人物的事件，不新增范围外承载者。"
    return system, user
