"""Bound optional formal history without truncating cards or current continuity."""

from __future__ import annotations

import inspect
import sys
from copy import deepcopy
from typing import Any

from novel_writer.generation.budget import input_tokens, request_for, request_preview
from novel_writer.generation.content import fingerprint
from novel_writer.generation.intent import contract_for as previous_contract
from novel_writer.generation.intent import render_for as previous_render
from novel_writer.generation.novel import role_for
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.services.provider_profiles import ProviderProfile

NOTE = (
    "按输入预算选择旧事实、场景和未来素材；省略不等于未发生、已解决或新的待办。完整原件保留在本地。"
)


def enabled(spec: GenerationSpec) -> bool:
    return spec.workflow == "novel-run-v1" and spec.context_policy == "bounded-v1"


def contract_for(spec: GenerationSpec) -> str:
    original = previous_contract(spec)
    if not enabled(spec):
        return original
    return fingerprint(
        {"base": original, "context_budget": inspect.getsource(sys.modules[__name__])}
    )


def render_for(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    plan: dict[str, Any] | None = None,
    body: str | None = None,
    author_note: str | None = None,
    reports: dict[str, Any] | None = None,
) -> tuple[str, str]:
    reports = reports or {}
    if enabled(spec) and reports.get("working_context"):
        working = {**reports["working_context"]}
        related = {**working.get("related_state", {})}
        initial = snapshot["context"]
        event_ids = {
            e["id"] for key in ("critical_facts", "recent_events") for e in initial.get(key, [])
        }
        event_ids.update(e["id"] for e in related.get("events", []))
        working["recent_events"] = list(
            {
                e["id"]: e
                for e in [*working.get("recent_events", []), *related.get("events", [])]
                if e["id"] in event_ids
            }.values()
        )
        for key, primary in (
            ("characters", "characters"),
            ("relationships", "relationships"),
            ("beliefs", "beliefs"),
            ("events", "recent_events"),
        ):
            # Remove exact duplicate records only, preserving other changed entities.
            related[key] = [e for e in related.get(key, []) if e not in working.get(primary, [])]
        reports = {**reports, "working_context": {**working, "related_state": related}}
    if (
        enabled(spec)
        and spec.stage_mode == "longform-v1"
        and role_for(action) in {"chief", "writer"}
        and action != "rewrite"
        and reports.get("completed_units", 0) > 0
        and reports.get("working_context")
    ):
        # Full current candidate + validated handoff now carry the immediate continuity.
        # Reader and whole-stage rewrites still retain the formal preceding chapter.
        snapshot = {
            **snapshot,
            "context": {
                **snapshot["context"],
                "recent_chapters": [],
                "preceding_reference_note": (
                    "已有本阶段完整正文与完整单元接力；此前正式末章保留在本地。"
                ),
            },
        }
    return previous_render(spec, snapshot, action, plan, body, author_note, reports)


def fit_context(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    profile: ProviderProfile,
    latest_chapter_id: str | None,
) -> None:
    if not enabled(spec):
        return
    context = snapshot["context"]
    related = context.get("related_state", {})
    scenes = related.get("scenes", [])
    current_scenes = [s for s in scenes if s.get("chapter_id") == latest_chapter_id]
    current_events = {i for scene in current_scenes for i in scene.get("event_ids", [])}
    all_events = [*context.get("critical_facts", []), *context.get("recent_events", [])]
    # Empty books or imported prose may have no Scene references; keep the recent event set.
    if not current_events:
        current_events = {e["id"] for e in context.get("recent_events", [])}
        if not current_events and all_events:
            current_events.add(all_events[-1]["id"])
    future = context.get("future_material_not_obligations", {})
    groups = [(context, "critical_facts"), (related, "scenes")]
    groups.extend((future, key) for key in ("plot_threads", "open_questions"))
    originals = {
        f"{index}:{key}": deepcopy(parent.get(key, []))
        for index, (parent, key) in enumerate(groups)
    }
    optional: list[tuple[list[dict[str, Any]], dict[str, Any], str]] = []
    for index, (parent, key) in enumerate(groups):
        source = originals[f"{index}:{key}"]
        protected = (
            current_events
            if key == "critical_facts"
            else {s["id"] for s in current_scenes}
            if key == "scenes"
            else set()
        )
        selected = [item for item in source if item["id"] in protected]
        parent[key] = selected
        candidates = list(reversed(source)) if key == "scenes" else source
        optional.extend(
            (selected, item, f"{index}:{key}") for item in candidates if item["id"] not in protected
        )
    context["history_selection_note"] = NOTE
    snapshot["context_archive"] = originals
    target = max(0, min(46000, spec.input_limit - 12000))

    def count() -> int:
        system, user = render_for(spec, snapshot, "plan")
        return input_tokens(
            request_preview(request_for(spec, profile, "plan", system, user)),
            snapshot["counting"]["chief"],
        )

    mandatory_count = count()
    selected_audit = []
    for selected, item, group in optional:
        accepted = False
        if mandatory_count < target:
            selected.append(item)
            accepted = count() <= target
            if not accepted:
                selected.pop()
        selected_audit.append(
            {"group": group, "id": item["id"], "sha256": fingerprint(item), "selected": accepted}
        )
    snapshot["context_budget"] = {
        "policy": "bounded-v1",
        "target_with_future_reserve": target,
        "mandatory_input_count": mandatory_count,
        "source_sha256": fingerprint(originals),
        "optional_items": selected_audit,
        "cards_truncated": False,
    }
    # Keep the existing local selection diagnostics accurate after this second selection.
    audit = context.get("selection_audit", {})
    if "scenes" in audit:
        audit["scenes"]["selected_count"] = len(related["scenes"])
    if "events" in audit:
        audit["events"]["selected_count"] = len(context.get("critical_facts", [])) + len(
            context.get("recent_events", [])
        )
