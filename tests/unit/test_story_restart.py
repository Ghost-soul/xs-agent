from uuid import uuid4

from novel_writer.domain.models import (
    Character,
    NarrativePosition,
    PlotThread,
    ReaderDisclosure,
    ReaderPromise,
    ReaderPromiseEvidence,
    ReaderPromiseUpdate,
    Scene,
    StoryEvent,
    StoryFoundation,
    StoryState,
    TimelineConstraint,
    WorldLoreEntry,
    WorldRule,
)
from novel_writer.services.workflow import _restart_state_from_current_blueprint
from novel_writer.services.workflow_state import restart_state_from_current_blueprint


def test_workflow_facade_reexports_restart_state_transform() -> None:
    assert _restart_state_from_current_blueprint is restart_state_from_current_blueprint


def test_restart_state_keeps_current_blueprint_and_removes_prose_evidence() -> None:
    chapter_id = uuid4()
    character = Character(name="奥古斯都", current_state="已进入皇城")
    first_event = StoryEvent(summary="抵达皇城", participants=(character.id,))
    second_event = StoryEvent(summary="进入宫门", participants=(character.id,))
    scene = Scene(
        chapter_id=chapter_id,
        chapter_ordinal=1,
        ordinal=1,
        viewpoint_character_id=character.id,
        event_ids=(first_event.id, second_event.id),
        summary="奥古斯都进入皇城",
    )
    current = StoryState(
        characters=(character,),
        scenes=(scene,),
        events=(first_event, second_event),
        timeline_constraints=(
            TimelineConstraint(
                before_event_id=first_event.id,
                after_event_id=second_event.id,
                reason="先到城外再入宫",
            ),
        ),
        world_rules=(WorldRule(statement="皇权受到旧贵族制约"),),
        world_lore=(
            WorldLoreEntry(
                category="history",
                name="奥古斯都家族",
                summary="源自宫廷宦官并在帝国内乱中上位",
            ),
        ),
        plot_threads=(PlotThread(name="帝都权力重组", progress=35),),
        story_foundation=StoryFoundation(theme="秩序与自由"),
        narrative_position=NarrativePosition(
            current_location="皇城",
            recent_major_event="奥古斯都进入宫门",
        ),
        disclosures=(
            ReaderDisclosure(
                fact_key="augustus-origin",
                statement="奥古斯都家族源自宫廷宦官",
                first_revealed_in_scene_id=scene.id,
            ),
        ),
        reader_promises=(
            ReaderPromise(
                kind="mystery",
                summary="谁在操控帝都局势",
                established_chapter=1,
                last_updated_chapter=1,
                history=(
                    ReaderPromiseUpdate(
                        action="established",
                        chapter_ordinal=1,
                        note="幕后势力首次露面",
                        evidence=(
                            ReaderPromiseEvidence(
                                chapter_id=chapter_id,
                                chapter_ordinal=1,
                                start=0,
                                end=4,
                                quote="幕后势力",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    restarted = _restart_state_from_current_blueprint(current)

    assert restarted.characters == current.characters
    assert restarted.world_rules == current.world_rules
    assert restarted.world_lore == current.world_lore
    assert restarted.plot_threads == current.plot_threads
    assert restarted.story_foundation == current.story_foundation
    assert restarted.narrative_position == current.narrative_position
    assert restarted.scenes == ()
    assert restarted.events == ()
    assert restarted.timeline_constraints == ()
    assert restarted.disclosures == ()
    assert restarted.reader_promises == ()
