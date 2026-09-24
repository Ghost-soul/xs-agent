"""Versioned role projections; full sources and historical prompts remain frozen locally."""

from __future__ import annotations

import inspect
import sys
from copy import deepcopy
from typing import Any

from novel_writer.generation import card_selection, context_budget
from novel_writer.generation.budget import input_tokens, option_for, request_for, request_preview
from novel_writer.generation.content import fingerprint, json_text, parse_object
from novel_writer.generation.context import terms
from novel_writer.generation.novel import role_for
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.services.provider_profiles import ProviderProfile

POLICY = "focused-v1"
NOTE = (
    "资料按本次角色与事件选取；省略不表示未发生、已解决或不存在。"
    "same_as 指向本次输入中已经完整给出的同一资料，不是外部链接；"
    "历史变更的时间与动作含义仍保留，变化前后的不同值不合并。"
    "人物与世界的概览不替代已提供的完整资料，不能据省略推断新事实。"
    "same_as 仅供阅读，输出事实仍按给定 schema 提供实际字段值。"
)


def enabled(spec: GenerationSpec) -> bool:
    return spec.workflow == "novel-run-v1" and spec.context_policy == POLICY


def previous_spec(spec: GenerationSpec) -> GenerationSpec:
    return spec.model_copy(update={"context_policy": "bounded-v1"}) if enabled(spec) else spec


def contract_for(spec: GenerationSpec) -> str:
    base = card_selection.contract_for(previous_spec(spec))
    return (
        fingerprint({"base": base, "focused_context": inspect.getsource(sys.modules[__name__])})
        if enabled(spec)
        else base
    )


def amendment_contract(spec: GenerationSpec) -> str:
    return contract_for(spec) if enabled(spec) else card_selection.amendment_contract(spec)


def material_target(spec: GenerationSpec) -> int:
    # Reserve room for the plan, subsequent prose and handoffs; ceilings are not targets.
    return (
        max(0, min(120000, spec.input_limit - 40000))
        if enabled(spec)
        else min(40000, spec.input_limit - 12000)
    )


def mentioned(item: dict[str, Any], text: str) -> bool:
    return any(
        isinstance(value, str) and len(value) >= 2 and value.casefold() in text.casefold()
        for value in [item.get("id"), item.get("name"), *item.get("aliases", [])]
    )


def select_reference(
    reference: dict[str, Any],
    role: str,
    query: str,
    active_ids: set[str],
    whole_stage: bool,
) -> dict[str, Any]:
    """Keep causal/relationship neighbours, full voices for active cast and global rules."""
    characters = reference.get("characters", [])
    active = active_ids | {c["id"] for c in characters if mentioned(c, query)}
    # A partner or witness may matter without appearing by name in the current unit.
    while True:
        previous = set(active)
        for relation in reference.get("relationships", []):
            ends = {relation.get("source_character_id"), relation.get("target_character_id")}
            if ends & active:
                active.update(i for i in ends if isinstance(i, str))
        if active == previous:
            break
    if role == "chief" or whole_stage or not active:
        active.update(c["id"] for c in characters)
    selected = [c for c in characters if c["id"] in active]
    deferred = [c for c in characters if c["id"] not in active]
    reference["characters"] = selected
    if deferred:
        reference["other_character_overview"] = [
            {k: c[k] for k in ("id", "name", "aliases", "current_state", "location_id") if k in c}
            for c in deferred
        ]
    if role in {"memory", "checker"}:
        # Evidence roles need identity, knowledge and state, not prose performance examples.
        reference["characters"] = [
            {
                k: v
                for k, v in c.items()
                if k
                not in {
                    "speech_style",
                    "portrayal_profile",
                    "personality",
                    "decision_style",
                    "tier",
                }
            }
            for c in selected
        ]
    anchors = query + json_text(
        {
            k: v
            for k, v in reference.items()
            if k
            in {
                "characters",
                "relationships",
                "beliefs",
                "places",
                "recent_events",
                "critical_facts",
                "related_state",
                "narrative_position",
                "formal_summaries",
            }
        }
    )
    query_terms = terms(anchors)
    lore = reference.get("world_lore", [])
    detailed = [
        entry
        for entry in lore
        if (
            entry.get("risk_level", "high") == "high"
            or entry.get("category") in {"world_structure", "power_system", "core_secret"}
            or mentioned(entry, anchors)
            or len(terms(entry.get("summary", "") + entry.get("name", "")) & query_terms) >= 3
        )
    ]
    # Follow references between selected world entries to avoid severing dependencies.
    while True:
        connected = anchors + json_text(detailed)
        more = [entry for entry in lore if entry not in detailed and mentioned(entry, connected)]
        if not more:
            break
        detailed.extend(more)
    reference["world_lore"] = [entry for entry in lore if entry in detailed]
    compact = [entry for entry in lore if entry not in detailed]
    if compact:
        reference["world_lore_overview"] = [
            {k: entry[k] for k in ("id", "category", "name", "summary") if k in entry}
            for entry in compact
        ]
    # Runtime bookkeeping stays in the saved snapshot, not the model's creative context.
    for key in ("preflight", "selection_audit", "source_state_sha256", "source_version_id"):
        reference.pop(key, None)
    return {
        "characters_full": [c["id"] for c in selected],
        "characters_overview": [c["id"] for c in deferred],
        "world_lore_full": [entry["id"] for entry in lore if entry in detailed],
        "world_lore_overview": [entry["id"] for entry in compact],
    }


def share_duplicates(payload: dict[str, Any], reference_key: str) -> int:
    reference = payload[reference_key]
    canonical: dict[str, str] = {}
    for collection, records in reference.items():
        if isinstance(records, list) and collection not in {"formal_summaries", "recent_chapters"}:
            for index, item in enumerate(records):
                if isinstance(item, dict) and item.get("id"):
                    canonical.setdefault(json_text(item), f"{reference_key}.{collection}[{index}]")
    position = reference.get("narrative_position")
    if isinstance(position, dict) and position:
        canonical[json_text(position)] = f"{reference_key}.narrative_position"
    count = 0

    def walk(value: Any, path: str) -> Any:
        nonlocal count
        if isinstance(value, dict):
            same = canonical.get(json_text(value))
            if same and same != path:
                count += 1
                return {"same_as": same}
            return {k: walk(v, f"{path}.{k}") for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(value)]
        return value

    # Keep the complete canonical references untouched, including voice and knowledge fields.
    for key in ("formal_summaries", "related_state"):
        if key in reference:
            reference[key] = walk(reference[key], f"{reference_key}.{key}")
    if payload.get("macro_diagnostic"):
        payload["macro_diagnostic"] = walk(payload["macro_diagnostic"], "macro_diagnostic")
    return count


def render_for(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    plan: dict[str, Any] | None = None,
    body: str | None = None,
    author_note: str | None = None,
    reports: dict[str, Any] | None = None,
) -> tuple[str, str]:
    system, raw = card_selection.render_for(
        previous_spec(spec),
        snapshot,
        action,
        plan,
        body,
        author_note,
        reports,
    )
    role = role_for(action)
    if not enabled(spec) or role == "reader" or action == "title":
        return system, raw
    payload = parse_object(raw)
    reference_key = next(
        (
            key
            for key in (
                "formal_reference",
                "formal_start",
                "candidate_working_reference",
                "formal_facts",
            )
            if isinstance(payload.get(key), dict)
        ),
        None,
    )
    # Editor already receives only the authorized paragraphs, scope and output schema.
    if reference_key is None:
        return system, raw
    reference = payload[reference_key]
    source_hash = fingerprint(reference)
    task = payload.get("current_task")
    active = set(task.get("character_ids", [])) if isinstance(task, dict) else set()
    query = json_text(
        {
            "direction": spec.direction,
            "boundaries": spec.author_boundaries,
            "viewpoint": spec.viewpoint,
            "task": task,
            "body": body,
            "author_note": author_note,
            "position": reference.get("narrative_position"),
        }
    )
    audit = select_reference(reference, role, query, active, action in {"rewrite", "amend"})
    summaries = reference.get("formal_summaries", [])
    for summary in summaries:
        # Keep events, changes, knowledge and temporal values; empty delta categories add nothing.
        if isinstance(summary.get("factual_changes"), dict):
            summary["factual_changes"] = {
                k: v for k, v in summary["factual_changes"].items() if v not in ([], {}, None)
            }
        for key in ("adoption_sha256", "body_sha256", "version_id", "method"):
            summary.pop(key, None)
    macro = payload.get("macro_diagnostic")
    if isinstance(macro, dict):
        selected_revisions = {s.get("revision_id") for s in summaries}
        selected_revisions.update(
            c.get("revision_id") for c in reference.get("recent_chapters", [])
        )
        macro["actual_recent_changes"] = [
            change
            for change in macro.get("actual_recent_changes", [])
            if change.get("revision_id") in selected_revisions
        ]
        for key in ("formal_sources_sha256", "formal_state_sha256", "source_version_id"):
            macro.pop(key, None)
    duplicate_count = share_duplicates(payload, reference_key)
    payload["context_selection"] = {
        "policy": POLICY,
        "source_sha256": source_hash,
        "role": role,
        "shared_objects": duplicate_count,
        **audit,
    }
    return system + "\n" + NOTE, json_text(payload)


def fit_context(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    profile: ProviderProfile,
    latest_chapter_id: str | None,
) -> None:
    if not enabled(spec):
        return context_budget.fit_context(spec, snapshot, profile, latest_chapter_id)
    context = snapshot["context"]
    related = context.get("related_state", {})
    scenes = related.get("scenes", [])
    current_scenes = [s for s in scenes if s.get("chapter_id") == latest_chapter_id]
    current_events = {i for scene in current_scenes for i in scene.get("event_ids", [])}
    if not current_events:
        current_events = {e["id"] for e in context.get("recent_events", [])}
    if not current_events and context.get("critical_facts"):
        current_events.add(context["critical_facts"][-1]["id"])
    # Retain both sides of established temporal/causal links to the current event.
    links = [
        {event["id"], *event.get("happens_before", [])}
        for event in [*context.get("critical_facts", []), *context.get("recent_events", [])]
    ]
    links.extend(
        {item.get("before_event_id"), item.get("after_event_id")}
        for item in related.get("timeline_constraints", [])
    )
    while True:
        previous_events = set(current_events)
        for link in links:
            if link & current_events:
                current_events.update(i for i in link if isinstance(i, str))
        if current_events == previous_events:
            break
    future = context.get("future_material_not_obligations", {})
    groups = [
        (context, "critical_facts", current_events),
        (related, "scenes", {s["id"] for s in current_scenes}),
        (future, "plot_threads", set()),
        (future, "open_questions", set()),
    ]
    archive = {}
    optional: list[tuple[list[dict[str, Any]], dict[str, Any], str]] = []
    for index, (parent, key, protected) in enumerate(groups):
        values = deepcopy(parent.get(key, []))
        group = f"{index}:{key}"
        archive[group] = values
        parent[key] = [item for item in values if item["id"] in protected]
        optional.extend(
            (parent[key], item, group) for item in values if item["id"] not in protected
        )
    snapshot["context_archive"] = archive
    context["history_selection_note"] = context_budget.NOTE

    def count() -> int:
        system, user = render_for(spec, snapshot, "plan")
        return input_tokens(
            request_preview(request_for(spec, profile, "plan", system, user)),
            snapshot["counting"]["chief"],
        )

    mandatory_count = count()
    target = min(
        material_target(spec),
        max(
            0,
            (option_for(profile, spec.chief_model).context_window or 0) - spec.chief_output_limit,
        ),
    )
    audit = []
    for selected, item, group in optional:
        selected.append(item)
        accepted = count() <= target
        if not accepted:
            selected.pop()
        audit.append(
            {"group": group, "id": item["id"], "sha256": fingerprint(item), "selected": accepted}
        )
    snapshot["context_budget"] = {
        "policy": POLICY,
        "target_with_future_reserve": target,
        "mandatory_input_count": mandatory_count,
        "source_sha256": fingerprint(archive),
        "optional_items": audit,
        "cards_truncated": False,
    }
