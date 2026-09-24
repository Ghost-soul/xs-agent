"""Pure formal-state transformations used by workflow commands."""

from novel_writer.db.models import ChapterRecord
from novel_writer.domain.models import StateDelta, StoryState


def without_chapter(state: StoryState, chapter: ChapterRecord) -> StoryState:
    removed_scenes = tuple(item for item in state.scenes if item.chapter_id == chapter.id)
    removed_scene_ids = {item.id for item in removed_scenes}
    remaining_scenes = tuple(item for item in state.scenes if item.chapter_id != chapter.id)
    remaining_event_ids = {event_id for scene in remaining_scenes for event_id in scene.event_ids}
    removed_event_ids = {
        event_id for scene in removed_scenes for event_id in scene.event_ids
    } - remaining_event_ids
    remaining_events = tuple(
        item.model_copy(
            update={
                "happens_before": tuple(
                    target for target in item.happens_before if target not in removed_event_ids
                )
            }
        )
        for item in state.events
        if item.id not in removed_event_ids
    )
    remaining_promises = tuple(
        item
        for item in state.reader_promises
        if item.established_chapter != chapter.ordinal
        and all(update.chapter_ordinal != chapter.ordinal for update in item.history)
    )
    return StoryState.model_validate(
        state.model_copy(
            update={
                "scenes": remaining_scenes,
                "events": remaining_events,
                "timeline_constraints": tuple(
                    item
                    for item in state.timeline_constraints
                    if item.before_event_id not in removed_event_ids
                    and item.after_event_id not in removed_event_ids
                ),
                "disclosures": tuple(
                    item
                    for item in state.disclosures
                    if item.first_revealed_in_scene_id not in removed_scene_ids
                ),
                "world_lore": tuple(
                    item
                    for item in state.world_lore
                    if not (
                        item.source == "extracted" and item.first_seen_chapter == chapter.ordinal
                    )
                ),
                "reader_promises": remaining_promises,
            }
        ).model_dump(mode="json")
    )


def truncate_story_state(
    base: StoryState,
    current: StoryState,
    retained_deltas: list[StateDelta],
    cutoff_ordinal: int,
) -> StoryState:
    """Rebuild formal state immediately before one chapter and its successors."""

    rebuilt = base
    for delta in retained_deltas:
        rebuilt = delta.apply(rebuilt)

    promises = []
    for promise in current.reader_promises:
        if promise.established_chapter >= cutoff_ordinal:
            continue
        history = tuple(
            update for update in promise.history if update.chapter_ordinal < cutoff_ordinal
        )
        if not history:
            continue
        promises.append(
            promise.model_copy(
                update={
                    "history": history,
                    "last_updated_chapter": history[-1].chapter_ordinal,
                    "status": "fulfilled" if history[-1].action == "fulfilled" else "open",
                }
            )
        )
    return rebuilt.model_copy(update={"reader_promises": tuple(promises)})


def restart_state_from_current_blueprint(current: StoryState) -> StoryState:
    """Keep the editable blueprint while removing prose-bound evidence."""

    return current.model_copy(
        update={
            "scenes": (),
            "events": (),
            "timeline_constraints": (),
            "disclosures": (),
            "reader_promises": (),
        }
    )
