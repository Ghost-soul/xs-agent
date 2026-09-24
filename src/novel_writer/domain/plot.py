from __future__ import annotations

from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from novel_writer.domain.base import FrozenModel


class PlotThread(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=120)
    status: Literal["open", "resolved", "abandoned"] = "open"
    priority: int = Field(default=50, ge=0, le=100)
    last_advanced_chapter: int | None = Field(default=None, ge=1)
    summary: str = ""
    thread_type: Literal["main", "side"] = "main"
    goal: str = ""
    progress: int = Field(default=0, ge=0, le=100)
    related_characters: tuple[str, ...] = ()



class OpenQuestion(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    question: str = Field(min_length=1, max_length=500)
    category: Literal["character", "world", "plot"] = "plot"
    status: Literal["open", "partial", "answered", "abandoned"] = "open"
    raised_chapter: int | None = Field(default=None, ge=1)
    current_clues: str = ""
    answer_plan: str = ""
    importance: int = Field(default=50, ge=0, le=100)


class FormalBodyEvidence(FrozenModel):
    """Immutable evidence anchored to one formal chapter body."""

    chapter_id: UUID
    chapter_ordinal: int = Field(ge=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_offsets(self) -> Self:
        if self.end <= self.start:
            raise ValueError("formal body evidence end must be greater than start")
        return self


class ForeshadowingLifecycleEvent(FrozenModel):
    action: Literal[
        "introduced",
        "lightly_reinforced",
        "advanced",
        "marked_ready",
        "fulfilled",
        "abandoned",
        "plan_updated",
    ]
    chapter_id: UUID
    chapter_ordinal: int = Field(ge=1)
    before_status: str
    after_status: str
    note: str = Field(min_length=1)
    evidence: tuple[FormalBodyEvidence, ...] = Field(min_length=1)



class Foreshadowing(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    content: str = Field(min_length=1, max_length=1000)
    importance: Literal["low", "medium", "high"] = "medium"
    introduced_chapter: int = Field(ge=1)
    last_advanced_chapter: int = Field(ge=1)
    expected_payoff: str = Field(default="", max_length=500)
    recovery_condition: str = Field(default="", max_length=1000)
    status: Literal[
        "candidate",
        "active",
        "lightly_reinforced",
        "advanced",
        "ready_for_payoff",
        "fulfilled",
        "abandoned",
    ] = "active"
    related_characters: tuple[str, ...] = ()
    related_plot_threads: tuple[UUID, ...] = ()
    related_open_questions: tuple[UUID, ...] = ()
    related_reader_promises: tuple[UUID, ...] = ()
    reader_visible: bool = True
    payoff_readiness: int = Field(default=0, ge=0, le=100)
    reminder_after_chapters: int = Field(default=20, ge=1, le=500)
    history: tuple[str, ...] = Field(default=(), max_length=50)
    lifecycle_events: tuple[ForeshadowingLifecycleEvent, ...] = ()
    fulfilled_chapter: int | None = Field(default=None, ge=1)
    fulfilled_evidence: tuple[FormalBodyEvidence, ...] = ()

    @model_validator(mode="after")
    def validate_chapters(self) -> Self:
        if self.last_advanced_chapter < self.introduced_chapter:
            raise ValueError("foreshadowing last advance cannot precede introduction")
        if (
            self.status == "fulfilled"
            and self.fulfilled_chapter is not None
            and self.fulfilled_chapter < self.introduced_chapter
        ):
            raise ValueError("foreshadowing fulfillment cannot precede introduction")
        if self.status != "fulfilled" and (
            self.fulfilled_chapter is not None or self.fulfilled_evidence
        ):
            raise ValueError("only fulfilled foreshadowing may carry fulfillment evidence")
        return self



class ReaderDisclosure(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    fact_key: str = Field(min_length=1, max_length=160)
    statement: str = Field(min_length=1)
    first_revealed_in_scene_id: UUID



class EvidenceSpan(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_offsets(self) -> Self:
        if self.end <= self.start:
            raise ValueError("evidence end must be greater than start")
        return self



class ReaderPromiseEvidence(FrozenModel):
    chapter_id: UUID
    chapter_ordinal: int = Field(ge=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_offsets(self) -> Self:
        if self.end <= self.start:
            raise ValueError("reader promise evidence end must be greater than start")
        return self



class ReaderPromiseUpdate(FrozenModel):
    action: Literal["established", "advanced", "delayed", "fulfilled"]
    chapter_ordinal: int = Field(ge=1)
    note: str = Field(min_length=1)
    evidence: tuple[ReaderPromiseEvidence, ...] = Field(min_length=1)



class ReaderPromise(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    kind: Literal["mystery", "danger", "relationship", "growth", "payoff"]
    summary: str = Field(min_length=1, max_length=500)
    status: Literal["open", "fulfilled"] = "open"
    established_chapter: int = Field(ge=1)
    last_updated_chapter: int = Field(ge=1)
    history: tuple[ReaderPromiseUpdate, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_history(self) -> Self:
        if self.history[0].action != "established":
            raise ValueError("reader promise history must start with established")
        if self.history[0].chapter_ordinal != self.established_chapter:
            raise ValueError("reader promise established chapter does not match history")
        if self.history[-1].chapter_ordinal != self.last_updated_chapter:
            raise ValueError("reader promise last updated chapter does not match history")
        if any(
            later.chapter_ordinal < earlier.chapter_ordinal
            for earlier, later in zip(self.history, self.history[1:], strict=False)
        ):
            raise ValueError("reader promise history must be chronological")
        fulfilled = self.history[-1].action == "fulfilled"
        if fulfilled != (self.status == "fulfilled"):
            raise ValueError("reader promise status does not match its latest update")
        return self



