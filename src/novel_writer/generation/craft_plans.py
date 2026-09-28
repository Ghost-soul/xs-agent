"""Parse readable new plans separately from the author's preferred unit count."""

from typing import Any

from novel_writer.generation import guidance, plan_capacity
from novel_writer.generation.content import json_text
from novel_writer.generation.craft_models import CraftPlan, enabled
from novel_writer.generation.plan_format import (
    normalize_optional_text,
    normalize_questions,
    parse_plan_object,
)
from novel_writer.generation.schemas import (
    BackgroundPlan,
    GenerationSpec,
    NovelStoryPlan,
    StagePlan,
)


def parse_stage(
    raw: str, spec: GenerationSpec, snapshot: dict[str, Any]
) -> StagePlan | BackgroundPlan:
    if not enabled(spec):
        return plan_capacity.parse_stage(raw, spec, snapshot)
    value = normalize_questions(normalize_optional_text(parse_plan_object(raw)))
    plan = CraftPlan.model_validate(value)
    base = plan.model_dump(mode="json", exclude={"macro_progression"})
    base["scenes"] = [
        {k: v for k, v in scene.items() if k not in {"development", "size_weight"}}
        for scene in base["scenes"]
    ]
    # Validate identity, questions, and cast with the original fact boundary. Even an
    # over-cap readable plan is saved, but it cannot be scheduled or authorized.
    plan_capacity.parse_stage(
        json_text(base), spec.model_copy(update={"unit_limit": len(plan.scenes)}), snapshot
    )
    return plan


def parse_plan(
    raw: str, spec: GenerationSpec, snapshot: dict[str, Any]
) -> NovelStoryPlan | BackgroundPlan:
    if not enabled(spec):
        return guidance.selected_plan(raw, spec, snapshot)
    plan = parse_stage(raw, spec, snapshot)
    assert isinstance(plan, BackgroundPlan)
    if not 2 <= len(plan.scenes) <= 4:
        raise ValueError("单单元计划须包含二至四个场面")
    return plan
