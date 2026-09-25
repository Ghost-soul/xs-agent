"""Whole-paragraph history and continuity, sharing the v3 material budget."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from novel_writer.generation.content import paragraphs
from novel_writer.generation.knowledge_context import frozen_sources
from novel_writer.knowledge import lookup
from novel_writer.knowledge.text import source

Offer = Callable[[dict[str, Any], str, Any], bool]


def span(text: str, start: int, end: int) -> dict[str, Any]:
    return {"text": text[start:end], "start": start, "end": end}


def tail(text: str, start: int = 0) -> dict[str, Any]:
    entries = paragraphs(text[start:])
    offset = start + entries[-1]["start"] if entries else start
    return {**span(text, offset, len(text)), "complete": offset == start}


def extend_tail(container: dict[str, Any], key: str, text: str, start: int, offer: Offer) -> None:
    current = container[key]
    for entry in reversed(paragraphs(text[start : current["start"]])):
        offset = start + entry["start"]
        if current["end"] - offset > 6000:
            break
        candidate = {**current, **span(text, offset, current["end"]), "complete": offset == start}
        if not offer(container, key, candidate):
            break  # A continuous suffix, never isolated paragraphs presented as a full ending.
        current = candidate


def prepare(
    snapshot: dict[str, Any],
    body: str | None,
    reports: dict[str, Any],
    role: str,
    action: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    history: dict[str, Any] = {
        "scope": "正式历史，保留当时时点；不是本次正文证据",
        "recent_summaries": [],
        "hits": [],
    }
    continuity: dict[str, Any] = {}
    units = reports.get("role_units", [])
    if role == "writer" and action != "rewrite" and body:
        latest = units[-1] if units else None
        continuity = {
            "status": "已写但尚未正式采用；接力结果不是新的创作任务",
            "recent_prose": tail(body, latest["start"] if latest else 0),
            "unit_outcomes": [
                {k: deepcopy(u[k]) for k in ("unit", "outcome", "position", "unresolved")}
                for u in units
            ],
            "earlier_excerpts": [],
        }
        if not units:
            continuity["unit_summary_status"] = "无可用逐单元接力，未生成替代摘要"
    if role in {"chief", "writer"} and action != "rewrite" and not continuity:
        recent = snapshot["context"].get("recent_chapters", [])
        if recent:
            history["previous_ending"] = {
                "source_id": recent[-1]["revision_id"],
                **tail(recent[-1]["body"]),
            }
    return history, continuity


def fill(
    snapshot: dict[str, Any],
    body: str | None,
    reports: dict[str, Any],
    receipt: dict[str, Any],
    history: dict[str, Any],
    continuity: dict[str, Any],
    queries: list[str],
    offer: Offer,
) -> None:
    if continuity:
        units = reports.get("role_units", [])
        extend_tail(
            continuity, "recent_prose", body or "", units[-1]["start"] if units else 0, offer
        )
    if "previous_ending" in history:
        recent = snapshot["context"]["recent_chapters"][-1]
        extend_tail(history, "previous_ending", recent["body"], 0, offer)
    for summary in snapshot["context"].get("formal_summaries", [])[:3]:
        item = {
            k: deepcopy(summary[k])
            for k in ("revision_id", "outcome", "position", "coverage", "unresolved")
            if k in summary
        }
        offer(history, "recent_summaries", [*history["recent_summaries"], item])
    sources = {(s["kind"], s["source_id"]): s for s in frozen_sources(snapshot)}
    for hit in receipt["hits"]:
        if hit["kind"] != "chapter":
            continue
        original = sources[("chapter", hit["source_id"])]
        entries = [
            p
            for p in paragraphs(original["text"])
            if p["end"] > hit["start"] and p["start"] < hit["end"]
        ]
        if not entries:
            continue
        start, end = entries[0]["start"], entries[-1]["end"]
        occupied = [
            *history["hits"],
            *([history["previous_ending"]] if "previous_ending" in history else []),
        ]
        if any(
            h["source_id"] == hit["source_id"] and max(start, h["start"]) < min(end, h["end"])
            for h in occupied
        ):
            continue
        item = {
            "id": f"history-{len(history['hits']) + 1}",
            "kind": "chapter",
            "source_id": hit["source_id"],
            "chapter_id": hit.get("chapter_id"),
            "ordinal": hit.get("ordinal"),
            **span(original["text"], start, end),
        }
        offer(history, "hits", [*history["hits"], item])
    if not continuity:
        return
    units = reports.get("role_units", [])
    unit_sources = [
        source(
            "chapter",
            f"candidate-unit-{u['unit']}",
            (body or "")[u["start"] : u["end"]],
            unit=u["unit"],
        )
        for u in units[:-1]
    ]
    if not unit_sources:
        return
    by_id = {s["source_id"]: s for s in unit_sources}
    hits = lookup.prepare(
        "role-key-v3:candidate:" + reports.get("unit_chain_sha256", ""), unit_sources
    ).rank(queries, limit=8)
    for hit in hits:
        text = by_id[hit["source_id"]]["text"]
        entries = [
            p for p in paragraphs(text) if p["end"] > hit["start"] and p["start"] < hit["end"]
        ]
        if not entries:
            continue
        item = {
            "source_id": hit["source_id"],
            **span(text, entries[0]["start"], entries[-1]["end"]),
        }
        if any(
            h["source_id"] == item["source_id"]
            and max(h["start"], item["start"]) < min(h["end"], item["end"])
            for h in continuity["earlier_excerpts"]
        ):
            continue
        offer(continuity, "earlier_excerpts", [*continuity["earlier_excerpts"], item])
