from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from novel_writer.generation.token_limits import INPUT_TOKEN_LIMIT, TOKEN_LIMIT

REVISION = "genre-led-single-chapter-v1"
PARSER_REVISION = "generation-json-v1"
NOVEL_REVISION = "genre-led-novel-run-v1"
NOVEL_PARSER_REVISION = "novel-run-evidence-v1"
LONGFORM_REVISION = "genre-led-longform-v1"
LONGFORM_PARSER_REVISION = "longform-evidence-v1"
LONGFORM_PLAN_PARSER_REVISION = "longform-plan-capacity-v2"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoleModel(StrictModel):
    model: str = Field(min_length=1, max_length=160)
    tokenizer_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    output_limit: int = Field(default=TOKEN_LIMIT, ge=2000, le=TOKEN_LIMIT)


class GenerationSpec(StrictModel):
    workflow: Literal["single-chapter-v1", "novel-run-v1"] = "single-chapter-v1"
    context_policy: Literal["full-v1", "bounded-v1", "focused-v1", "world-bounded-v1"] = "full-v1"
    automation_policy: Literal["legacy-v1", "stage-auto-v1"] = "legacy-v1"
    feedback_policy: Literal["legacy-v1", "advisory-v1", "logic-v1"] = "legacy-v1"
    writing_policy: Literal["legacy-v1", "creative-v1", "background-v1", "guided-v1"] = "legacy-v1"
    card_selection_policy: Literal["legacy-v1", "separate-v1"] = "legacy-v1"
    narrative_card_ids: list[str] = Field(default_factory=list)
    narrative_policy: Literal["legacy-v1", "causal-v1"] = "legacy-v1"
    plan_policy: Literal["exact-v1", "bounded-v1"] = "exact-v1"
    length_policy: Literal["legacy-v1", "unit-v1"] = "legacy-v1"
    enable_checker: bool = True
    enable_reader: bool = False
    roles: dict[Literal["memory", "checker", "reader", "editor"], RoleModel] = Field(
        default_factory=dict
    )
    enable_editor: bool = False
    generate_title: bool = False
    base_version_id: UUID
    focus_card_id: str = Field(min_length=1, max_length=80)
    supporting_card_id: str | None = Field(default=None, max_length=80)
    direction: str = Field(min_length=1, max_length=2000)
    author_boundaries: str = Field(default="", max_length=4000)
    character_selection: Literal["manual", "chief-auto-v1"] = "manual"
    character_ids: list[str] = Field(default_factory=list, max_length=12)
    viewpoint: str = Field(default="", max_length=1000)
    relationship_scope: Literal[
        "genre-led", "explore", "specified_pair", "non_romantic", "not_applicable"
    ]
    relationship_character_ids: list[str] = Field(default_factory=list, max_length=12)
    profile_id: str = Field(min_length=2, max_length=40)
    chief_model: str = Field(min_length=1, max_length=160)
    writer_model: str = Field(min_length=1, max_length=160)
    chief_tokenizer_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    writer_tokenizer_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    stage_mode: Literal["single-unit-v1", "longform-v1"] = "single-unit-v1"
    chapter_count: int | None = Field(default=1, ge=1, le=3)
    unit_limit: int = Field(default=1, ge=1, le=6)
    milestone_unit: int | None = Field(default=None, ge=2, le=5)
    previous_stage_id: UUID | None = None
    target_characters: int | None = Field(default=4000, ge=2000, le=6000)
    input_limit: int = Field(default=INPUT_TOKEN_LIMIT, ge=8000, le=INPUT_TOKEN_LIMIT)
    chief_output_limit: int = Field(default=TOKEN_LIMIT, ge=2000, le=TOKEN_LIMIT)
    auxiliary_output_limit: int | None = Field(default=TOKEN_LIMIT, ge=2000, le=TOKEN_LIMIT)
    writer_output_limit: int = Field(default=TOKEN_LIMIT, ge=4000, le=TOKEN_LIMIT)
    max_cost_cny: Decimal = Field(ge=0, le=10000)
    timeout_seconds: int = Field(default=600, ge=60, le=1200)
    pause_after_plan: bool = False

    @model_validator(mode="after")
    def validate_scope(self) -> GenerationSpec:
        if self.length_policy == "unit-v1":
            if self.workflow != "novel-run-v1" or self.writing_policy not in {
                "background-v1",
                "guided-v1",
            }:
                raise ValueError("按叙事单元创作须使用新版 NovelRun 创作引导")
            self.chapter_count = self.target_characters = None
        elif self.chapter_count is None or self.target_characters is None:
            raise ValueError("历史篇幅合同缺少原章节数或目标字数")
        self.narrative_card_ids = [value.strip() for value in self.narrative_card_ids]
        if any(not value or len(value) > 80 for value in self.narrative_card_ids):
            raise ValueError("叙事卡 ID 须为1至80字的非空值")
        if len(self.narrative_card_ids) != len(set(self.narrative_card_ids)):
            raise ValueError("叙事卡不能重复")
        if self.card_selection_policy == "separate-v1":
            if self.workflow != "novel-run-v1" or self.writing_policy != "guided-v1":
                raise ValueError("独立叙事选卡须使用新版 NovelRun 创作引导")
        elif self.narrative_card_ids:
            raise ValueError("多张叙事卡须使用 separate-v1 选卡方式")
        if self.relationship_scope == "genre-led":
            if self.workflow != "novel-run-v1":
                raise ValueError("题材自主设计仅适用于新版 NovelRun")
            if self.relationship_character_ids:
                raise ValueError("题材自主设计不接受独立关系名单，请在作者描述中说明要求")
        if self.stage_mode == "longform-v1":
            if self.workflow != "novel-run-v1":
                raise ValueError("多单元阶段仅适用于 NovelRun")
            if self.milestone_unit and self.milestone_unit >= self.unit_limit:
                raise ValueError("纠偏里程碑必须在最后单元之前")
        elif self.chapter_count not in (None, 1) or self.unit_limit != 1 or self.previous_stage_id:
            raise ValueError("单单元模式不接受多章或跨阶段参数")
        automatic = self.character_selection == "chief-auto-v1"
        if automatic and self.workflow != "novel-run-v1":
            raise ValueError("自动选角仅适用于新版 NovelRun")
        if not automatic and not self.character_ids:
            raise ValueError("手选模式需要至少一位人物")
        if len(self.character_ids) != len(set(self.character_ids)):
            raise ValueError("人物范围不能重复")
        if not automatic and not set(self.relationship_character_ids) <= set(self.character_ids):
            raise ValueError("关系对象必须属于本次人物范围")
        if (
            self.relationship_scope == "specified_pair"
            and len(self.relationship_character_ids) != 2
        ):
            raise ValueError("指定关系需要两位不同人物")
        if len(set(self.relationship_character_ids)) != len(self.relationship_character_ids):
            raise ValueError("关系对象不能重复")
        if (
            self.relationship_scope == "explore"
            and len(self.relationship_character_ids) < 2
            and not (automatic and not self.relationship_character_ids)
        ):
            raise ValueError("爱情探索需明确至少两位允许探索的人物，不等于确定最终 CP")
        if not self.direction.strip() or (
            self.relationship_scope != "genre-led" and not self.viewpoint.strip()
        ):
            raise ValueError("请填写本阶段方向与视角范围")
        return self


class FrozenGenerationSpec(GenerationSpec):
    """Read saved batches with their historical omitted-field semantics, never create new work."""

    input_limit: int = Field(default=100000, ge=8000, le=INPUT_TOKEN_LIMIT)
    chief_output_limit: int = Field(default=6000, ge=2000, le=TOKEN_LIMIT)
    auxiliary_output_limit: int | None = Field(default=None, ge=2000, le=TOKEN_LIMIT)
    writer_output_limit: int = Field(default=12000, ge=4000, le=TOKEN_LIMIT)


class NovelRunSpec(GenerationSpec):
    # Existing stored specs without workflow keep their legacy interpretation.
    length_policy: Literal["legacy-v1", "unit-v1"] = "unit-v1"
    chapter_count: int | None = Field(default=None, ge=1, le=3)
    target_characters: int | None = Field(default=None, ge=2000, le=6000)

    @model_validator(mode="before")
    @classmethod
    def historical_length_defaults(cls, value):
        if isinstance(value, dict) and value.get("length_policy") == "legacy-v1":
            return {"chapter_count": 1, "target_characters": 4000, **value}
        return value

    plan_policy: Literal["exact-v1", "bounded-v1"] = "bounded-v1"
    workflow: Literal["single-chapter-v1", "novel-run-v1"] = "novel-run-v1"
    card_selection_policy: Literal["legacy-v1", "separate-v1"] = "separate-v1"
    narrative_policy: Literal["legacy-v1", "causal-v1"] = "causal-v1"
    feedback_policy: Literal["legacy-v1", "advisory-v1", "logic-v1"] = "logic-v1"
    writing_policy: Literal["legacy-v1", "creative-v1", "background-v1", "guided-v1"] = "guided-v1"
    context_policy: Literal[
        "full-v1", "bounded-v1", "focused-v1", "world-bounded-v1"
    ] = "world-bounded-v1"
    automation_policy: Literal["legacy-v1", "stage-auto-v1"] = "stage-auto-v1"
    relationship_scope: Literal[
        "genre-led", "explore", "specified_pair", "non_romantic", "not_applicable"
    ] = "genre-led"


class ScenePlan(StrictModel):
    event: str = Field(min_length=1, max_length=1400)
    character_ids: list[str] = Field(min_length=1, max_length=12)
    focus_percent: int = Field(ge=0, le=100)
    transition_percent: int = Field(ge=0, le=100)
    other_percent: int = Field(ge=0, le=100)
    choice_and_response: str = Field(min_length=1, max_length=1400)
    consequence: str = Field(min_length=1, max_length=1000)


class StoryPlan(StrictModel):
    chapter_goal: str = Field(min_length=1, max_length=1200)
    bridge: str = Field(min_length=1, max_length=1200)
    scenes: list[ScenePlan] = Field(min_length=2, max_length=4)
    major_turn: str = Field(min_length=1, max_length=1200)
    genre_causal_role: str = Field(min_length=1, max_length=1200)
    opening_focus_percent: int = Field(ge=0, le=20)
    questions: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_distribution(self) -> StoryPlan:
        focus = sum(s.focus_percent for s in self.scenes)
        transition = sum(s.transition_percent for s in self.scenes)
        other = sum(s.other_percent for s in self.scenes)
        if focus + transition + other != 100 or focus < 70 or transition > 20:
            raise ValueError("预计篇幅须合计 100%，主导题材至少 70%，独立过渡最多 20%")
        if any(s.focus_percent + s.transition_percent + s.other_percent == 0 for s in self.scenes):
            raise ValueError("场面必须分配篇幅")
        return self


class EvidenceFinding(StrictModel):
    observation: str = Field(min_length=1, max_length=1200)
    paragraph_ids: list[str] = Field(min_length=1, max_length=24)


class NovelStoryPlan(StoryPlan):
    question_scopes: dict[str, Literal["current_unit", "later"]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_questions(self) -> NovelStoryPlan:
        if set(self.question_scopes) - set(self.questions):
            raise ValueError("问题范围必须对应实际问题")
        return self


class StagePlan(StrictModel):
    chapter_goal: str = Field(min_length=1, max_length=1200)
    bridge: str = Field(min_length=1, max_length=1200)
    scenes: list[ScenePlan] = Field(min_length=1, max_length=6)
    major_turn: str = Field(min_length=1, max_length=1200)
    genre_causal_role: str = Field(min_length=1, max_length=1200)
    opening_focus_percent: int = Field(ge=0, le=20)
    questions: list[str] = Field(default_factory=list, max_length=8)
    question_scopes: dict[str, Literal["current_unit", "later"]] = Field(default_factory=dict)
    future_proposal: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def distribution(self) -> StagePlan:
        focus = sum(s.focus_percent for s in self.scenes)
        transition = sum(s.transition_percent for s in self.scenes)
        other = sum(s.other_percent for s in self.scenes)
        if focus + transition + other != 100 or focus < 70 or transition > 20:
            raise ValueError("全阶段份额合计100%，主导至少70%，独立过渡最多20%")
        if any(s.focus_percent + s.transition_percent + s.other_percent == 0 for s in self.scenes):
            raise ValueError("每个单元必须分配篇幅")
        if set(self.question_scopes) - set(self.questions):
            raise ValueError("问题范围必须对应实际问题")
        return self


class StageComparison(EvidenceFinding):
    decision: Literal["continue", "revise", "pause"]
    genre_progress: Literal["started", "changed", "missing", "unknown"]
    plan: StagePlan


class AuthorBlocker(StrictModel):
    kind: Literal["author_boundary_conflict", "missing_canonical_fact"]
    source: str = Field(min_length=1, max_length=1000)
    why_blocked: str = Field(min_length=1, max_length=1000)


class AutomatedStagePlan(StagePlan):
    story_questions: list[str] = Field(max_length=12)
    author_question_reasons: dict[str, AuthorBlocker]

    @model_validator(mode="after")
    def author_questions_have_reasons(self) -> AutomatedStagePlan:
        if set(self.questions) != set(self.author_question_reasons):
            raise ValueError("每个作者问题必须给出事实缺口或明确边界冲突及其来源")
        if set(self.questions) & set(self.story_questions):
            raise ValueError("剧情悬念不能同时作为作者问题")
        return self


class AutomatedStageComparison(StageComparison):
    plan: AutomatedStagePlan


class BackgroundScenePlan(StrictModel):
    event: str = Field(min_length=1, max_length=1400)
    character_ids: list[str] = Field(min_length=1, max_length=12)
    choice_and_response: str = Field(min_length=1, max_length=1400)
    consequence: str = Field(min_length=1, max_length=1000)


class BackgroundPlan(StrictModel):
    chapter_goal: str = Field(min_length=1, max_length=1200)
    bridge: str = Field(min_length=1, max_length=1200)
    scenes: list[BackgroundScenePlan] = Field(min_length=1, max_length=6)
    major_turn: str = Field(min_length=1, max_length=1200)
    world_context: str = Field(default="", max_length=4000)
    questions: list[str] = Field(default_factory=list, max_length=8)
    question_scopes: dict[str, Literal["current_unit", "later"]] = Field(default_factory=dict)
    future_proposal: str = Field(default="", max_length=2000)
    story_questions: list[str] = Field(default_factory=list, max_length=12)
    author_question_reasons: dict[str, AuthorBlocker] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def discard_obsolete_quotas(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {
            **{
                k: v
                for k, v in value.items()
                if k not in {"scenes", "genre_causal_role", "opening_focus_percent"}
            },
            "scenes": [
                {
                    k: v
                    for k, v in s.items()
                    if k not in {"focus_percent", "transition_percent", "other_percent"}
                }
                if isinstance(s, dict)
                else s
                for s in value.get("scenes", [])
            ]
            if isinstance(value.get("scenes"), list)
            else value.get("scenes"),
        }

    @model_validator(mode="after")
    def validate_questions(self) -> BackgroundPlan:
        if set(self.question_scopes) - set(self.questions):
            raise ValueError("问题范围必须对应实际问题")
        if set(self.questions) != set(self.author_question_reasons):
            raise ValueError("每个作者问题必须说明事实缺口或明确边界冲突及来源")
        if set(self.questions) & set(self.story_questions):
            raise ValueError("剧情悬念不能同时作为作者问题")
        return self


class BackgroundComparison(EvidenceFinding):
    decision: Literal["continue", "revise", "pause"]
    plan: BackgroundPlan

    @property
    def genre_progress(self) -> str:
        # Historical artifact readers understand this field; it is not requested or scored.
        return "unknown"


class ParagraphCategory(StrictModel):
    paragraph_ids: list[str] = Field(min_length=1, max_length=100)
    category: Literal["focus", "transition", "other", "unknown"]
    reason: str = Field(min_length=1, max_length=500)


class ChapterReview(StrictModel):
    outcome: Literal["realized", "partial", "missing", "unknown"]
    explanation: str = Field(min_length=1, max_length=2000)
    findings: list[EvidenceFinding] = Field(default_factory=list, max_length=16)
    continuity_conflicts: list[EvidenceFinding] = Field(default_factory=list, max_length=12)
    # Optional diagnostics are validated separately, never a gate on the whole report.
    classifications: list[Any] = Field(default_factory=list, max_length=100)
    continuity: dict[str, Any] | None = None
    revision_advice: str = Field(default="", max_length=2000)


class AuthorizeRequest(StrictModel):
    preview_sha256: str = Field(min_length=64, max_length=64)
    confirmed: Literal[True]


class AdoptRequest(StrictModel):
    preview_sha256: str = Field(min_length=64, max_length=64)
    title: str = Field(default="", max_length=200)
    narrative_position: dict[str, Any]
    factual_changes: dict[str, Any] = Field(default_factory=dict)
    confirmed: Literal[True]
    accept_genre_deviation: bool = False
    facts_confirmed: bool = False


class CandidateEdit(StrictModel):
    body: str = Field(min_length=1, max_length=40000)
    expected_body_sha256: str = Field(min_length=64, max_length=64)


class PlanEdit(StrictModel):
    plan: StoryPlan | NovelStoryPlan | StagePlan | AutomatedStagePlan | BackgroundPlan
    expected_plan_sha256: str | None = None
    author_note: str = Field(min_length=1, max_length=4000)
    question_answers: dict[str, str] = Field(default_factory=dict)
    deferred_questions: list[str] = Field(default_factory=list, max_length=8)


class ContinueStageRequest(StrictModel):
    preview_sha256: str = Field(min_length=64, max_length=64)
    expected_plan_sha256: str = Field(min_length=64, max_length=64)
    expected_questions_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    question_answers: dict[str, str] = Field(default_factory=dict, max_length=8)
    delegated_questions: list[str] = Field(default_factory=list, max_length=8)
    author_note: str = Field(default="", max_length=2000)
    confirmed: Literal[True]


class ResolveUnknown(StrictModel):
    confirmed: Literal[True]
    note: str = Field(min_length=1, max_length=2000)


class AmendmentRequest(StrictModel):
    length_policy: Literal["legacy-v1", "unit-v1"] = "unit-v1"
    narrative_policy: Literal["legacy-v1", "causal-v1"] = "causal-v1"
    context_policy: Literal[
        "full-v1", "bounded-v1", "focused-v1", "world-bounded-v1"
    ] = "world-bounded-v1"
    candidate_sha256: str = Field(min_length=64, max_length=64)
    mode: Literal["local", "rewrite", "verify"]
    instruction: str = Field(min_length=1, max_length=2000)
    paragraph_ids: list[str] = Field(default_factory=list, max_length=12)
    protected_paragraph_ids: list[str] = Field(default_factory=list, max_length=100)
    max_cost_cny: Decimal = Field(ge=0, le=10000)
    input_limit: int = Field(default=INPUT_TOKEN_LIMIT, ge=4000, le=INPUT_TOKEN_LIMIT)
    output_limit: int = Field(default=TOKEN_LIMIT, ge=4000, le=TOKEN_LIMIT)
    feedback_policy: Literal["legacy-v1", "advisory-v1", "logic-v1"] = "logic-v1"
    writing_policy: Literal["legacy-v1", "creative-v1", "background-v1", "guided-v1"] = "guided-v1"
    enable_checker: bool = True
    enable_reader: bool = False


class AmendmentAuthorization(StrictModel):
    preview_sha256: str = Field(min_length=64, max_length=64)
    confirmed: Literal[True]


class ChapterApproval(StrictModel):
    chapter_id: UUID
    title: str = Field(default="", max_length=200)
    narrative_position: dict[str, Any]
    factual_changes: dict[str, Any] = Field(default_factory=dict)
    facts_confirmed: Literal[True]


class StageAdoptRequest(StrictModel):
    chapters: list[ChapterApproval] = Field(min_length=1, max_length=6)
    preview_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    confirmed: bool = False
    accept_genre_deviation: bool = False
