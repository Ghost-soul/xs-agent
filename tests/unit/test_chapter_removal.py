from uuid import uuid4

from novel_writer.db.models import ChapterRecord
from novel_writer.domain.models import (
    Character,
    NarrativePosition,
    PlotThread,
    ReaderDisclosure,
    Scene,
    StateDelta,
    StoryEvent,
    StoryState,
    TimelineConstraint,
    WorldLoreEntry,
)
from novel_writer.services.workflow import _truncate_story_state, _without_chapter
from novel_writer.services.workflow_state import truncate_story_state, without_chapter


def test_workflow_facade_reexports_chapter_state_transforms() -> None:
    assert _without_chapter is without_chapter
    assert _truncate_story_state is truncate_story_state


def test_removing_chapter_prunes_only_its_derived_story_state() -> None:
    removed = ChapterRecord(id=uuid4(), project_id=uuid4(), ordinal=1, title="测试章")
    kept_chapter_id = uuid4()
    removed_event = StoryEvent(summary="测试事件")
    kept_event = StoryEvent(summary="保留事件")
    removed_scene = Scene(
        chapter_id=removed.id,
        chapter_ordinal=1,
        ordinal=1,
        event_ids=(removed_event.id,),
        summary="测试场景",
    )
    kept_scene = Scene(
        chapter_id=kept_chapter_id,
        chapter_ordinal=2,
        ordinal=1,
        event_ids=(kept_event.id,),
        summary="保留场景",
    )
    state = StoryState(
        scenes=(removed_scene, kept_scene),
        events=(removed_event, kept_event),
        timeline_constraints=(
            TimelineConstraint(
                before_event_id=removed_event.id,
                after_event_id=kept_event.id,
            ),
        ),
        disclosures=(
            ReaderDisclosure(
                fact_key="测试披露",
                statement="测试披露",
                first_revealed_in_scene_id=removed_scene.id,
            ),
        ),
        world_lore=(
            WorldLoreEntry(
                category="geography",
                name="测试地点",
                summary="只在测试章出现",
                source="extracted",
                first_seen_chapter=1,
            ),
            WorldLoreEntry(
                category="geography",
                name="作者地点",
                summary="作者固定设定",
                source="author",
                first_seen_chapter=1,
            ),
        ),
    )

    result = _without_chapter(state, removed)

    assert result.scenes == (kept_scene,)
    assert result.events == (kept_event,)
    assert result.timeline_constraints == ()
    assert result.disclosures == ()
    assert [item.name for item in result.world_lore] == ["作者地点"]


def test_truncating_story_replays_only_retained_chapter_deltas() -> None:
    character = Character(name="钱泷", current_state="尚未进入故事")
    thread = PlotThread(name="命运相遇", summary="尚未开始")
    base = StoryState(
        characters=(character,),
        plot_threads=(thread,),
        narrative_position=NarrativePosition(current_time="宴会开始前"),
    )
    first_delta = StateDelta(
        base_version=1,
        add_characters=(
            character.model_copy(
                update={"current_state": "已经来到四楼", "development_history": ("误入四楼",)}
            ),
        ),
        add_plot_threads=(
            thread.model_copy(
                update={"summary": "即将相遇", "progress": 10, "last_advanced_chapter": 1}
            ),
        ),
        set_narrative_position=NarrativePosition(current_time="宴会当晚"),
    )
    second_delta = StateDelta(
        base_version=1,
        add_characters=(
            character.model_copy(
                update={
                    "current_state": "已经共同调查流金堂",
                    "development_history": ("误入四楼", "共同调查流金堂"),
                }
            ),
        ),
        add_plot_threads=(
            thread.model_copy(
                update={"summary": "进入排水枢纽", "progress": 40, "last_advanced_chapter": 2}
            ),
        ),
        set_narrative_position=NarrativePosition(current_time="第二章结束"),
    )
    after_first = first_delta.apply(base)
    current = second_delta.apply(after_first)

    rebuilt = _truncate_story_state(base, current, [first_delta], cutoff_ordinal=2)

    assert rebuilt.characters == after_first.characters
    assert rebuilt.plot_threads == after_first.plot_threads
    assert rebuilt.narrative_position == after_first.narrative_position
