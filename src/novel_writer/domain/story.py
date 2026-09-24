from __future__ import annotations

import re
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, field_validator

from novel_writer.domain.base import FrozenModel


class StoryProject(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=200)



class Chapter(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    ordinal: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)



class StoryFoundation(FrozenModel):
    theme: str = ""
    core_expression: str = ""
    personal_side: str = ""
    opposing_side: str = ""
    long_term_conflict: str = ""
    primary_driver: str = ""
    secondary_drivers: tuple[str, ...] = ()



class NarrativePhase(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=160)
    goal: str = ""
    expected_change: str = ""
    status: Literal["pending", "active", "completed", "cancelled"] = "pending"



class PlotHistoryDecision(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    plan: str = Field(min_length=1, max_length=500)
    status: Literal["completed", "cancelled", "revised"]
    reason: str = ""
    chapter_ordinal: int | None = Field(default=None, ge=1)



class StoryForce(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    layer: Literal["world", "outline"] = "world"
    kind: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    goal: str = ""
    resources: tuple[str, ...] = ()
    state: str = ""
    influence: int = Field(default=50, ge=0, le=100)
    trajectory: str = ""
    scope: str = ""
    active: bool = True



class ScheduledDevelopment(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    layer: Literal["world", "outline"] = "outline"
    name: str = Field(min_length=1, max_length=120)
    trigger: str = ""
    outcome: str = Field(min_length=1)
    timing: str = ""
    probability: int = Field(default=100, ge=0, le=100)
    status: Literal["pending", "triggered", "cancelled"] = "pending"
    potential_impact: str = ""



class NarrativePosition(FrozenModel):
    current_phase: str = ""
    current_time: str = ""
    current_location: str = ""
    current_characters: tuple[str, ...] = ()
    recent_major_event: str = ""
    current_conflict: str = ""
    in_progress: str = ""
    horizon: str = ""
    notes: str = ""

    @field_validator("current_characters", mode="before")
    @classmethod
    def split_joined_character_names(cls, value: object) -> object:
        if not isinstance(value, list | tuple):
            return value
        names: list[str] = []
        for item in value:
            names.extend(part.strip() for part in re.split(r"[,，、]", str(item)) if part.strip())
        return tuple(dict.fromkeys(names))


