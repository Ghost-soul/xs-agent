"""Protect Chief's immediate continuity and one relevant historical paragraph."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from novel_writer.generation import key_history
from novel_writer.generation.content import paragraphs
from novel_writer.generation.knowledge_context import frozen_sources


def summaries(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    # Preview freezes these in newest-first order. Keep complete field values.
    result = []
    seen = set()
    for summary in snapshot["context"].get("formal_summaries", [])[:3]:
        identifier = summary.get("revision_id")
        if identifier in seen:
            continue
        seen.add(identifier)
        result.append(
            {
                k: deepcopy(summary[k])
                for k in ("revision_id", "outcome", "position", "coverage", "unresolved")
                if k in summary
            }
        )
    return result


def overlaps(item: dict[str, Any], occupied: list[dict[str, Any]]) -> bool:
    return any(
        h["source_id"] == item["source_id"]
        and max(h["start"], item["start"]) < min(h["end"], item["end"])
        for h in occupied
    )


def excerpts(
    snapshot: dict[str, Any], receipt: dict[str, Any], history: dict[str, Any]
) -> list[dict[str, Any]]:
    sources = {(s["kind"], s["source_id"]): s for s in frozen_sources(snapshot)}
    occupied = [history["previous_ending"]] if "previous_ending" in history else []
    result: list[dict[str, Any]] = []
    for hit in receipt["hits"]:
        if hit["kind"] != "chapter":
            continue
        original = sources[("chapter", hit["source_id"])]
        # Retrieve complete, non-overlapping paragraphs, not an incomplete JSON/text window.
        for paragraph in paragraphs(original["text"]):
            if paragraph["end"] <= hit["start"] or paragraph["start"] >= hit["end"]:
                continue
            item = {
                "id": f"history-{len(result) + 1}",
                "kind": "chapter",
                "source_id": hit["source_id"],
                "chapter_id": hit.get("chapter_id"),
                "ordinal": hit.get("ordinal"),
                **key_history.span(original["text"], paragraph["start"], paragraph["end"]),
            }
            if not overlaps(item, occupied):
                result.append(item)
                occupied.append(item)
    return result


def prepare(
    snapshot: dict[str, Any], receipt: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    history, _ = key_history.prepare(snapshot, None, {}, "chief", "plan")
    recent = summaries(snapshot)
    hits = excerpts(snapshot, receipt, history)
    # These cannot be crowded out by mandatory candidate dossiers. They still count
    # toward the final authorized request limit; no text is truncated to fit a target.
    history["recent_summaries"] = recent[:1]
    history["hits"] = hits[:1]
    return history, recent[1:], hits[1:]


def fill(
    snapshot: dict[str, Any],
    history: dict[str, Any],
    recent: list[dict[str, Any]],
    hits: list[dict[str, Any]],
    offer: key_history.Offer,
) -> None:
    for item in recent:
        offer(history, "recent_summaries", [*history["recent_summaries"], item])
    for item in hits:
        offer(history, "hits", [*history["hits"], item])
    if "previous_ending" in history:
        original = snapshot["context"]["recent_chapters"][-1]["body"]

        def extend(container: dict[str, Any], key: str, value: Any) -> bool:
            # Earlier retrieved paragraphs have priority; never duplicate them in a suffix.
            if overlaps(value, history["hits"]):
                return False
            return offer(container, key, value)

        key_history.extend_tail(history, "previous_ending", original, 0, extend)
