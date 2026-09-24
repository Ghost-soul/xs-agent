from __future__ import annotations

from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from novel_writer.domain.base import FrozenModel


class Place(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=120)
    description: str = ""



class WorldRule(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    statement: str = Field(min_length=1)



class WorldLoreEntry(FrozenModel):
    """Structured world-bible entry, either authored or extracted from prose."""

    id: UUID = Field(default_factory=uuid4)
    category: Literal[
        "world_structure",
        "geography",
        "history",
        "power_system",
        "society",
        "civilization",
        "species",
        "faction",
        "core_secret",
    ]
    subsection: str = Field(default="", max_length=80)
    name: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1)
    details: tuple[str, ...] = ()
    stability: Literal["static", "dynamic"] = "static"
    risk_level: Literal["low", "high"] = "high"
    source: Literal["author", "extracted"] = "author"
    first_seen_chapter: int | None = Field(default=None, ge=1)
    evidence_quote: str = ""



class Scene(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    chapter_id: UUID
    chapter_ordinal: int = Field(ge=1)
    ordinal: int = Field(ge=1)
    viewpoint_character_id: UUID | None = None
    location_id: UUID | None = None
    event_ids: tuple[UUID, ...] = ()
    summary: str = Field(min_length=1)
    goal: str = ""
    obstacle: str = ""
    choice: str = ""
    result: str = ""
    cost: str = ""
    next_pressure: str = ""
    expectation_change: str = ""



class StoryEvent(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    summary: str = Field(min_length=1)
    happens_before: tuple[UUID, ...] = ()
    participants: tuple[UUID, ...] = ()



class TimelineConstraint(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    before_event_id: UUID
    after_event_id: UUID
    reason: str = ""


