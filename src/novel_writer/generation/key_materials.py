"""Role-specific projections with soft, shared budgets and intact required facts."""

from __future__ import annotations

import re
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from novel_writer.generation import key_history, role_materials, role_queries
from novel_writer.generation.content import fingerprint, json_text, paragraphs
from novel_writer.generation.knowledge_context import _dependencies, _mentions
from novel_writer.generation.schemas import GenerationSpec

TARGETS = {
    "chief": 12000,
    "writer": 10000,
    "memory": 6000,
    "checker": 8000,
    "editor": 2000,
    "reader": 2000,
}
AUDIT_FIELDS = {"history", "lifecycle_events", "fulfilled_evidence", "development_history"}
EXPRESSION = {"speech_style", "portrayal_profile", "personality", "decision_style", "tier"}


def project(record: dict[str, Any], role: str, *, focal: bool = True) -> dict[str, Any]:
    excluded = set(AUDIT_FIELDS)
    if role in {"memory", "checker"}:
        excluded |= EXPRESSION
    elif role == "chief" and not focal:
        excluded |= {"mind_state", "tier"}
    # Free-text identity/state/constraints and all candidate voices stay whole.
    return {
        k: deepcopy(v)
        for k, v in record.items()
        if k not in excluded and v not in (None, "", [], {}, ())
    }


def identifiers(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(identifiers(v) for k, v in value.items() if k != "id"))
    if isinstance(value, list | tuple):
        return set().union(*(identifiers(v) for v in value))
    if isinstance(value, str):
        return {value, *re.findall(r"[a-zA-Z0-9_-]{2,}", value)}
    return set()


def select_required(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    role: str,
    state: dict[str, Any],
    body: str | None,
    plan: dict[str, Any] | None,
    author_note: str | None,
) -> tuple[dict[str, Any], set[str]]:
    task = (
        role_queries.current_task(action, plan) if role == "writer" and action != "rewrite" else []
    )
    tasks = task if isinstance(task, list) else [task or {}]
    query = (
        (body or "")
        if role in {"memory", "checker"} or action == "rewrite"
        else (
            json_text(task) + (body or "")[-6000:]
            if role == "writer"
            else spec.direction + spec.author_boundaries + spec.viewpoint + (author_note or "")
        )
    )
    position = state.get("narrative_position", {})
    active = {str(i) for t in tasks for i in t.get("character_ids", [])}
    active.update(c["id"] for c in state["characters"] if role_queries.mentions(c, query))
    active.update(
        c["id"]
        for c in state["characters"]
        if role_queries.mentions(c, json_text(position.get("current_characters", [])))
    )
    if role == "chief":
        active.update(
            (snapshot.get("cast_selection") or {}).get("required_ids", spec.character_ids)
        )
    if not active:
        # Chief has no new plan yet: expand the leading ranked candidates while keeping
        # every candidate's identity/voice/constraints available for its actual choice.
        # Evidence roles keep ambiguous participants rather than inventing a binding.
        candidates = snapshot["context"].get("characters", [])
        active.update(c["id"] for c in (candidates[:2] if role == "chief" else candidates))
    roster = (
        {
            c["id"]
            for c in (snapshot.get("cast_selection") or {}).get(
                "characters", snapshot["context"].get("characters", [])
            )
        }
        if role == "chief"
        else set()
    )
    selected: dict[str, Any] = {"narrative_position": deepcopy(position), "characters": []}
    for c in state["characters"]:
        if c["id"] in active | roster:
            selected["characters"].append(project(c, role, focal=c["id"] in active))
    locations = {c.get("location_id") for c in state["characters"] if c["id"] in active}
    for key in role_materials.ALL_COLLECTIONS:
        if key in {"characters", "world_rules", "world_lore"}:
            continue
        records = []
        for item in state.get(key, []):
            linked = key in {"relationships", "beliefs", "reader_promises"} and bool(
                active & identifiers(item)
            )
            if linked or item.get("id") in locations or role_queries.mentions(item, query):
                records.append(project(item, role))
        if records:
            selected[key] = records
    rules = [project(r, role) for r in state.get("world_rules", [])]
    anchors = (
        query
        + json_text(position)
        + json_text(rules)
        + json_text(
            {
                **selected,
                "characters": [c for c in selected["characters"] if c["id"] in active],
            }
        )
    )
    lore = state.get("world_lore", [])
    required = _dependencies(lore, {r["id"] for r in lore if _mentions(r, anchors)})
    selected.update(
        world_rules=rules, world_lore=[project(r, role) for r in lore if r["id"] in required]
    )
    close_dependencies(selected, state, role, active)
    return selected, active


def close_dependencies(
    selected: dict[str, Any],
    state: dict[str, Any],
    role: str,
    active: set[str],
) -> None:
    known = {
        str(r["id"]): (key, r)
        for key in role_materials.ALL_COLLECTIONS
        for r in state.get(key, [])
        if r.get("id")
    }
    while True:
        chosen = {
            r["id"]
            for key, records in selected.items()
            if isinstance(records, list)
            for r in records
            if isinstance(r, dict) and "id" in r
        }
        needed = identifiers(selected)
        for record in state.get("timeline_constraints", []):
            if chosen & identifiers(record):
                needed.add(record["id"])
        pending = (needed & known.keys()) - chosen
        if not pending:
            return
        for identifier in sorted(pending):
            key, record = known[identifier]
            selected.setdefault(key, []).append(project(record, role, focal=identifier in active))


def build(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    role: str,
    receipt: dict[str, Any],
    body: str | None,
    count: Callable[[Any], int],
    plan: dict[str, Any] | None,
    reports: dict[str, Any],
    author_note: str | None,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    state, candidate = role_materials.state_at_opening(snapshot, action, reports)
    reference, active = select_required(
        spec, snapshot, action, role, state, body, plan, author_note
    )
    history, continuity = key_history.prepare(snapshot, body, reports, role, action)
    material = {"reference": reference, "history": history, "continuity": continuity}
    if role in {"chief", "writer"}:
        material["style_guidance"] = {
            k: deepcopy(snapshot["context"][k])
            for k in ("style_assets", "reference_style")
            if k in snapshot["context"]
        }
    target = min(TARGETS[role], max(0, spec.input_limit // 2))
    target = min(target, reports.get("_key_material_target", target))
    required_count = count(material)
    rejected = 0

    def offer(container: dict[str, Any], key: str, value: Any) -> bool:
        nonlocal rejected
        old = container[key]
        container[key] = value
        if count(material) <= target:
            return True
        container[key] = old
        rejected += 1
        return False

    queries = receipt.get("queries", []) + receipt.get("query_coverage", {}).get(
        "lexical_queries", []
    )
    key_history.fill(snapshot, body, reports, receipt, history, continuity, queries, offer)
    by_key = {
        (key, r["id"]): r
        for key in role_materials.ALL_COLLECTIONS
        for r in state.get(key, [])
        if r.get("id")
    }
    for hit in receipt["hits"]:
        key, identifier = hit["kind"], hit["source_id"]
        record = by_key.get((key, identifier))
        if record is None or any(r["id"] == identifier for r in material["reference"].get(key, [])):
            continue
        proposed = deepcopy(material["reference"])
        proposed.setdefault(key, []).append(project(record, role, focal=identifier in active))
        if key == "world_lore":
            lore = state.get("world_lore", [])
            closure = _dependencies(lore, {r["id"] for r in proposed["world_lore"]})
            proposed["world_lore"] = [project(r, role) for r in lore if r["id"] in closure]
        close_dependencies(proposed, state, role, active)
        offer(material, "reference", proposed)
    reference = material["reference"]
    selected_count = sum(len(v) for v in reference.values() if isinstance(v, list))
    source_count = sum(len(state.get(key, [])) for key in role_materials.ALL_COLLECTIONS)
    audit = {
        "policy": "role-key-v3",
        "role": role,
        "soft_target": target,
        "material_count": count(material),
        "required_count": required_count,
        "required_above_target": required_count > target,
        "source_count": source_count,
        "selected_count": selected_count,
        "omitted_count": max(0, source_count - selected_count),
        "optional_over_budget": rejected,
        "source_sha256": fingerprint(state),
        "material_sha256": fingerprint(material),
        "retrieval_sha256": receipt.get("sha256"),
        "counting_method": (
            snapshot.get("counting", {}).get(action)
            or snapshot.get("counting", {}).get(role)
            or {"method": "utf8-byte-upper-bound"}
        )["method"],
        "sections": {k: count(v) for k, v in material.items()},
    }
    return material, {**audit, "sha256": fingerprint(audit)}, candidate


def editor_material(
    snapshot: dict[str, Any],
    body: str,
    scope: dict[str, Any],
    count: Callable[[Any], int],
) -> dict[str, Any]:
    entries = paragraphs(body)
    allowed = set(scope.get("paragraph_ids", []))
    positions = {i for i, p in enumerate(entries) if p["id"] in allowed}
    query = str(scope.get("instruction", "")) + "\n".join(
        p["text"] for p in entries if p["id"] in allowed
    )
    result: dict[str, Any] = {"read_only_neighbors": [], "expression_reference": {}}
    for c in snapshot["context"].get("characters", []):
        if role_queries.mentions(c, query):
            result["expression_reference"].setdefault("characters", []).append(
                {k: deepcopy(v) for k, v in c.items() if k in EXPRESSION | {"id", "name"}}
            )
    for k in ("style_assets", "reference_style"):
        if snapshot["context"].get(k):
            result["expression_reference"][k] = deepcopy(snapshot["context"][k])
    for i in sorted({n for p in positions for n in (p - 1, p + 1)}):
        if 0 <= i < len(entries) and i not in positions:
            result["read_only_neighbors"].append({k: entries[i][k] for k in ("id", "text")})
            if count(result) > TARGETS["editor"]:
                result["read_only_neighbors"].pop()
    return result
