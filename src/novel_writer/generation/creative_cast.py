"""Stage-local character proposals, never an alternate formal state."""

from copy import deepcopy
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field

from novel_writer.generation import craft_plans
from novel_writer.generation.content import json_text
from novel_writer.generation.craft_models import CraftPlan, CraftPlanEdit
from novel_writer.generation.plan_format import parse_plan_object
from novel_writer.generation.schemas import (
    AutomatedStagePlan,
    BackgroundPlan,
    GenerationSpec,
    NovelStoryPlan,
    StagePlan,
    StoryPlan,
    StrictModel,
)

KEY = "creative_autonomy_contract"
MAX_NEW = 3


class CharacterProposal(StrictModel):
    id: UUID
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=1600)
    independent_goal: str = Field(min_length=1, max_length=600)
    voice: str = Field(default="", max_length=600)
    entry_reason: str = Field(min_length=1, max_length=800)


class CreativePlan(CraftPlan):
    new_characters: list[CharacterProposal] = Field(default_factory=list, max_length=MAX_NEW)
    creative_notes: list[str] = Field(default_factory=list, max_length=16)


class CreativePlanEdit(CraftPlanEdit):
    plan: (
        StoryPlan | NovelStoryPlan | StagePlan | AutomatedStagePlan
        | BackgroundPlan | CraftPlan | CreativePlan
    )


def reserve(snapshot: dict[str, Any], state: dict[str, Any]) -> None:
    snapshot["new_character_slots"] = [str(uuid4()) for _ in range(MAX_NEW)]
    # Local validation only: never send the full name registry to a role.
    snapshot["character_identity_registry"] = [
        {"id": c["id"], "names": [c["name"], *c.get("aliases", [])]}
        for c in state.get("characters", [])
    ]


def is_gap(question: dict[str, Any]) -> bool:
    return (question.get("reason") or {}).get("kind") == "missing_canonical_fact"


def parse_stage(
    raw: str, spec: GenerationSpec, snapshot: dict[str, Any],
) -> StagePlan | BackgroundPlan:
    if not snapshot.get(KEY):
        return craft_plans.parse_stage(raw, spec, snapshot)
    from novel_writer.generation.creative_contract import checked

    checked(snapshot)
    value = parse_plan_object(raw)
    proposed = value.get("new_characters", [])
    if not isinstance(proposed, list) or len(proposed) > MAX_NEW:
        raise ValueError("每阶段最多新增三位人物，new_characters 须为列表")
    proposals = [CharacterProposal.model_validate(p).model_dump(mode="json") for p in proposed]
    ids = {p["id"] for p in proposals}
    if len(ids) != len(proposals) or not ids <= set(snapshot.get("new_character_slots", [])):
        raise ValueError("新人物必须使用本阶段预留的唯一人物 ID")
    names = {str(n).strip().casefold() for c in snapshot.get("character_identity_registry", [])
             for n in c["names"]}
    for proposal in proposals:
        name = proposal["name"].strip().casefold()
        if not name or name in names:
            raise ValueError("新人物姓名不能为空或与已有姓名、别名重复；请沿用原人物或明确区分")
        names.add(name)
    local = deepcopy(snapshot)
    selection = local.setdefault("cast_selection", {
        "characters": [{"id": i} for i in spec.character_ids],
        "required_ids": list(spec.character_ids),
    })
    selection["characters"] += [{"id": i} for i in ids]
    base = {k: v for k, v in value.items() if k not in {"new_characters", "creative_notes"}}
    parsed = craft_plans.parse_stage(json_text(base), spec, local)
    plan = CreativePlan.model_validate({
        **parsed.model_dump(mode="json"), "new_characters": proposals,
        "creative_notes": value.get("creative_notes", []),
    })
    used = {i for s in plan.scenes for i in s.character_ids}
    if not ids <= used:
        raise ValueError("新增人物须参与当前计划，未使用的人物无需创建")
    # Keep the full question/source as design uncertainty, not a blocker or story fact.
    gaps = {q for q, reason in plan.author_question_reasons.items()
            if reason.kind == "missing_canonical_fact"}
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
    raw: str, spec: GenerationSpec, snapshot: dict[str, Any],
) -> NovelStoryPlan | BackgroundPlan:
    if not snapshot.get(KEY):
        return craft_plans.parse_plan(raw, spec, snapshot)
    plan = parse_stage(raw, spec, snapshot)
    if not 2 <= len(plan.scenes) <= 4:
        raise ValueError("单单元计划须包含二至四个场面")
    assert isinstance(plan, BackgroundPlan)
    return plan


def reserved_ids(snapshot: dict[str, Any], plan: dict[str, Any] | None) -> frozenset[str]:
    if not snapshot.get(KEY):
        return frozenset()
    return frozenset(p["id"] for p in (plan or {}).get("new_characters", [])) & frozenset(
        snapshot.get("new_character_slots", [])
    )


def protect_proposals(prior: dict[str, Any], replacement: dict[str, Any], written: int) -> None:
    # Once writing starts, a proposal's identity cannot be silently repurposed.
    if not written:
        return
    after = {p["id"]: p for p in replacement.get("new_characters", [])}
    for p in prior.get("new_characters", []):
        if after.get(p["id"]) != p:
            raise ValueError("写作开始后保留已有候选人物身份，不能通过修改后续计划覆盖")
