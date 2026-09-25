"""Bounded role material with explicit opening-state and prose-history boundaries."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from novel_writer.generation.content import digest, json_text, paragraphs, parse_object
from novel_writer.generation.knowledge_context import frozen_sources
from novel_writer.generation.role_queries import current_task, mentions
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.knowledge import lookup
from novel_writer.knowledge.text import COLLECTIONS, source
from novel_writer.services.errors import WorkflowError

STATE_LIMITS = {"chief": 60000, "writer": 40000, "memory": 30000, "checker": 40000, "editor": 8000}
WORLD_LIMITS = {"chief": 16000, "writer": 12000, "memory": 10000, "checker": 16000, "editor": 8000}
HISTORY_LIMITS = {"chief": 6000, "writer": 8000, "memory": 4000, "checker": 6000, "editor": 2000}
OTHER_COLLECTIONS = ("scenes", "plot_threads", "open_questions", "disclosures")
ALL_COLLECTIONS = (*COLLECTIONS, *OTHER_COLLECTIONS)


def state_at_opening(
    snapshot: dict[str, Any], action: str, reports: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    context = snapshot["context"]
    incremental = action.startswith(("write:", "memory:"))
    working = reports.get("role_working_state") if incremental else None
    if working is not None:
        return {
            key: deepcopy(working.get(key, {} if key == "narrative_position" else []))
            for key in (*ALL_COLLECTIONS, "narrative_position")
        }, True
    state: dict[str, Any] = {"narrative_position": deepcopy(context.get("narrative_position", {}))}
    for key in ALL_COLLECTIONS:
        records = [*context.get("related_state", {}).get(key, []), *context.get(key, [])]
        if key == "events":
            records += [*context.get("critical_facts", []), *context.get("recent_events", [])]
        state[key] = list({r["id"]: deepcopy(r) for r in records if r.get("id")}.values())
    # Full frozen formal objects hydrate structured hits, never their partial JSON windows.
    indexed = {key: {str(r.get("id")): r for r in state[key]} for key in ALL_COLLECTIONS}
    for item in frozen_sources(snapshot):
        if item["kind"] not in ALL_COLLECTIONS:
            continue
        if digest(item["text"]) != item["sha256"]:
            raise WorkflowError("知识来源内容校验失败")
        record = parse_object(item["text"])
        indexed[item["kind"]].setdefault(record.get("id", item["source_id"]), record)
    for key, by_identifier in indexed.items():
        state[key] = list(by_identifier.values())
    if incremental and reports.get("working_context"):
        working = reports["working_context"]
        for key, records in working.get("related_state", {}).items():
            if key in ALL_COLLECTIONS:
                original = {r["id"]: r for r in state[key]}
                original.update({r["id"]: deepcopy(r) for r in records})
                state[key] = list(original.values())
        for key in ALL_COLLECTIONS:
            if key in working:
                state[key] = deepcopy(working[key])
        state["narrative_position"] = deepcopy(
            working.get("narrative_position", state["narrative_position"])
        )
        return state, True
    return state, False


def _project_record(record: dict[str, Any], role: str) -> dict[str, Any]:
    # Keep complete field values. System-generated audit histories are not present state.
    excluded = {"history", "lifecycle_events", "fulfilled_evidence", "development_history"}
    if role in {"memory", "checker", "editor"}:
        excluded |= {"speech_style", "portrayal_profile", "personality", "decision_style", "tier"}
    return {k: deepcopy(v) for k, v in record.items() if k not in excluded}


def select_state(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    role: str,
    state: dict[str, Any],
    receipt: dict[str, Any],
    body: str | None,
    count: Callable[[Any], int],
    plan: dict[str, Any] | None,
) -> dict[str, Any]:
    task = current_task(action, plan) if role == "writer" and action != "rewrite" else None
    query = (
        (body or "")
        if role in {"memory", "checker"}
        else (
            json_text(task) + (body or "")[-6000:]
            if role == "writer"
            else spec.direction + spec.author_boundaries
        )
    )
    tasks = task if isinstance(task, list) else [task or {}]
    active = {i for t in tasks for i in t.get("character_ids", [])}
    active.update(c["id"] for c in state["characters"] if mentions(c, query))
    onstage = json_text(state.get("narrative_position", {}).get("current_characters", []))
    active.update(c["id"] for c in state["characters"] if mentions(c, onstage))
    if role == "chief":
        active.update(c["id"] for c in snapshot["context"].get("characters", []))
    if not active:
        active.update(c["id"] for c in snapshot["context"].get("characters", []))
    selected: dict[str, Any] = {"narrative_position": deepcopy(state.get("narrative_position", {}))}
    for key in ALL_COLLECTIONS:
        if key not in {"world_lore", "world_rules"}:
            selected[key] = []
    chosen: set[tuple[str, str]] = set()
    limit = min(STATE_LIMITS[role], spec.input_limit // 2)

    def add(key: str, record: dict[str, Any], required: bool) -> None:
        identity = (key, str(record.get("id")))
        if identity in chosen:
            return
        item = _project_record(record, role)
        selected[key].append(item)
        if count(selected) > limit:
            selected[key].pop()
            if required:
                raise WorkflowError(
                    "本次必要人物、现场或事实依赖超过角色资料预算；未截断字段、未发送请求"
                )
        else:
            chosen.add(identity)

    for record in state["characters"]:
        if record["id"] in active:
            add("characters", record, True)
    locations = {c.get("location_id") for c in selected["characters"]} - {None}
    for key in selected:
        if key in {"characters", "narrative_position"}:
            continue
        for record in state.get(key, []):
            linked = (
                bool(
                    active
                    & {
                        record.get("source_character_id"),
                        record.get("target_character_id"),
                        record.get("character_id"),
                    }
                )
                if key in {"relationships", "beliefs"}
                else False
            )
            if linked or record.get("id") in locations or mentions(record, query):
                add(key, record, True)
    # Selected relationships identify neighbours, but do not traverse the entire social graph.
    neighbours = {
        r.get(field)
        for r in selected["relationships"]
        for field in ("source_character_id", "target_character_id")
    } - active
    for record in state["characters"]:
        if record["id"] in neighbours:
            add("characters", record, False)
    by_id = {
        key: {str(r.get("id")): r for r in state.get(key, [])}
        for key in selected
        if key != "narrative_position"
    }
    for hit in receipt["hits"]:
        key = hit["kind"]
        if key in by_id and hit["source_id"] in by_id[key]:
            add(key, by_id[key][hit["source_id"]], False)
    # Keep complete causal/temporal dependencies of chosen facts, not dangling references.
    while True:
        evidence = json_text(selected)
        before = len(chosen)
        for key, records in by_id.items():
            for identifier, record in records.items():
                if identifier and identifier in evidence:
                    add(key, record, True)
        if len(chosen) == before:
            break
    return {k: v for k, v in selected.items() if v or k == "characters"}


def select_world(
    spec: GenerationSpec,
    role: str,
    state: dict[str, Any],
    reference: dict[str, Any],
    receipt: dict[str, Any],
    query: str,
    count: Callable[[Any], int],
) -> dict[str, Any]:
    from novel_writer.generation.knowledge_context import _dependencies, _mentions

    lore = state.get("world_lore", [])
    rules = state.get("world_rules", [])
    anchors = query + json_text(reference) + json_text(rules)
    required = _dependencies(lore, {r["id"] for r in lore if _mentions(r, anchors)})
    selected = {
        "world_rules": deepcopy(rules),
        "world_lore": [deepcopy(r) for r in lore if r["id"] in required],
    }
    limit = min(WORLD_LIMITS[role], spec.input_limit // 4)
    if count(selected) > limit:
        raise WorkflowError("本次必要世界规则及完整依赖超过世界资料预算；未截断条件、未发送请求")
    ids = list(dict.fromkeys(h["source_id"] for h in receipt["hits"] if h["kind"] == "world_lore"))
    for identifier in ids[:8]:
        closure = _dependencies(lore, required | {identifier})
        candidate = {**selected, "world_lore": [deepcopy(r) for r in lore if r["id"] in closure]}
        if count(candidate) <= limit:
            selected, required = candidate, closure
    return selected


def excerpt(body: str, size: int, *, start: int = 0) -> dict[str, Any]:
    offset = max(start, len(body) - size)
    boundary = body.find("\n", offset)
    if offset > start and boundary >= offset and boundary + 1 < len(body):
        offset = boundary + 1
    return {"text": body[offset:], "start": offset, "end": len(body), "complete": offset == start}


def history(
    spec: GenerationSpec,
    role: str,
    snapshot: dict[str, Any],
    receipt: dict[str, Any],
    count: Callable[[Any], int],
) -> dict[str, Any]:
    context = snapshot["context"]
    limit = min(HISTORY_LIMITS[role], spec.input_limit // 8)
    summaries = context.get("formal_summaries", [])[:3]
    packet: dict[str, Any] = {
        "scope": "正式版本的历史参考，旧状态须按当时时点理解；不是本次正文证据",
        "recent_summaries": [],
        "previous_ending": None,
        "hits": [],
        "summary_availability": {"available": len(summaries), "included": 0},
    }
    for summary in summaries:
        item = {
            k: deepcopy(summary[k])
            for k in ("revision_id", "outcome", "position", "coverage", "unresolved")
            if k in summary
        }
        packet["recent_summaries"].append(item)
        packet["summary_availability"]["included"] = len(packet["recent_summaries"])
        if count(packet) > limit * 0.6:
            packet["recent_summaries"].pop()
            packet["summary_availability"]["included"] = len(packet["recent_summaries"])
    recent = context.get("recent_chapters", [])[-1:]
    if recent:
        for size in (1200, 600, 200):
            packet["previous_ending"] = {
                "source_id": recent[0]["revision_id"],
                **excerpt(recent[0]["body"], size),
            }
            if count(packet) <= limit:
                break
        if count(packet) > limit:
            raise WorkflowError("最近章末现场超过历史资料预算，未发送请求")
    for hit in receipt["hits"]:
        if hit["kind"] != "chapter":
            continue
        ending = packet["previous_ending"]
        if ending and ending["source_id"] == hit["source_id"] and hit["start"] >= ending["start"]:
            continue
        item = {
            k: deepcopy(hit.get(k))
            for k in ("kind", "source_id", "chapter_id", "ordinal", "start", "end", "text")
        }
        item["id"] = f"history-{len(packet['hits']) + 1}"
        packet["hits"].append(item)
        if count(packet) > limit:
            packet["hits"].pop()
    return packet


def continuity(
    spec: GenerationSpec,
    body: str | None,
    reports: dict[str, Any],
    queries: list[str],
    count: Callable[[Any], int],
) -> dict[str, Any]:
    text = body or ""
    units = reports.get("role_units", [])
    limit = min(24000, spec.input_limit // 4)
    latest = units[-1] if units else None
    start = latest["start"] if latest else 0
    packet: dict[str, Any] = {
        "status": "本阶段已写但尚未正式采用；摘要是事实接力，不是新的创作任务",
        "recent_prose": excerpt(text, 6000, start=start),
        "unit_outcomes": [],
        "earlier_excerpts": [],
    }
    # A bound applies to wire size, not a target length for the story being generated.
    if count(packet) > limit * 0.65:
        packet["recent_prose"] = excerpt(text, max(200, limit // 6), start=start)
    for unit in reversed(units):
        item = {k: deepcopy(unit[k]) for k in ("unit", "outcome", "position", "unresolved")}
        packet["unit_outcomes"].append(item)
        if count(packet) > limit:
            packet["unit_outcomes"].pop()
            if unit is latest:
                raise WorkflowError("上一单元接力与结尾超过连续性预算，未截断必要事实、未发送请求")
    packet["unit_outcomes"].reverse()
    packet["omitted_unit_summaries"] = len(units) - len(packet["unit_outcomes"])
    if text and not units:
        # No verified unit boundaries: keep a bounded excerpt and explicitly say so.
        packet["unit_summary_status"] = "无可用的逐单元接力，未生成替代摘要"
    sources = [
        source(
            "chapter", f"candidate-unit-{u['unit']}", text[u["start"] : u["end"]], unit=u["unit"]
        )
        for u in units[:-1]
    ]
    if sources:
        corpus = lookup.prepare(
            "role-rag-v2:candidate:" + reports.get("unit_chain_sha256", ""), sources
        )
        for hit in corpus.rank(queries, limit=8):
            item = {k: hit[k] for k in ("source_id", "text", "start", "end")}
            packet["earlier_excerpts"].append(item)
            if count(packet) > limit:
                packet["earlier_excerpts"].pop()
    if count(packet) > limit:
        raise WorkflowError("当前接续资料超过连续性预算，未发送请求")
    return packet


def neighbors(
    body: str, scope: dict[str, Any], count: Callable[[Any], int]
) -> list[dict[str, Any]]:
    spans = paragraphs(body)
    authorized = set(scope.get("paragraph_ids", []))
    selected = {i for i, p in enumerate(spans) if p["id"] in authorized}
    result = []
    for i in sorted({j for index in selected for j in (index - 1, index + 1)}):
        if not 0 <= i < len(spans) or spans[i]["id"] in authorized:
            continue
        result.append({k: spans[i][k] for k in ("id", "text")})
        if count(result) > 4000:
            result.pop()
    return result
