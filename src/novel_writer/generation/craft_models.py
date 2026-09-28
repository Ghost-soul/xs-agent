"""Opt-in stage craft types; historical schemas and omitted fields remain frozen."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from novel_writer.generation.schemas import (
    AuthorizeRequest,
    AutomatedStagePlan,
    BackgroundPlan,
    BackgroundScenePlan,
    ContinueStageRequest,
    FrozenGenerationSpec,
    GenerationSpec,
    NovelRunSpec,
    NovelStoryPlan,
    PlanEdit,
    StagePlan,
    StoryPlan,
    StrictModel,
)

POLICY = "stage-craft-v1"


class StageScale(StrictModel):
    scale_mode: Literal["stage-range", "natural"] = "stage-range"
    min_characters: int = Field(default=15000, ge=1, le=100000)
    max_characters: int = Field(default=20000, ge=1, le=100000)
    preferred_units: int = Field(default=5, ge=1, le=6)

    @model_validator(mode="after")
    def ordered(self) -> StageScale:
        if self.min_characters > self.max_characters:
            raise ValueError("阶段篇幅下界不能超过上界")
        return self


class CraftSpec(NovelRunSpec):
    craft_policy: Literal["stage-craft-v1"] = "stage-craft-v1"
    stage_mode: Literal["single-unit-v1", "longform-v1"] = "longform-v1"
    unit_limit: int = Field(default=5, ge=1, le=6)
    stage_scale: StageScale = Field(default_factory=StageScale)

    @model_validator(mode="before")
    @classmethod
    def mode_defaults(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("stage_mode") == "single-unit-v1":
            return {
                "unit_limit": 1,
                "stage_scale": {"scale_mode": "natural", "preferred_units": 1},
                **value,
            }
        if isinstance(value, dict) and "stage_scale" not in value and "unit_limit" in value:
            return {**value, "stage_scale": {"preferred_units": min(5, value["unit_limit"])}}
        return value

    @model_validator(mode="after")
    def craft_scope(self) -> CraftSpec:
        expected = {
            "workflow": "novel-run-v1",
            "context_policy": "chief-focus-v4",
            "narrative_policy": "plot-led-v3",
            "length_policy": "unit-v1",
            "writing_policy": "guided-v1",
            "plan_policy": "bounded-v1",
            "automation_policy": "stage-auto-v1",
            "card_selection_policy": "separate-v1",
        }
        if any(getattr(self, key) != value for key, value in expected.items()):
            raise ValueError("新版创作合同必须完整绑定，不能混用历史策略")
        if self.enable_reader or self.milestone_unit or self.feedback_policy != "logic-v1":
            raise ValueError("新版创作保留可选逻辑核对，不开启 Reader 或定时复核")
        if self.stage_scale.preferred_units > self.unit_limit:
            raise ValueError("首选单元数不能超过本次授权上限")
        if self.stage_mode == "single-unit-v1" and self.stage_scale.scale_mode != "natural":
            raise ValueError("单单元模式不继承长篇阶段目标")
        return self


class Development(StrictModel):
    onstage_process: str = Field(default="", max_length=2000)
    reveal_or_turn: str = Field(default="", max_length=1400)
    payoff_or_aftermath: str = Field(default="", max_length=1400)
    local_freedom: str = Field(default="", max_length=2000)


class CraftUnit(BackgroundScenePlan):
    development: Development = Field(default_factory=Development)
    size_weight: float = Field(default=1, gt=0, le=100, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def optional_weight(cls, value: Any) -> Any:
        if isinstance(value, dict):
            try:
                weight = float(value.get("size_weight", 1))
            except (ValueError, TypeError):
                weight = 1
            return {**value, "size_weight": weight if 0 < weight <= 100 else 1}
        return value


class CraftPlan(BackgroundPlan):
    scenes: list[CraftUnit] = Field(min_length=1, max_length=6)  # type: ignore[assignment]
    macro_progression: str = Field(default="", max_length=2000)


class CraftPlanEdit(PlanEdit):
    plan: StoryPlan | NovelStoryPlan | StagePlan | AutomatedStagePlan | BackgroundPlan | CraftPlan


class CraftAuthorization(AuthorizeRequest):
    expected_plan_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    expected_adjustment_sha256: str | None = Field(default=None, min_length=64, max_length=64)


class CraftContinuation(ContinueStageRequest):
    expected_adjustment_sha256: str | None = Field(default=None, min_length=64, max_length=64)


def enabled(spec: GenerationSpec) -> bool:
    return getattr(spec, "craft_policy", None) == POLICY


def read_spec(value: Any) -> GenerationSpec:
    if isinstance(value, dict) and value.get("craft_policy") == POLICY:
        return CraftSpec.model_validate(value)
    return FrozenGenerationSpec.model_validate(value)


def execution_spec(spec: dict[str, Any], request: dict[str, Any]) -> GenerationSpec:
    options = request.get("feedback_options", {})
    if options.get("craft_policy") == POLICY and spec.get("craft_policy") != POLICY:
        from novel_writer.generation.amendments import with_limits

        return with_limits(read_spec(spec), {**options, "max_cost_cny": spec["max_cost_cny"]})
    result = read_spec(spec).model_copy(
        update={k: v for k, v in options.items() if k != "stage_scale"}
    )
    if enabled(result) and "stage_scale" in options:
        result = result.model_copy(
            update={"stage_scale": StageScale.model_validate(options["stage_scale"])}
        )
    return result
