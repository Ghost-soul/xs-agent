from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from novel_writer.domain.models import (
    StoryState,
)

ForeshadowingStatus = Literal[
    "candidate",
    "active",
    "lightly_reinforced",
    "advanced",
    "ready_for_payoff",
    "fulfilled",
    "abandoned",
]

MEMORY_TRANSITIONS: dict[str, frozenset[str]] = {
    "candidate": frozenset({"active"}),
    "active": frozenset({"lightly_reinforced", "advanced"}),
    "lightly_reinforced": frozenset({"lightly_reinforced", "advanced"}),
    "advanced": frozenset({"advanced", "ready_for_payoff"}),
    "ready_for_payoff": frozenset({"ready_for_payoff", "fulfilled"}),
    "fulfilled": frozenset(),
    "abandoned": frozenset(),
}

AUTHOR_TRANSITIONS: dict[str, frozenset[str]] = {
    "candidate": frozenset({"active", "abandoned"}),
    "active": frozenset({"lightly_reinforced", "advanced", "ready_for_payoff", "abandoned"}),
    "lightly_reinforced": frozenset(
        {"lightly_reinforced", "advanced", "ready_for_payoff", "abandoned"}
    ),
    "advanced": frozenset({"advanced", "ready_for_payoff", "fulfilled", "abandoned"}),
    "ready_for_payoff": frozenset({"fulfilled", "abandoned"}),
    "fulfilled": frozenset(),
    "abandoned": frozenset(),
}


class ForeshadowingAttention(BaseModel):
    foreshadowing_id: UUID
    summary: str
    status: ForeshadowingStatus
    importance: Literal["low", "medium", "high"]
    silent_chapters: int = Field(ge=0)
    payoff_readiness: int = Field(ge=0, le=100)
    expected_payoff: str = ""
    recovery_condition: str = ""
    activation_reasons: tuple[str, ...] = ()
    suggested_action: Literal[
        "observe", "lightly_reinforce", "advance", "consider_payoff"
    ] = "observe"
    policy: Literal["non_binding_no_forced_payoff"] = "non_binding_no_forced_payoff"
    score: int = 0


class ForeshadowingAttentionResult(BaseModel):
    contract_version: Literal["foreshadowing-attention-v1"] = "foreshadowing-attention-v1"
    items: tuple[ForeshadowingAttention, ...] = ()
    open_total: int = Field(ge=0)
    backlog_attention_total: int = Field(ge=0)
    payoff_echo_candidate: dict[str, Any] | None = None


@dataclass(frozen=True)
class ForeshadowingSelectionInput:
    current_chapter: int
    focus_text: str = ""
    current_characters: frozenset[str] = frozenset()
    plot_thread_ids: frozenset[UUID] = frozenset()
    open_question_ids: frozenset[UUID] = frozenset()
    causal_foreshadowing_ids: frozenset[UUID] = frozenset()
    target_foreshadowing_ids: frozenset[UUID] = frozenset()
    author_guidance: str = ""


class ForeshadowingAttentionService:
    """Deterministically rank formal foreshadowings for every consuming role."""

    def select(
        self,
        state: StoryState,
        selection: ForeshadowingSelectionInput,
        *,
        limit: int = 8,
    ) -> ForeshadowingAttentionResult:
        ranked: list[tuple[tuple[int, int, int, int, int, str], ForeshadowingAttention]] = []
        open_items = [
            item for item in state.foreshadowings if item.status not in {"fulfilled", "abandoned"}
        ]
        for item in open_items:
            silent = max(0, selection.current_chapter - item.last_advanced_chapter)
            reasons: list[str] = []
            score = 1
            direct = item.id in selection.causal_foreshadowing_ids
            if item.status == "ready_for_payoff":
                score += 10
                reasons.append("ready_for_payoff")
            importance_score = {"high": 6, "medium": 3, "low": 0}[item.importance]
            score += importance_score
            if importance_score:
                reasons.append(f"importance_{item.importance}")
            if silent >= item.reminder_after_chapters:
                score += 6 + min(4, (silent - item.reminder_after_chapters) // 5)
                reasons.append("silence_threshold_reached")
            if set(item.related_characters).intersection(selection.current_characters):
                score += 5
                reasons.append("current_character_match")
            if set(item.related_plot_threads).intersection(selection.plot_thread_ids) or set(
                item.related_open_questions
            ).intersection(selection.open_question_ids):
                score += 5
                reasons.append("formal_thread_or_question_match")
            if direct:
                score += 8
                reasons.append("causal_handoff_match")
            if item.id in selection.target_foreshadowing_ids:
                score += 7
                reasons.append("unit_target_match")
            focus = f"{selection.focus_text}\n{selection.author_guidance}".casefold()
            if focus and (
                item.content.casefold() in focus
                or any(name.casefold() in focus for name in item.related_characters if name)
            ):
                score += 6
                reasons.append("brief_or_author_guidance_match")
            if item.payoff_readiness >= 80:
                score += 5
                reasons.append("high_payoff_readiness")
            action: Literal["observe", "lightly_reinforce", "advance", "consider_payoff"]
            if item.status == "ready_for_payoff" or item.payoff_readiness >= 80:
                action = "consider_payoff"
            elif item.status in {"lightly_reinforced", "advanced"}:
                action = "advance"
            elif silent >= item.reminder_after_chapters:
                action = "lightly_reinforce"
            else:
                action = "observe"
            attention = ForeshadowingAttention(
                foreshadowing_id=item.id,
                summary=item.content[:240],
                status=item.status,
                importance=item.importance,
                silent_chapters=silent,
                payoff_readiness=item.payoff_readiness,
                expected_payoff=item.expected_payoff[:240],
                recovery_condition=item.recovery_condition[:240],
                activation_reasons=tuple(reasons or ("open_formal_foreshadowing",)),
                suggested_action=action,
                score=score,
            )
            rank = (
                score,
                int(direct),
                {"high": 3, "medium": 2, "low": 1}[item.importance],
                item.last_advanced_chapter,
                -item.introduced_chapter,
                str(item.id),
            )
            ranked.append((rank, attention))
        ranked.sort(key=lambda value: value[0], reverse=True)
        backlog = sum(
            max(0, selection.current_chapter - item.last_advanced_chapter)
            >= item.reminder_after_chapters
            for item in open_items
        )
        return ForeshadowingAttentionResult(
            items=tuple(item for _, item in ranked[:limit]),
            open_total=len(open_items),
            backlog_attention_total=backlog,
        )


def validate_foreshadowing_transition(
    before: str,
    after: str,
    *,
    actor: Literal["memory", "author"] = "memory",
) -> None:
    if after == before and after not in {"lightly_reinforced", "advanced", "ready_for_payoff"}:
        raise ValueError(f"foreshadowing transition {before} -> {after} is not allowed")
    allowed = MEMORY_TRANSITIONS if actor == "memory" else AUTHOR_TRANSITIONS
    if after not in allowed.get(before, frozenset()):
        raise ValueError(f"foreshadowing transition {before} -> {after} is not allowed")


