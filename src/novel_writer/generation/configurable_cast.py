"""Editable character limits; historical creative-autonomy-v1 parsing stays frozen."""

from copy import deepcopy
from typing import Any
from uuid import uuid4

from pydantic import Field

from novel_writer.generation import craft_plans
from novel_writer.generation import creative_cast as previous
from novel_writer.generation.content import json_text
from novel_writer.generation.craft_models import CraftPlan, CraftPlanEdit
from novel_writer.generation.creative_cast import (  # noqa: F401
    CharacterProposal,
    is_gap,
    protect_proposals,
    reserved_ids,
)
from novel_writer.generation.editable_rules import limit
from novel_writer.generation.plan_format import parse_plan_object
from novel_writer.generation.schemas import (
    AutomatedStagePlan,
    BackgroundPlan,
    GenerationSpec,
    NovelStoryPlan,
    StagePlan,
    StoryPlan,
)

KEY = "prompt_rules_contract"
MAX_NEW = 12


class CreativePlan(CraftPlan):
    new_characters: list[CharacterProposal] = Field(default_factory=list, max_length=MAX_NEW)
    creative_notes: list[str] = Field(default_factory=list, max_length=16)


class CreativePlanEdit(CraftPlanEdit):
    plan: (
        StoryPlan
        | NovelStoryPlan
        | StagePlan
        | AutomatedStagePlan
        | BackgroundPlan
        | CraftPlan
        | CreativePlan
    )


def reserve(snapshot: dict[str, Any], state: dict[str, Any]) -> None:
    maximum = limit(snapshot.get("prompt_templates"))
    snapshot["new_character_limit"] = maximum
    existing = snapshot.get("new_character_slots", [])
    snapshot["new_character_slots"] = (existing + [str(uuid4()) for _ in range(maximum)])[:maximum]
    # Local validation only: never send the full name registry to a role.
    snapshot["character_identity_registry"] = [
        {"id": c["id"], "names": [c["name"], *c.get("aliases", [])]}
        for c in state.get("characters", [])
    ]


def parse_stage(
    raw: str,
    spec: GenerationSpec,
    snapshot: dict[str, Any],
) -> StagePlan | BackgroundPlan:
    if not snapshot.get(KEY):
        return previous.parse_stage(raw, spec, snapshot)
    from novel_writer.generation.editable_contract import checked

    checked(snapshot)
    value = parse_plan_object(raw)
    proposed = value.get("new_characters", [])
    maximum = snapshot["new_character_limit"]
    if not isinstance(proposed, list) or len(proposed) > maximum:
        raise ValueError(f"本阶段最多新增 {maximum} 位人物，new_characters 须为列表")
    proposals = [CharacterProposal.model_validate(p).model_dump(mode="json") for p in proposed]
    ids = {p["id"] for p in proposals}
    if len(ids) != len(proposals) or not ids <= set(snapshot.get("new_character_slots", [])):
        raise ValueError("新人物必须使用本阶段预留的唯一人物 ID")
    names = {
        str(n).strip().casefold()
        for c in snapshot.get("character_identity_registry", [])
        for n in c["names"]
    }
    for proposal in proposals:
        name = proposal["name"].strip().casefold()
        if not name or name in names:
            raise ValueError("新人物姓名不能为空或与已有姓名、别名重复；请沿用原人物或明确区分")
        names.add(name)
    local = deepcopy(snapshot)
    selection = local.setdefault(
        "cast_selection",
        {
            "characters": [{"id": i} for i in spec.character_ids],
            "required_ids": list(spec.character_ids),
        },
    )
    selection["characters"] += [{"id": i} for i in ids]
    base = {k: v for k, v in value.items() if k not in {"new_characters", "creative_notes"}}
    parsed = craft_plans.parse_stage(json_text(base), spec, local)
    plan = CreativePlan.model_validate(
        {
            **parsed.model_dump(mode="json"),
            "new_characters": proposals,
            "creative_notes": value.get("creative_notes", []),
        }
    )
    used = {i for s in plan.scenes for i in s.character_ids}
    if not ids <= used:
        raise ValueError("新增人物须参与当前计划，未使用的人物无需创建")
    # Keep the full question/source as design uncertainty, not a blocker or story fact.
    gaps = {
        q
        for q, reason in plan.author_question_reasons.items()
        if reason.kind == "missing_canonical_fact"
    }
    for q in plan.questions:
        if q in gaps:
            reason = plan.author_question_reasons[q]
            note = f"{q}\n原报告来源：{reason.source}\n原说明：{reason.why_blocked}"
            if note not in plan.creative_notes:
                if len(plan.creative_notes) < 16:
                    plan.creative_notes.append(note)
                else:
                    # Preserve all source text without exceeding the bounded notes list.
                    plan.creative_notes[-1] += "\n\n" + note
    plan.questions = [q for q in plan.questions if q not in gaps]
    plan.question_scopes = {q: s for q, s in plan.question_scopes.items() if q not in gaps}
    plan.author_question_reasons = {
        q: r for q, r in plan.author_question_reasons.items() if q not in gaps
    }
    return CreativePlan.model_validate(plan.model_dump(mode="json"))


def parse_plan(
    raw: str,
    spec: GenerationSpec,
    snapshot: dict[str, Any],
) -> NovelStoryPlan | BackgroundPlan:
    if not snapshot.get(KEY):
        return previous.parse_plan(raw, spec, snapshot)
    plan = parse_stage(raw, spec, snapshot)
    if not 2 <= len(plan.scenes) <= 4:
        raise ValueError("单单元计划须包含二至四个场面")
    assert isinstance(plan, BackgroundPlan)
    return plan
