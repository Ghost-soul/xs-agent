from uuid import uuid4

import pytest
from pydantic import ValidationError

from novel_writer.domain.models import (
    Character,
    CharacterRelationship,
    EvidenceSpan,
    NarrativePosition,
    Place,
    ReaderDisclosure,
    Scene,
    StateDelta,
    StoryEvent,
    StoryState,
    TimelineConstraint,
    WorldLoreEntry,
)


def test_narrative_position_splits_model_joined_character_names() -> None:
    position = NarrativePosition(current_characters=("凌风、海伦，艾伦", "凌风"))

    assert position.current_characters == ("凌风", "海伦", "艾伦")


def test_evidence_span_requires_forward_offsets() -> None:
    with pytest.raises(ValidationError, match="evidence end must be greater than start"):
        EvidenceSpan(start=4, end=4, quote="无效")


def test_delta_applies_without_mutating_base_state() -> None:
    existing = Character(name="林遥")
    state = StoryState(characters=(existing,))
    event = StoryEvent(summary="林遥作出选择", participants=(existing.id,))
    delta = StateDelta(
        base_version=1,
        add_events=(event,),
        evidence=(EvidenceSpan(start=0, end=4, quote="作出选择"),),
    )

    updated = delta.apply(state)

    assert state.events == ()
    assert updated.characters == (existing,)
    assert updated.events == (event,)


def test_delta_upsert_is_stable_by_entity_id() -> None:
    character_id = uuid4()
    old_place = Place(name="山门")
    new_place = Place(name="城中")
    before = Character(id=character_id, name="林遥", location_id=old_place.id)
    after = Character(id=character_id, name="林遥", location_id=new_place.id)

    updated = StateDelta(
        base_version=1,
        add_characters=(after,),
        add_places=(new_place,),
    ).apply(StoryState(characters=(before,), places=(old_place,)))

    assert updated.characters == (after,)
    assert updated.places == (old_place, new_place)


def test_story_state_limits_important_character_library_to_twenty() -> None:
    StoryState(characters=tuple(Character(name=f"重要人物{i}") for i in range(20)))

    with pytest.raises(ValidationError, match="at most 20 active A-tier"):
        StoryState(characters=tuple(Character(name=f"人物{i}") for i in range(21)))

    StoryState(
        characters=tuple(Character(name=f"活跃人物{i}") for i in range(20))
        + (Character(name="已退场人物", library_status="retired"),)
    )

    StoryState(
        characters=tuple(Character(name=f"B级人物{i}", tier="B") for i in range(100))
        + tuple(Character(name=f"C级人物{i}", tier="C") for i in range(120))
    )


def test_delta_applies_structured_world_lore() -> None:
    lore = WorldLoreEntry(
        category="power_system",
        subsection="等级体系",
        name="筑基境",
        summary="筑基会重塑经脉，但不能让神魂离体。",
        risk_level="high",
        source="extracted",
        first_seen_chapter=3,
        evidence_quote="经脉重塑",
    )

    updated = StateDelta(base_version=1, add_world_lore=(lore,)).apply(StoryState())

    assert updated.world_lore == (lore,)


def test_story_state_validates_relationship_timeline_viewpoint_and_disclosure() -> None:
    chapter_id = uuid4()
    gate = Place(name="北城门")
    protagonist = Character(name="林遥", location_id=gate.id)
    companion = Character(name="顾川", location_id=gate.id)
    arrival = StoryEvent(summary="抵达城门", participants=(protagonist.id, companion.id))
    closure = StoryEvent(summary="城门关闭", participants=(protagonist.id,))
    first_scene = Scene(
        chapter_id=chapter_id,
        chapter_ordinal=1,
        ordinal=1,
        viewpoint_character_id=protagonist.id,
        location_id=gate.id,
        event_ids=(arrival.id,),
        summary="抵达",
    )
    second_scene = Scene(
        chapter_id=chapter_id,
        chapter_ordinal=1,
        ordinal=2,
        viewpoint_character_id=protagonist.id,
        location_id=gate.id,
        event_ids=(closure.id,),
        summary="关闭",
    )
    disclosure = ReaderDisclosure(
        fact_key="gate-rule",
        statement="城门日落后关闭",
        first_revealed_in_scene_id=second_scene.id,
    )

    state = StoryState(
        characters=(protagonist, companion),
        places=(gate,),
        scenes=(first_scene, second_scene),
        events=(arrival, closure),
        timeline_constraints=(
            TimelineConstraint(before_event_id=arrival.id, after_event_id=closure.id),
        ),
        relationships=(
            CharacterRelationship(
                source_character_id=protagonist.id,
                target_character_id=companion.id,
                relation_type="同伴",
            ),
        ),
        disclosures=(disclosure,),
    )

    assert state.reader_disclosures_at(first_scene.id) == ()
    assert state.reader_disclosures_at(second_scene.id) == (disclosure,)


def test_story_state_rejects_unknown_viewpoint_character() -> None:
    with pytest.raises(ValidationError, match="scene viewpoint references an unknown id"):
        StoryState(
            scenes=(
                Scene(
                    chapter_id=uuid4(),
                    chapter_ordinal=1,
                    ordinal=1,
                    viewpoint_character_id=uuid4(),
                    summary="无效视角",
                ),
            )
        )


def test_story_state_rejects_timeline_cycle() -> None:
    first = StoryEvent(summary="先发生")
    second = StoryEvent(summary="后发生", happens_before=(first.id,))

    with pytest.raises(ValidationError, match="timeline constraints contain a cycle"):
        StoryState(
            events=(first, second),
            timeline_constraints=(
                TimelineConstraint(before_event_id=first.id, after_event_id=second.id),
            ),
        )
