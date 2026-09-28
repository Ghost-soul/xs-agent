"""Frozen macro references and protected, explicitly covered continuous prose."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from novel_writer.generation import key_queries, role_queries
from novel_writer.generation.content import digest, fingerprint, json_text, paragraphs
from novel_writer.generation.craft_models import enabled
from novel_writer.generation.knowledge_context import _counter
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.knowledge.text import terms


def bind_snapshot(spec: GenerationSpec, snapshot: dict[str, Any], state: dict[str, Any]) -> None:
    if not enabled(spec):
        return
    active = [p for p in state.get("narrative_phases", []) if p.get("status") == "active"]
    current = active[0] if len(active) == 1 else None
    query = spec.direction + spec.author_boundaries + " ".join(spec.character_ids)
    query += json_text(current or {}) + json_text(state.get("narrative_position", {}))
    candidates = []
    for collection in ("story_forces", "scheduled_developments", "reader_promises", "plot_threads"):
        for item in state.get(collection, []):
            if item.get("active") is False or item.get("status") in {
                "fulfilled",
                "resolved",
                "closed",
                "abandoned",
                "cancelled",
                "triggered",
            }:
                continue
            raw = json_text(item)
            related = (
                any(i in raw for i in spec.character_ids)
                or role_queries.mentions(item, query)
                or len(terms(raw) & terms(query)) >= 2
            )
            if related:
                candidates.append(
                    {
                        "collection": collection,
                        "record": deepcopy(item),
                        "source_version_id": str(spec.base_version_id),
                        "nature": "formal-record; intentions are not events",
                        "author_origin": "unknown",
                        "binding_instruction": False,
                    }
                )
    selected: list[dict[str, Any]] = []
    count = _counter(snapshot.get("counting", {}).get("chief", {"method": "utf8-byte-upper-bound"}))
    candidates.sort(key=lambda item: -len(terms(json_text(item["record"])) & terms(query)))
    for item in candidates:
        direct = role_queries.mentions(item["record"], spec.direction + spec.author_boundaries)
        if not direct and (len(selected) >= 6 or count([*selected, item]) > 1200):
            continue
        selected.append(item)
    phases = state.get("narrative_phases", [])
    next_phase = None
    if current:
        index = next(i for i, p in enumerate(phases) if p["id"] == current["id"])
        next_phase = next((p for p in phases[index + 1 :] if p.get("status") == "pending"), None)
    formal_order = {s["revision_id"]: i for i, s in enumerate(snapshot.get("sources", []))}
    summaries = sorted(
        (
            s
            for s in snapshot["context"].get("formal_summaries", [])
            if s.get("revision_id") in formal_order
        ),
        key=lambda s: formal_order[s["revision_id"]],
    )[-3:]
    snapshot["craft_macro"] = {
        "source_version_id": str(spec.base_version_id),
        "current_phase": deepcopy(current),
        "phase_status": "known" if current else "unknown",
        "opening_position": deepcopy(state.get("narrative_position")),
        "next_phase_reference": deepcopy(next_phase),
        "actual_recent_changes": [
            {
                k: deepcopy(s.get(k))
                for k in ("revision_id", "body_sha256", "outcome", "position", "coverage")
            }
            for s in summaries
        ],
        "related_open_references": selected,
        "author_origin": "unknown",
        "future_is_fact": False,
        "interpretation": "正式存储不等于作者硬指令；目标、势力意图和预定发展尚未发生。",
    }
    snapshot["craft_macro_audit"] = {"candidates": len(candidates), "selected": len(selected)}
    recent = snapshot["context"].get("recent_chapters", [])
    if recent:
        snapshot["craft_previous_chapter"] = deepcopy(recent[-1])


def continuity(
    snapshot: dict[str, Any],
    body: str | None,
    reports: dict[str, Any],
    action: str,
    plan: dict[str, Any] | None,
) -> dict[str, Any]:
    units = reports.get("role_units", [])
    if body and units:
        last = units[-1]
        text = body[last["start"] : last["end"]]
        source = {"unit": last["unit"], "source": "validated-candidate", "offset": last["start"]}
    else:
        chapter: dict[str, Any] = snapshot.get("craft_previous_chapter") or next(
            iter(reversed(snapshot["context"].get("recent_chapters", []))), {}
        )
        text = chapter.get("body", "")
        source = {
            "source": "formal-chapter",
            "revision_id": chapter.get("revision_id"),
            "offset": 0,
        }
    start = 0
    evidence = []
    if len(text) > 12000:
        entries = paragraphs(text)
        for entry in reversed(entries):
            start = entry["start"]
            if len(text) - start >= 10000:
                break
        task = role_queries.current_task(action, plan)
        query = json_text(task)
        people = snapshot["context"].get("characters", [])
        names = [p.get("name", "") for p in people if role_queries.mentions(p, query)]
        # Whole earlier paragraphs of direct named participants remain available.
        # No invented summary or guessed pronoun resolution is used.
        evidence = [
            {"start": p["start"], "end": p["end"], "text": p["text"]}
            for p in entries
            if p["end"] <= start and any(n and n in p["text"] for n in names)
        ]
        offset = int(source["offset"])
        for change in reports.get("memory", {}).get("changes", []):
            for span in change.get("evidence", []):
                begin, end = span["start"] - offset, span["end"] - offset
                if 0 <= begin < end <= start and not any(
                    p["start"] == begin and p["end"] == end for p in evidence
                ):
                    evidence.append({"start": begin, "end": end, "text": text[begin:end]})
        evidence.sort(key=lambda p: p["start"])
    return {
        **source,
        "body_sha256": digest(text),
        "source_length": len(text),
        "recent_prose": {
            "text": text[start:],
            "start": start,
            "end": len(text),
            "complete": start == 0,
        },
        "earlier_direct_evidence": evidence,
        "unit_outcomes": deepcopy(units),
        "coverage": "full" if start == 0 else "continuous-ending-with-earlier-evidence",
        "boundary": "原文与接力相互对照；遗漏不等于未发生，未来计划不是接力事实。",
    }


def query_plan(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    plan: dict[str, Any] | None,
    body: str | None,
    reports: dict[str, Any],
    author_note: str | None = None,
) -> dict[str, Any]:
    query = key_queries.query_plan(spec, snapshot, action, plan, body, reports, author_note)
    if not enabled(spec) or not (action == "plan" or action.startswith("write")):
        return query
    items = [
        *[
            {"outcome": u.get("outcome"), "unresolved": u.get("unresolved")}
            for u in reports.get("role_units", [])[-2:]
        ],
        *snapshot.get("craft_macro", {}).get("related_open_references", []),
    ]
    causal = [piece for item in items[:6] for piece in role_queries.windows(json_text(item), 2)]
    return {
        **query,
        "queries": list(dict.fromkeys([*query["queries"][:6], *causal]))[:10],
        "lexical_queries": [*query["lexical_queries"], *causal],
        "causal_sources_sha256": fingerprint(items),
    }
