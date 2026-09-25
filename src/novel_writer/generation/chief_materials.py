"""Chief-only projection: preserve decisions and voices, prioritize continuity."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from novel_writer.generation import chief_history, key_materials, role_materials
from novel_writer.generation.content import fingerprint
from novel_writer.generation.knowledge_context import _dependencies
from novel_writer.generation.schemas import GenerationSpec

TARGET = 12000
# All other portrayal fields (goals, competence, boundaries, conflict/stress responses,
# voice examples and anti-examples) can affect selection or decisions and remain intact.
OPTIONAL_PORTRAYAL = {"attention_bias"}


def nonempty(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: cleaned
            for k, v in value.items()
            if (cleaned := nonempty(v)) not in (None, "", [], {}, ())
        }
    if isinstance(value, list):
        return [nonempty(v) for v in value]
    return deepcopy(value)


def character(record: dict[str, Any], *, focal: bool) -> dict[str, Any]:
    result = key_materials.project(record, "chief", focal=focal)
    for key in ("library_status", "tier"):
        result.pop(key, None)
    profile = result.get("portrayal_profile", {})
    if not focal:
        for key in OPTIONAL_PORTRAYAL:
            profile.pop(key, None)
    # current_state/description can mix identity, history and present constraints.
    # Keep each original free-text value; never guess clauses or cut a string by length.
    return nonempty(result)  # type: ignore[no-any-return]


def style(context: dict[str, Any]) -> dict[str, Any]:
    result = {k: deepcopy(context[k]) for k in ("style_assets", "reference_style") if k in context}
    reference = result.get("reference_style")
    if isinstance(reference, dict):
        for key in ("profile_id", "version", "contains_reference_text"):
            reference.pop(key, None)
        for entry in reference.get("positive_contract", []):
            if isinstance(entry, dict):
                entry.pop("evidence_sample_ids", None)
    return result


def build(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    receipt: dict[str, Any],
    count: Callable[[Any], int],
    plan: dict[str, Any] | None,
    body: str | None,
    reports: dict[str, Any],
    author_note: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    state, _ = role_materials.state_at_opening(snapshot, action, reports)
    reference, active = key_materials.select_required(
        spec, snapshot, action, "chief", state, body, plan, author_note
    )
    source_people = {c["id"]: c for c in state["characters"]}

    def project_reference(value: dict[str, Any]) -> dict[str, Any]:
        projected = deepcopy(value)
        projected["characters"] = [
            character(c, focal=c["id"] in active) for c in value["characters"]
        ]
        return projected

    reference = project_reference(reference)
    history, recent, hits = chief_history.prepare(snapshot, receipt)
    material = {
        "reference": reference,
        "history": history,
        "style_guidance": style(snapshot["context"]),
    }
    target = min(TARGET, max(0, spec.input_limit // 2), reports.get("_key_material_target", TARGET))
    required_count = count(material)
    protected = {
        "recent_summaries": len(history["recent_summaries"]),
        "history_paragraphs": len(history["hits"]),
    }
    rejected = 0
    current_count = required_count

    def offer(container: dict[str, Any], key: str, value: Any) -> bool:
        nonlocal rejected, current_count
        # All offers only add optional material. Do not tokenize a large unchanged
        # required packet once per rejected candidate when it already fills the budget.
        if current_count >= target:
            rejected += 1
            return False
        old = container[key]
        container[key] = value
        proposed_count = count(material)
        if proposed_count <= target:
            current_count = proposed_count
            return True
        container[key] = old
        rejected += 1
        return False

    # Relevant history gets the remaining budget before optional candidate expression.
    chief_history.fill(snapshot, history, recent, hits, offer)
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
        proposed.setdefault(key, []).append(key_materials.project(record, "chief"))
        if key == "world_lore":
            lore = state.get("world_lore", [])
            closure = _dependencies(lore, {r["id"] for r in proposed["world_lore"]})
            proposed["world_lore"] = [
                key_materials.project(r, "chief") for r in lore if r["id"] in closure
            ]
        key_materials.close_dependencies(proposed, state, "chief", active)
        offer(material, "reference", project_reference(proposed))

    expression_added = 0
    for current in list(material["reference"]["characters"]):
        identifier = current["id"]
        if identifier in active:
            continue
        full = character(source_people[identifier], focal=False)
        for key in OPTIONAL_PORTRAYAL:
            value = source_people[identifier].get("portrayal_profile", {}).get(key)
            if value:
                full.setdefault("portrayal_profile", {})[key] = deepcopy(value)
        if full == current:
            continue
        proposed = deepcopy(material["reference"])
        proposed["characters"] = [
            full if c["id"] == identifier else c for c in proposed["characters"]
        ]
        if offer(material, "reference", proposed):
            expression_added += 1

    selected_count = sum(len(v) for v in material["reference"].values() if isinstance(v, list))
    source_count = sum(len(state.get(key, [])) for key in role_materials.ALL_COLLECTIONS)
    audit = {
        "policy": "chief-focus-v4",
        "role": "chief",
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
            or snapshot.get("counting", {}).get("chief")
            or {"method": "utf8-byte-upper-bound"}
        )["method"],
        "sections": {k: count(v) for k, v in material.items()},
        "protected_continuity": protected,
        "focal_characters": len(active),
        "candidate_expression_added": expression_added,
    }
    return material, {**audit, "sha256": fingerprint(audit)}
