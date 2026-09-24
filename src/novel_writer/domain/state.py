from __future__ import annotations

from collections import deque
from typing import Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from novel_writer.domain.base import FrozenModel, Identified
from novel_writer.domain.character import Character, CharacterBelief, CharacterRelationship
from novel_writer.domain.plot import (
    EvidenceSpan,
    Foreshadowing,
    OpenQuestion,
    PlotThread,
    ReaderDisclosure,
    ReaderPromise,
)
from novel_writer.domain.story import (
    NarrativePhase,
    NarrativePosition,
    PlotHistoryDecision,
    ScheduledDevelopment,
    StoryForce,
    StoryFoundation,
)
from novel_writer.domain.world import (
    Place,
    Scene,
    StoryEvent,
    TimelineConstraint,
    WorldLoreEntry,
    WorldRule,
)


class StoryState(FrozenModel):
    characters: tuple[Character, ...] = ()
    places: tuple[Place, ...] = ()
    scenes: tuple[Scene, ...] = ()
    events: tuple[StoryEvent, ...] = ()
    timeline_constraints: tuple[TimelineConstraint, ...] = ()
    relationships: tuple[CharacterRelationship, ...] = ()
    world_rules: tuple[WorldRule, ...] = ()
    world_lore: tuple[WorldLoreEntry, ...] = ()
    beliefs: tuple[CharacterBelief, ...] = ()
    plot_threads: tuple[PlotThread, ...] = ()
    story_foundation: StoryFoundation = Field(default_factory=StoryFoundation)
    open_questions: tuple[OpenQuestion, ...] = ()
    foreshadowings: tuple[Foreshadowing, ...] = ()
    narrative_phases: tuple[NarrativePhase, ...] = ()
    plot_history: tuple[PlotHistoryDecision, ...] = ()
    story_forces: tuple[StoryForce, ...] = ()
    scheduled_developments: tuple[ScheduledDevelopment, ...] = ()
    narrative_position: NarrativePosition = Field(default_factory=NarrativePosition)
    disclosures: tuple[ReaderDisclosure, ...] = ()
    reader_promises: tuple[ReaderPromise, ...] = ()

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        active = [item for item in self.characters if item.library_status == "active"]
        if sum(item.tier == "A" for item in active) > 20:
            raise ValueError("character library may contain at most 20 active A-tier members")
        if sum(item.tier == "B" for item in active) > 100:
            raise ValueError("character library may contain at most 100 active B-tier members")
        collections: dict[str, tuple[Identified, ...]] = {
            "character": self.characters,
            "place": self.places,
            "scene": self.scenes,
            "event": self.events,
            "timeline constraint": self.timeline_constraints,
            "relationship": self.relationships,
            "world rule": self.world_rules,
            "world lore": self.world_lore,
            "belief": self.beliefs,
            "plot thread": self.plot_threads,
            "open question": self.open_questions,
            "foreshadowing": self.foreshadowings,
            "narrative phase": self.narrative_phases,
            "plot history decision": self.plot_history,
            "story force": self.story_forces,
            "scheduled development": self.scheduled_developments,
            "disclosure": self.disclosures,
            "reader promise": self.reader_promises,
        }
        for name, items in collections.items():
            ids = [item.id for item in items]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {name} id")

        character_ids = {item.id for item in self.characters}
        place_ids = {item.id for item in self.places}
        scene_ids = {item.id for item in self.scenes}
        event_ids = {item.id for item in self.events}

        for character in self.characters:
            _require_optional_reference(character.location_id, place_ids, "character location")
        for event in self.events:
            _require_references(event.participants, character_ids, "event participant")
            _require_references(event.happens_before, event_ids, "event ordering")
            if event.id in event.happens_before:
                raise ValueError("an event cannot happen before itself")
        for relationship in self.relationships:
            _require_reference(
                relationship.source_character_id, character_ids, "relationship source"
            )
            _require_reference(
                relationship.target_character_id, character_ids, "relationship target"
            )
            if relationship.source_character_id == relationship.target_character_id:
                raise ValueError("a character relationship requires two distinct characters")
        for belief in self.beliefs:
            _require_reference(belief.character_id, character_ids, "belief character")
        for constraint in self.timeline_constraints:
            _require_reference(constraint.before_event_id, event_ids, "timeline before event")
            _require_reference(constraint.after_event_id, event_ids, "timeline after event")
            if constraint.before_event_id == constraint.after_event_id:
                raise ValueError("a timeline constraint requires two distinct events")
        for scene in self.scenes:
            _require_optional_reference(
                scene.viewpoint_character_id, character_ids, "scene viewpoint"
            )
            _require_optional_reference(scene.location_id, place_ids, "scene location")
            _require_references(scene.event_ids, event_ids, "scene event")
        for disclosure in self.disclosures:
            _require_reference(disclosure.first_revealed_in_scene_id, scene_ids, "disclosure scene")
        plot_thread_ids = {item.id for item in self.plot_threads}
        open_question_ids = {item.id for item in self.open_questions}
        reader_promise_ids = {item.id for item in self.reader_promises}
        for item in self.foreshadowings:
            _require_references(
                item.related_plot_threads, plot_thread_ids, "foreshadow plot thread"
            )
            _require_references(
                item.related_open_questions, open_question_ids, "foreshadow open question"
            )
            _require_references(
                item.related_reader_promises,
                reader_promise_ids,
                "foreshadow reader promise",
            )
        chapter_ids = {scene.chapter_id for scene in self.scenes}
        for promise in self.reader_promises:
            for update in promise.history:
                for evidence in update.evidence:
                    _require_reference(
                        evidence.chapter_id, chapter_ids, "reader promise evidence chapter"
                    )

        self._validate_scene_order()
        self._validate_timeline()
        return self

    def reader_disclosures_at(self, scene_id: UUID) -> tuple[ReaderDisclosure, ...]:
        scenes = {scene.id: scene for scene in self.scenes}
        target = scenes.get(scene_id)
        if target is None:
            raise ValueError("scene not found")
        target_position = (target.chapter_ordinal, target.ordinal)
        visible = [
            disclosure
            for disclosure in self.disclosures
            if _scene_position(scenes[disclosure.first_revealed_in_scene_id]) <= target_position
        ]
        return tuple(
            sorted(
                visible,
                key=lambda item: _scene_position(scenes[item.first_revealed_in_scene_id]),
            )
        )

    def _validate_scene_order(self) -> None:
        chapter_ids_by_ordinal: dict[int, UUID] = {}
        positions: set[tuple[int, int]] = set()
        for scene in self.scenes:
            known_chapter_id = chapter_ids_by_ordinal.setdefault(
                scene.chapter_ordinal, scene.chapter_id
            )
            if known_chapter_id != scene.chapter_id:
                raise ValueError("chapter ordinal refers to multiple chapters")
            position = (scene.chapter_ordinal, scene.ordinal)
            if position in positions:
                raise ValueError("duplicate scene position")
            positions.add(position)

    def _validate_timeline(self) -> None:
        graph: dict[UUID, set[UUID]] = {
            event.id: set(event.happens_before) for event in self.events
        }
        for constraint in self.timeline_constraints:
            graph[constraint.before_event_id].add(constraint.after_event_id)

        indegree = {event_id: 0 for event_id in graph}
        for successors in graph.values():
            for successor in successors:
                indegree[successor] += 1

        ready = deque(event_id for event_id, degree in indegree.items() if degree == 0)
        visited_count = 0
        while ready:
            event_id = ready.popleft()
            visited_count += 1
            for successor in graph[event_id]:
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    ready.append(successor)

        if visited_count != len(graph):
            raise ValueError("timeline constraints contain a cycle")



class StateDelta(FrozenModel):
    base_version: int = Field(ge=1)
    add_characters: tuple[Character, ...] = ()
    add_places: tuple[Place, ...] = ()
    add_scenes: tuple[Scene, ...] = ()
    add_events: tuple[StoryEvent, ...] = ()
    add_timeline_constraints: tuple[TimelineConstraint, ...] = ()
    add_relationships: tuple[CharacterRelationship, ...] = ()
    add_world_rules: tuple[WorldRule, ...] = ()
    add_world_lore: tuple[WorldLoreEntry, ...] = ()
    add_beliefs: tuple[CharacterBelief, ...] = ()
    add_plot_threads: tuple[PlotThread, ...] = ()
    add_open_questions: tuple[OpenQuestion, ...] = ()
    add_foreshadowings: tuple[Foreshadowing, ...] = ()
    add_narrative_phases: tuple[NarrativePhase, ...] = ()
    add_plot_history: tuple[PlotHistoryDecision, ...] = ()
    add_story_forces: tuple[StoryForce, ...] = ()
    add_scheduled_developments: tuple[ScheduledDevelopment, ...] = ()
    set_narrative_position: NarrativePosition | None = None
    add_disclosures: tuple[ReaderDisclosure, ...] = ()
    add_reader_promises: tuple[ReaderPromise, ...] = ()
    evidence: tuple[EvidenceSpan, ...] = ()

    def apply(self, state: StoryState) -> StoryState:
        return StoryState(
            characters=_merge_by_id(state.characters, self.add_characters),
            places=_merge_by_id(state.places, self.add_places),
            scenes=_merge_by_id(state.scenes, self.add_scenes),
            events=_merge_by_id(state.events, self.add_events),
            timeline_constraints=_merge_by_id(
                state.timeline_constraints, self.add_timeline_constraints
            ),
            relationships=_merge_by_id(state.relationships, self.add_relationships),
            world_rules=_merge_by_id(state.world_rules, self.add_world_rules),
            world_lore=_merge_by_id(state.world_lore, self.add_world_lore),
            beliefs=_merge_by_id(state.beliefs, self.add_beliefs),
            plot_threads=_merge_by_id(state.plot_threads, self.add_plot_threads),
            story_foundation=state.story_foundation,
            open_questions=_merge_by_id(state.open_questions, self.add_open_questions),
            foreshadowings=_merge_by_id(state.foreshadowings, self.add_foreshadowings),
            narrative_phases=_merge_by_id(state.narrative_phases, self.add_narrative_phases),
            plot_history=_merge_by_id(state.plot_history, self.add_plot_history),
            story_forces=_merge_by_id(state.story_forces, self.add_story_forces),
            scheduled_developments=_merge_by_id(
                state.scheduled_developments, self.add_scheduled_developments
            ),
            narrative_position=self.set_narrative_position or state.narrative_position,
            disclosures=_merge_by_id(state.disclosures, self.add_disclosures),
            reader_promises=_merge_by_id(state.reader_promises, self.add_reader_promises),
        )


def _merge_by_id[T: FrozenModel](current: tuple[T, ...], additions: tuple[T, ...]) -> tuple[T, ...]:
    merged = {item.id: item for item in current}  # type: ignore[attr-defined]
    merged.update({item.id: item for item in additions})  # type: ignore[attr-defined]
    return tuple(merged.values())


def _require_reference(reference: UUID, valid_ids: set[UUID], label: str) -> None:
    if reference not in valid_ids:
        raise ValueError(f"{label} references an unknown id")


def _require_optional_reference(reference: UUID | None, valid_ids: set[UUID], label: str) -> None:
    if reference is not None:
        _require_reference(reference, valid_ids, label)


def _require_references(references: tuple[UUID, ...], valid_ids: set[UUID], label: str) -> None:
    for reference in references:
        _require_reference(reference, valid_ids, label)


def _scene_position(scene: Scene) -> tuple[int, int]:
    return scene.chapter_ordinal, scene.ordinal



class StateVersion(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    number: int = Field(ge=1)
    parent_id: UUID | None = None
    rollback_of_id: UUID | None = None
    state: StoryState
    chapter_revisions: dict[UUID, UUID] = Field(default_factory=dict)



