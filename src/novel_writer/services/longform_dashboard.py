from __future__ import annotations

import heapq
from collections import defaultdict
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import (
    ChapterRecord,
    ChapterRevisionRecord,
    ProjectRecord,
    StateVersionRecord,
)
from novel_writer.domain.models import StoryState
from novel_writer.services.causal_continuity import (
    CausalHandoff,
    CausalPulseReport,
    build_causal_pulse_report,
    load_current_causal_manifest,
)
from novel_writer.services.errors import NotFoundError
from novel_writer.services.foreshadowing import (
    ForeshadowingAttentionService,
    ForeshadowingSelectionInput,
)
from novel_writer.services.reader_promises import assess_promise_lifecycle
from novel_writer.services.state_health import scan_state_pollution

DEFAULT_VOLUME_SIZE = 20


class LongformDashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def build(self, project_id: UUID) -> dict[str, Any]:
        project = await self.session.get(ProjectRecord, project_id)
        if project is None or project.current_version_id is None:
            raise NotFoundError("project not found")
        version = await self.session.get(StateVersionRecord, project.current_version_id)
        if version is None:
            raise NotFoundError("formal version not found")
        state = StoryState.model_validate(version.state)
        records = list(
            await self.session.scalars(
                select(ChapterRecord).where(ChapterRecord.project_id == project.id)
            )
        )
        records.sort(key=lambda item: (item.display_ordinal or item.ordinal, item.ordinal))
        formal_ids = set(version.chapter_revisions)
        revisions = {
            str(item.id): item
            for item in await self.session.scalars(
                select(ChapterRevisionRecord).where(
                    ChapterRevisionRecord.id.in_(
                        [UUID(value) for value in version.chapter_revisions.values()]
                    )
                )
            )
        }
        chapters = [
            {
                "id": str(item.id),
                "ordinal": item.display_ordinal or item.ordinal,
                "title": item.title,
                "body": revisions[version.chapter_revisions[str(item.id)]].body,
            }
            for item in records
            if str(item.id) in formal_ids
        ]
        causal_manifest, causal_diagnostic = await load_current_causal_manifest(
            self.session,
            project_id=project.id,
            version=version,
        )
        causal_handoffs = causal_manifest.handoffs if causal_manifest is not None else ()
        current_chapter = max(
            (cast(int, item["ordinal"]) for item in chapters),
            default=0,
        )
        causal_pulse = build_causal_pulse_report(
            state,
            causal_handoffs,
            current_chapter=current_chapter,
        )
        result = build_longform_dashboard(
            project.title,
            version.number,
            chapters,
            state,
            causal_handoffs=causal_handoffs,
            causal_pulse=causal_pulse,
            causal_diagnostic=causal_diagnostic,
        )
        findings = scan_state_pollution(version.state)
        result["state_health"] = {
            "healthy": not findings,
            "findings": findings,
            "version": version.number,
        }
        return result


def build_longform_dashboard(
    title: str,
    version: int,
    chapters: list[dict[str, Any]],
    state: StoryState,
    *,
    causal_handoffs: tuple[CausalHandoff, ...] = (),
    causal_pulse: CausalPulseReport | None = None,
    causal_diagnostic: str | None = None,
) -> dict[str, Any]:
    chapter_by_id = {
        str(item["id"]): {
            "id": str(item["id"]),
            "ordinal": int(item["ordinal"]),
            "title": str(item["title"]),
        }
        for item in chapters
    }
    chapter_ordinal_by_id = {str(item["id"]): int(item["ordinal"]) for item in chapters}

    def effective_scene_ordinal(scene: Any) -> int:
        return int(chapter_ordinal_by_id.get(str(scene.chapter_id), scene.chapter_ordinal))

    scene_by_event: dict[UUID, list[Any]] = defaultdict(list)
    scenes_by_chapter: dict[int, list[Any]] = defaultdict(list)
    for scene in state.scenes:
        scenes_by_chapter[effective_scene_ordinal(scene)].append(scene)
        for event_id in scene.event_ids:
            scene_by_event[event_id].append(scene)
    event_by_id = {item.id: item for item in state.events}
    character_by_id = {item.id: item for item in state.characters}

    chapter_rows: list[dict[str, Any]] = []
    for chapter in chapters:
        ordinal = int(chapter["ordinal"])
        scenes = scenes_by_chapter.get(ordinal, [])
        event_ids = {event_id for scene in scenes for event_id in scene.event_ids}
        promise_actions = sum(
            1
            for promise in state.reader_promises
            for update in promise.history
            if _effective_promise_chapter(update, chapter_ordinal_by_id) == ordinal
        )
        viewpoints = sorted(
            {
                character_by_id[item.viewpoint_character_id].name
                for item in scenes
                if item.viewpoint_character_id in character_by_id
            }
        )
        intensity = min(100, len(scenes) * 14 + len(event_ids) * 18 + promise_actions * 16)
        chapter_rows.append(
            {
                "id": str(chapter["id"]),
                "ordinal": ordinal,
                "title": str(chapter["title"]),
                "scene_count": len(scenes),
                "event_count": len(event_ids),
                "promise_actions": promise_actions,
                "viewpoints": viewpoints,
                "intensity": intensity,
                "pacing": _pacing_label(intensity),
                "progress_summary": _chapter_progress_summary(chapter, scenes, event_by_id),
            }
        )

    volumes: list[dict[str, Any]] = []
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in chapter_rows:
        grouped[(int(item["ordinal"]) - 1) // DEFAULT_VOLUME_SIZE + 1].append(item)
    for number, items in sorted(grouped.items()):
        average = round(sum(int(item["intensity"]) for item in items) / len(items))
        volumes.append(
            {
                "number": number,
                "label": f"第{number}卷",
                "start_chapter": items[0]["ordinal"],
                "end_chapter": items[-1]["ordinal"],
                "average_intensity": average,
                "chapters": items,
                "diagnosis": _volume_diagnosis(items),
            }
        )

    arcs = []
    for character in state.characters:
        appearances = sorted(
            {
                effective_scene_ordinal(scene)
                for scene in state.scenes
                if scene.viewpoint_character_id == character.id
                or any(
                    character.id in event_by_id[event_id].participants
                    for event_id in scene.event_ids
                    if event_id in event_by_id
                )
            }
        )
        events = [item.summary for item in state.events if character.id in item.participants]
        beliefs = [item.proposition for item in state.beliefs if item.character_id == character.id]
        links = [
            item
            for item in state.relationships
            if character.id in {item.source_character_id, item.target_character_id}
        ]
        arcs.append(
            {
                "id": str(character.id),
                "name": character.name,
                "description": character.description,
                "appearance_chapters": appearances,
                "event_beats": events[-6:],
                "beliefs": beliefs,
                "relationship_changes": len(links),
                "signal": (
                    "active"
                    if len(appearances) >= 3 or len(events) >= 3
                    else "emerging"
                    if appearances or events
                    else "dormant"
                ),
            }
        )

    current_chapter = max((int(item["ordinal"]) for item in chapters), default=0)
    promise_history_by_id: dict[str, list[dict[str, Any]]] = {}
    promise_chapter_overrides: dict[str, tuple[int, int]] = {}
    for promise in state.reader_promises:
        mapped_history = [
            (_effective_promise_chapter(item, chapter_ordinal_by_id), item)
            for item in promise.history
        ]
        promise_chapter_overrides[str(promise.id)] = (
            mapped_history[0][0],
            mapped_history[-1][0],
        )
        promise_history_by_id[str(promise.id)] = [
            {
                "chapter": mapped_chapter,
                "action": item.action,
                "note": item.note,
            }
            for mapped_chapter, item in mapped_history
        ]
    promise_lifecycle = assess_promise_lifecycle(
        state.reader_promises,
        current_chapter,
        chapter_overrides=promise_chapter_overrides,
        include_fulfilled=True,
    )
    foreshadow = [
        {
            **item,
            "id": item["promise_id"],
            "attention": item["action_needed"] in {"fulfill_or_advance", "reactivate"},
            "history": promise_history_by_id[str(item["promise_id"])],
        }
        for item in promise_lifecycle
    ]
    threads = [
        {
            "id": str(item.id),
            "name": item.name,
            "summary": item.summary,
            "status": item.status,
            "priority": item.priority,
            "last_advanced_chapter": item.last_advanced_chapter,
            "attention": (
                item.status == "open"
                and item.last_advanced_chapter is not None
                and current_chapter - item.last_advanced_chapter >= 5
            ),
        }
        for item in state.plot_threads
    ]
    attention_result = ForeshadowingAttentionService().select(
        state,
        ForeshadowingSelectionInput(
            current_chapter=current_chapter,
            current_characters=frozenset(state.narrative_position.current_characters),
        ),
        limit=max(8, len(state.foreshadowings)),
    )
    attention_by_id = {str(item.foreshadowing_id): item for item in attention_result.items}
    managed_foreshadowings: list[dict[str, Any]] = []
    for foreshadowing in state.foreshadowings:
        attention_item = attention_by_id.get(str(foreshadowing.id))
        silent = max(0, current_chapter - foreshadowing.last_advanced_chapter)
        lifecycle = [
            event.model_dump(mode="json") for event in foreshadowing.lifecycle_events
        ]
        if not lifecycle:
            lifecycle = [
                {
                    "action": "legacy_history",
                    "chapter_ordinal": foreshadowing.introduced_chapter,
                    "before_status": "unknown",
                    "after_status": foreshadowing.status,
                    "note": note,
                    "evidence": [],
                }
                for note in foreshadowing.history
            ]
        managed_foreshadowings.append(
            {
                **foreshadowing.model_dump(mode="json"),
                "silent_chapters": silent,
                "attention": (
                    foreshadowing.status not in {"fulfilled", "abandoned"}
                    and silent >= foreshadowing.reminder_after_chapters
                ),
                "selection_reasons": (
                    list(attention_item.activation_reasons) if attention_item else []
                ),
                "suggested_action": (
                    attention_item.suggested_action if attention_item else "observe"
                ),
                "lifecycle_timeline": lifecycle,
                "ledger_out_of_sync": _foreshadowing_ledger_out_of_sync(
                    foreshadowing, state
                ),
                "reminder": "建议轻微关联、推进、兑现或标记废弃；不强制回收。",
            }
        )
    expectation_stack = sorted(
        (
            {
                **item,
                "urgency": _promise_urgency(item),
                "next_action": item["suggestion"],
            }
            for item in foreshadow
            if item["status"] == "open"
        ),
        key=lambda item: (
            -cast(int, item["urgency"]),
            cast(float, item["memory_strength"]),
            cast(int, item["established_chapter"]),
        ),
    )
    scene_outcomes = [
        {
            "scene_id": str(scene.id),
            "chapter": effective_scene_ordinal(scene),
            "scene": scene.ordinal,
            "summary": scene.summary,
            "goal": scene.goal,
            "obstacle": scene.obstacle,
            "choice": scene.choice,
            "result": scene.result,
            "cost": scene.cost,
            "next_pressure": scene.next_pressure,
            "expectation_change": scene.expectation_change,
            "complete": all(
                (scene.goal, scene.obstacle, scene.choice, scene.result, scene.next_pressure)
            ),
        }
        for scene in sorted(
            state.scenes, key=lambda item: (effective_scene_ordinal(item), item.ordinal)
        )
    ]
    repetition = _repetition_signals(state, scene_outcomes)

    causal_events = _topological_events(state, scene_by_event)
    causal_rank = {item.id: index for index, item in enumerate(causal_events)}
    ordered_events = sorted(
        causal_events,
        key=lambda item: (
            min(
                (
                    (effective_scene_ordinal(scene), scene.ordinal)
                    for scene in scene_by_event.get(item.id, [])
                ),
                default=(10**9, 10**9),
            ),
            causal_rank[item.id],
        ),
    )
    timeline = []
    chapter_by_ordinal = {int(item["ordinal"]): item for item in chapters}
    for event in ordered_events:
        positions = sorted(
            (effective_scene_ordinal(scene), scene.ordinal)
            for scene in scene_by_event.get(event.id, [])
        )
        timeline.append(
            {
                "id": str(event.id),
                "summary": _event_display_summary(
                    event.summary,
                    chapter_by_ordinal.get(positions[0][0]) if positions else None,
                ),
                "chapter": positions[0][0] if positions else None,
                "scene": positions[0][1] if positions else None,
                "participants": [
                    character_by_id[item].name
                    for item in event.participants
                    if item in character_by_id
                ],
                "before": [str(item) for item in event.happens_before],
            }
        )

    relationships = [
        {
            "id": str(item.id),
            "source": str(item.source_character_id),
            "source_name": character_by_id[item.source_character_id].name,
            "target": str(item.target_character_id),
            "target_name": character_by_id[item.target_character_id].name,
            "type": item.relation_type,
            "description": item.description,
        }
        for item in state.relationships
    ]
    linked_ids = {
        value
        for item in state.relationships
        for value in (item.source_character_id, item.target_character_id)
    }
    warnings = []
    if chapters and not state.scenes:
        warnings.append("正式章节已有正文，但尚未形成场景级结构记录。")
    if state.characters and any(item.id not in linked_ids for item in state.characters):
        warnings.append("部分人物尚未建立正式关系记录。")
    if any(item["attention"] for item in foreshadow):
        warnings.append("有读者承诺连续五章未推进，需要在后续方向规划中关注。")
    if any(item["attention"] for item in managed_foreshadowings):
        warnings.append("有正式伏笔超过作者设定的沉默阈值，建议检查但不强制回收。")
    if scene_outcomes and any(not item["complete"] for item in scene_outcomes[-5:]):
        warnings.append("近期场景缺少完整的目标—阻力—选择—结果—下一压力记录。")
    if repetition["warnings"]:
        warnings.append("近期场景存在重复模式，请检查破局方式、场景结果或章尾牵引是否同质化。")
    return {
        "project": {"title": title, "formal_version": version},
        "overview": {
            "chapters": len(chapters),
            "volumes": len(volumes),
            "characters": len(state.characters),
            "open_promises": sum(1 for item in state.reader_promises if item.status == "open"),
            "open_threads": sum(1 for item in state.plot_threads if item.status == "open"),
            "warnings": warnings,
        },
        "volumes": volumes,
        "character_arcs": arcs,
        "foreshadow": {
            "managed": managed_foreshadowings,
            "promises": foreshadow,
            "threads": threads,
        },
        "timeline": timeline,
        "relationships": {
            "nodes": [
                {"id": str(item.id), "name": item.name, "description": item.description}
                for item in state.characters
            ],
            "edges": relationships,
        },
        "narrative_position": state.narrative_position.model_dump(mode="json"),
        "narrative_pressure": {
            "scene_outcomes": scene_outcomes[-20:],
            "expectation_stack": expectation_stack,
            "repetition": repetition,
        },
        "causal_continuity": {
            "handoffs": [item.model_dump(mode="json") for item in causal_handoffs],
            "pulse": causal_pulse.model_dump(mode="json") if causal_pulse else None,
            "source_diagnostic": causal_diagnostic,
            "advisory_only": True,
        },
        "chapter_lookup": chapter_by_id,
    }


def _foreshadowing_ledger_out_of_sync(item: Any, state: StoryState) -> bool:
    linked = {
        promise.id: promise
        for promise in state.reader_promises
        if promise.id in item.related_reader_promises
    }
    if not linked:
        return False
    if item.status == "fulfilled":
        return any(promise.status != "fulfilled" for promise in linked.values())
    return any(promise.status == "fulfilled" for promise in linked.values())


def _repetition_signals(state: StoryState, scene_outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    recent = scene_outcomes[-12:]
    corpus = " ".join(
        str(value)
        for item in recent
        for value in (item["summary"], item["choice"], item["result"], item["next_pressure"])
    )
    patterns = (
        "隐藏实力",
        "突然出现",
        "黑影",
        "震惊",
        "误会",
        "审讯",
        "追杀",
        "一拳",
        "他不知道",
    )
    phrase_counts = {item: corpus.count(item) for item in patterns if corpus.count(item) >= 2}
    choices = [str(item["choice"]).strip() for item in recent if str(item["choice"]).strip()]
    results = [str(item["result"]).strip() for item in recent if str(item["result"]).strip()]
    duplicate_choices = sorted({item for item in choices if choices.count(item) >= 2})
    duplicate_results = sorted({item for item in results if results.count(item) >= 2})
    warnings = []
    if phrase_counts:
        warnings.append("近期重复出现：" + "、".join(phrase_counts))
    if duplicate_choices:
        warnings.append("人物选择重复：" + "；".join(duplicate_choices[:3]))
    if duplicate_results:
        warnings.append("场景结果重复：" + "；".join(duplicate_results[:3]))
    return {
        "window_scenes": len(recent),
        "phrase_counts": phrase_counts,
        "duplicate_choices": duplicate_choices,
        "duplicate_results": duplicate_results,
        "warnings": warnings,
    }


def _effective_promise_chapter(update: Any, chapter_ordinal_by_id: dict[str, int]) -> int:
    for evidence in update.evidence:
        mapped = chapter_ordinal_by_id.get(str(evidence.chapter_id))
        if mapped is not None:
            return mapped
    return int(update.chapter_ordinal)


def _promise_urgency(item: dict[str, Any]) -> int:
    action_base = {
        "fulfill_or_advance": 75,
        "reactivate": 62,
        "monitor": 36,
        "none": 12,
    }[str(item["action_needed"])]
    kind_bonus = {
        "payoff": 12,
        "danger": 9,
        "growth": 7,
        "relationship": 5,
        "mystery": 4,
    }.get(str(item["kind"]), 3)
    silence_bonus = min(12, cast(int, item["silent_chapters"]) * 2)
    return min(100, action_base + kind_bonus + silence_bonus)


def _chapter_progress_summary(
    chapter: dict[str, Any], scenes: list[Any], event_by_id: dict[UUID, Any]
) -> str:
    event_summaries = [
        event_by_id[event_id].summary
        for scene in scenes
        for event_id in scene.event_ids
        if event_id in event_by_id
    ]
    body = str(chapter.get("body", ""))
    paragraphs = [item.strip() for item in body.splitlines() if item.strip()]
    if event_summaries and not (
        paragraphs
        and len(event_summaries) == 1
        and event_summaries[0] == paragraphs[0]
        and len(paragraphs) > 1
    ):
        return "；".join(dict.fromkeys(event_summaries))[:240]
    return (paragraphs[-1] if paragraphs else "已有正式正文，尚待结构化提取")[:240]


def _event_display_summary(summary: str, chapter: dict[str, Any] | None) -> str:
    if chapter is None:
        return summary
    body = str(chapter.get("body", ""))
    paragraphs = [item.strip() for item in body.splitlines() if item.strip()]
    if len(paragraphs) > 1 and summary == paragraphs[0]:
        return paragraphs[-1][:240]
    return summary


def _topological_events(state: StoryState, scene_by_event: dict[UUID, list[Any]]) -> list[Any]:
    events = {item.id: item for item in state.events}
    graph = {item.id: set(item.happens_before) for item in state.events}
    for item in state.timeline_constraints:
        graph[item.before_event_id].add(item.after_event_id)
    indegree = {item: 0 for item in graph}
    for targets in graph.values():
        for target in targets:
            indegree[target] += 1
    source_order = {item.id: index for index, item in enumerate(state.events)}

    def priority(event_id: UUID) -> tuple[int, int, int, str]:
        positions = [
            (scene.chapter_ordinal, scene.ordinal) for scene in scene_by_event.get(event_id, [])
        ]
        chapter, scene = min(positions, default=(10**9, 10**9))
        return chapter, scene, source_order[event_id], str(event_id)

    queue = [(*priority(item.id), item.id) for item in state.events if indegree[item.id] == 0]
    heapq.heapify(queue)
    ordered = []
    while queue:
        *_, event_id = heapq.heappop(queue)
        ordered.append(events[event_id])
        for target in graph[event_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                heapq.heappush(queue, (*priority(target), target))
    return ordered


def _pacing_label(intensity: int) -> str:
    if intensity >= 70:
        return "高压推进"
    if intensity >= 35:
        return "稳步推进"
    return "蓄势或过渡"


def _volume_diagnosis(chapters: list[dict[str, Any]]) -> str:
    if not chapters:
        return "暂无正式章节"
    intensities = [int(item["intensity"]) for item in chapters]
    if max(intensities) == 0:
        return "已有正文，但结构化节奏证据不足"
    if len(chapters) >= 3 and len(set(intensities[-3:])) == 1:
        return "最近三章强度接近，需留意节奏同质化"
    if intensities[-1] >= max(intensities):
        return "卷内压力正在上升"
    return "卷内存在起伏"
