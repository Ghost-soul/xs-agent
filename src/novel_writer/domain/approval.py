from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import Field

from novel_writer.domain.base import FrozenModel


class ApprovalTarget(StrEnum):
    CHAPTER_BRIEF = "chapter_brief"
    CHAPTER_BODY = "chapter_body"
    STATE_DELTA = "state_delta"



class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"



class Approval(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    target_type: ApprovalTarget
    target_id: UUID
    decision: ApprovalDecision
    reason: str | None = None
    created_at: datetime



class BriefScenePlan(FrozenModel):
    title: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1)
    location: str = Field(min_length=1, max_length=120)
    viewpoint: str = Field(min_length=1, max_length=100)
    entry_state: str = Field(min_length=1)
    character_goal: str = Field(min_length=1)
    obstacle: str = Field(min_length=1)
    escalation: str = Field(min_length=1)
    turning_point: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    disclosure: str = ""
    withheld_information: str = ""
    state_changes: tuple[str, ...] = ()



class BriefOption(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=120)
    logline: str = Field(min_length=1)
    dramatic_question: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    emotional_arc: str = Field(min_length=1)
    ending_hook: str = Field(min_length=1)
    scene_plans: tuple[BriefScenePlan, ...] = Field(min_length=2, max_length=6)



class ChapterBrief(FrozenModel):
    chapter_id: UUID
    chapter_ordinal: int = Field(ge=1)
    base_version: int = Field(ge=1)
    objective: str
    scenes: tuple[str, ...]
    constraints: tuple[str, ...] = ()
    inspiration: str = ""
    dramatic_question: str = ""
    emotional_arc: str = ""
    ending_hook: str = ""
    scene_plans: tuple[BriefScenePlan, ...] = ()


NonEmptyText = Annotated[str, Field(min_length=1)]
