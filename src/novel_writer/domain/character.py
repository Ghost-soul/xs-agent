from __future__ import annotations

from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from novel_writer.domain.base import FrozenModel


class MindBelief(FrozenModel):
    content: str = Field(min_length=1, max_length=500)
    confidence: int = Field(default=50, ge=0, le=100)


class MindDrive(FrozenModel):
    content: str = Field(min_length=1, max_length=500)
    intensity: int = Field(default=50, ge=0, le=100)


class MindEmotion(FrozenModel):
    emotion: str = Field(min_length=1, max_length=100)
    intensity: int = Field(default=50, ge=0, le=100)
    cause: str = Field(default="", max_length=500)


class InternalConflict(FrozenModel):
    side_a: str = Field(min_length=1, max_length=500)
    side_b: str = Field(min_length=1, max_length=500)
    pressure: int = Field(default=50, ge=0, le=100)


class CharacterMindState(FrozenModel):
    beliefs: tuple[MindBelief, ...] = ()
    desires: tuple[MindDrive, ...] = ()
    fears: tuple[MindDrive, ...] = ()
    current_emotions: tuple[MindEmotion, ...] = ()
    internal_conflicts: tuple[InternalConflict, ...] = ()
    coping_strategy: str = Field(default="", max_length=1000)


class CharacterVoiceExample(FrozenModel):
    quote: str = Field(min_length=1, max_length=300)
    source: Literal["author", "formal_chapter"] = "author"
    chapter_ordinal: int | None = Field(default=None, ge=1)


class CharacterPortrayalProfile(FrozenModel):
    independent_goal: str = Field(default="", max_length=1000)
    unique_competence: str = Field(default="", max_length=1000)
    value_boundary: str = Field(default="", max_length=1000)
    attention_bias: tuple[str, ...] = Field(default=(), max_length=4)
    conflict_method: str = Field(default="", max_length=1000)
    stress_response: str = Field(default="", max_length=1000)
    voice_examples: tuple[CharacterVoiceExample, ...] = Field(default=(), max_length=3)
    anti_examples: tuple[str, ...] = Field(default=(), max_length=3)


class Character(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    personality: str = Field(default="", max_length=2000)
    speech_style: str = Field(default="", max_length=2000)
    decision_style: str = Field(default="", max_length=2000)
    forbidden_behaviors: tuple[str, ...] = ()
    current_state: str = Field(default="", max_length=2000)
    development_history: tuple[str, ...] = Field(default=(), max_length=20)
    library_status: Literal["active", "retired"] = "active"
    tier: Literal["A", "B", "C"] = "A"
    mind_state: CharacterMindState = Field(default_factory=CharacterMindState)
    aliases: tuple[str, ...] = Field(default=(), max_length=12)
    portrayal_profile: CharacterPortrayalProfile = Field(default_factory=CharacterPortrayalProfile)
    location_id: UUID | None = None

    @model_validator(mode="after")
    def validate_aliases(self) -> Character:
        normalized = [item.strip().casefold() for item in self.aliases]
        if any(not item for item in normalized):
            raise ValueError("character aliases cannot be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("character aliases must be unique")
        if self.name.strip().casefold() in normalized:
            raise ValueError("character aliases must not repeat the formal name")
        return self


class CharacterRelationship(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    source_character_id: UUID
    target_character_id: UUID
    relation_type: str = Field(min_length=1, max_length=80)
    description: str = ""


class CharacterBelief(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    character_id: UUID
    proposition: str = Field(min_length=1)
    is_true: bool | None = None
