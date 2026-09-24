from uuid import uuid4

from novel_writer.domain.models import (
    Character,
    CharacterRelationship,
    PlotThread,
    ReaderPromise,
    ReaderPromiseEvidence,
    ReaderPromiseUpdate,
    Scene,
    StoryEvent,
    StoryState,
    TimelineConstraint,
)
from novel_writer.services.longform_dashboard import build_longform_dashboard


def test_dashboard_connects_rhythm_arcs_promises_timeline_and_relations() -> None:
    chapter_id = uuid4()
    protagonist = Character(name="林遥", description="寻找被抹去的身份")
    guard = Character(name="守门甲士")
    arrival = StoryEvent(summary="林遥抵达城门", participants=(protagonist.id,))
    closure = StoryEvent(summary="城门关闭", participants=(protagonist.id, guard.id))
    scene = Scene(
        chapter_id=chapter_id,
        chapter_ordinal=1,
        ordinal=1,
        viewpoint_character_id=protagonist.id,
        event_ids=(arrival.id, closure.id),
        summary="林遥试图进城但城门关闭",
        goal="在闭门前进城",
        obstacle="甲士拒绝放行",
        choice="公开身份异常",
        result="城门仍然关闭",
        cost="引起甲士警觉",
        next_pressure="必须证明自己没有死亡",
        expectation_change="读者开始追问补录死亡的原因",
    )
    evidence = ReaderPromiseEvidence(
        chapter_id=chapter_id, chapter_ordinal=1, start=0, end=2, quote="身份"
    )
    promise = ReaderPromise(
        kind="mystery",
        summary="林遥为何被补录死亡",
        established_chapter=1,
        last_updated_chapter=1,
        history=(
            ReaderPromiseUpdate(
                action="established",
                chapter_ordinal=1,
                note="身份异常出现",
                evidence=(evidence,),
            ),
        ),
    )
    state = StoryState(
        characters=(protagonist, guard),
        scenes=(scene,),
        events=(arrival, closure),
        timeline_constraints=(
            TimelineConstraint(before_event_id=arrival.id, after_event_id=closure.id),
        ),
        relationships=(
            CharacterRelationship(
                source_character_id=guard.id,
                target_character_id=protagonist.id,
                relation_type="阻拦",
            ),
        ),
        plot_threads=(
            PlotThread(name="补录之谜", last_advanced_chapter=1, priority=90),
        ),
        reader_promises=(promise,),
    )
    chapters = [
        {
            "id": str(chapter_id if ordinal == 1 else uuid4()),
            "ordinal": ordinal,
            "title": f"第{ordinal}章",
        }
        for ordinal in range(1, 8)
    ]

    result = build_longform_dashboard("测试", 6, chapters, state)

    assert result["volumes"][0]["chapters"][0]["intensity"] > 0
    assert result["character_arcs"][0]["appearance_chapters"] == [1]
    assert result["foreshadow"]["promises"][0]["attention"] is True
    assert result["foreshadow"]["promises"][0]["silent_chapters"] == 6
    assert result["foreshadow"]["promises"][0]["memory_strength"] == 0.25
    assert result["foreshadow"]["promises"][0]["action_needed"] == "fulfill_or_advance"
    assert result["foreshadow"]["threads"][0]["attention"] is True
    assert [item["summary"] for item in result["timeline"]] == [
        "林遥抵达城门",
        "城门关闭",
    ]
    assert result["relationships"]["edges"][0]["type"] == "阻拦"
    assert result["narrative_pressure"]["scene_outcomes"][0]["complete"] is True
    assert result["narrative_pressure"]["expectation_stack"][0]["summary"] == (
        "林遥为何被补录死亡"
    )
    assert result["narrative_pressure"]["expectation_stack"][0]["silent_chapters"] == 6


def test_dashboard_reports_missing_structured_scene_evidence() -> None:
    result = build_longform_dashboard(
        "旧稿",
        1,
        [{"id": str(uuid4()), "ordinal": 1, "title": "第一章"}],
        StoryState(),
    )

    assert "正式章节已有正文，但尚未形成场景级结构记录。" in result["overview"]["warnings"]


def test_dashboard_remaps_historical_scene_and_promise_ordinals_by_chapter_id() -> None:
    chapter_id = uuid4()
    event = StoryEvent(summary="凌风击倒魔法师")
    scene = Scene(
        chapter_id=chapter_id,
        chapter_ordinal=3,
        ordinal=1,
        event_ids=(event.id,),
        summary="旧数据曾把首章记录为第三章",
    )
    evidence = ReaderPromiseEvidence(
        chapter_id=chapter_id, chapter_ordinal=3, start=0, end=2, quote="审查"
    )
    promise = ReaderPromise(
        kind="danger",
        summary="魔法协会审查",
        established_chapter=3,
        last_updated_chapter=3,
        history=(
            ReaderPromiseUpdate(
                action="established",
                chapter_ordinal=3,
                note="审查逼近",
                evidence=(evidence,),
            ),
        ),
    )
    result = build_longform_dashboard(
        "测试",
        2,
        [{"id": str(chapter_id), "ordinal": 1, "title": "第1章", "body": "正文"}],
        StoryState(scenes=(scene,), events=(event,), reader_promises=(promise,)),
    )

    chapter = result["volumes"][0]["chapters"][0]
    assert chapter["scene_count"] == 1
    assert chapter["event_count"] == 1
    assert chapter["promise_actions"] == 1
    assert result["timeline"][0]["chapter"] == 1
    assert result["foreshadow"]["promises"][0]["established_chapter"] == 1
