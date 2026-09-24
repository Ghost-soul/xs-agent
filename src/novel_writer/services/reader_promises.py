from __future__ import annotations

from typing import Any

from novel_writer.domain.models import (
    ReaderPromise,
)

# ---------------------------------------------------------------------------
# Phase 4.2: Reader promise lifecycle – memory decay & reactivation
# ---------------------------------------------------------------------------

# Memory decay parameters.  The old character-count approximation only saw the
# most recent chapter window, so lifecycle decisions now use formal chapter ordinals.
PROMISE_MEMORY_HALF_LIFE_CHAPTERS = 3.0
REACTIVATION_THRESHOLD = 0.3
FULFILLMENT_URGENCY_CHAPTERS = 6


def compute_promise_memory_strength(
    promise: ReaderPromise,
    current_chapter: int,
    *,
    last_updated_chapter: int | None = None,
) -> float:
    """Compute reader memory strength for a promise (0.0-1.0).

    Every reader-visible promise update resets the decay clock.  Optional chapter
    overrides support historical chapter renumbering in the longform dashboard.
    """
    import math

    refresh_chapter = last_updated_chapter or promise.last_updated_chapter
    silent_chapters = max(0, current_chapter - refresh_chapter)
    strength = math.pow(0.5, silent_chapters / PROMISE_MEMORY_HALF_LIFE_CHAPTERS)
    return round(max(0.0, min(1.0, strength)), 3)


def assess_promise_lifecycle(
    promises: list[ReaderPromise] | tuple[ReaderPromise, ...],
    current_chapter: int,
    *,
    chapter_overrides: dict[str, tuple[int, int]] | None = None,
    include_fulfilled: bool = False,
) -> list[dict[str, Any]]:
    """Assess all open promises and produce lifecycle recommendations.

    Returns a list of assessment dicts suitable for injection into
    the chapter brief or creative mandate.
    """
    assessments: list[dict[str, Any]] = []

    for promise in promises:
        if promise.status != "open" and not include_fulfilled:
            continue

        established_chapter, last_updated_chapter = (chapter_overrides or {}).get(
            str(promise.id),
            (promise.established_chapter, promise.last_updated_chapter),
        )
        strength = compute_promise_memory_strength(
            promise,
            current_chapter,
            last_updated_chapter=last_updated_chapter,
        )
        silent_chapters = max(0, current_chapter - last_updated_chapter)
        chapters_held = max(0, current_chapter - established_chapter)

        assessment: dict[str, Any] = {
            "promise_id": str(promise.id),
            "kind": promise.kind,
            "summary": promise.summary,
            "status": promise.status,
            "established_chapter": established_chapter,
            "last_updated_chapter": last_updated_chapter,
            "memory_strength": strength,
            "silent_chapters": silent_chapters,
            "chapters_held": chapters_held,
        }

        if promise.status == "fulfilled":
            assessment["action_needed"] = "none"
            assessment["lifecycle_state"] = "fulfilled"
            assessment["suggestion"] = "承诺已经兑现，无需继续推进。"
        elif chapters_held >= FULFILLMENT_URGENCY_CHAPTERS:
            assessment["action_needed"] = "fulfill_or_advance"
            assessment["lifecycle_state"] = "long_held"
            assessment["suggestion"] = (
                f"「{promise.summary}」已悬置{chapters_held}章，"
                "建议推进、兑现，或由作者明确继续延后。"
            )
        elif strength < REACTIVATION_THRESHOLD:
            assessment["action_needed"] = "reactivate"
            assessment["lifecycle_state"] = "fading"
            assessment["suggestion"] = (
                f"读者对「{promise.summary}」的记忆已衰减至{strength:.0%}，"
                "建议在自然时机用事件、对话或具体物件重新建立关联。"
            )
        elif strength > 0.7:
            assessment["action_needed"] = "none"
            assessment["lifecycle_state"] = "fresh"
            assessment["suggestion"] = "刚建立或更新，当前无需机械提醒。"
        else:
            assessment["action_needed"] = "monitor"
            assessment["lifecycle_state"] = "monitor"
            assessment["suggestion"] = "记忆强度适中，可在自然时机轻微关联。"

        assessments.append(assessment)

    action_rank = {
        "fulfill_or_advance": 0,
        "reactivate": 1,
        "monitor": 2,
        "none": 3,
    }
    return sorted(
        assessments,
        key=lambda item: (
            action_rank[str(item["action_needed"])],
            float(item["memory_strength"]),
            int(item["established_chapter"]),
        ),
    )
