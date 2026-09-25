"""New Chief input policy; all frozen v3 and earlier renderers remain unchanged."""

from __future__ import annotations

import inspect
import json
import sys
from copy import deepcopy
from typing import Any

from novel_writer.generation import (
    chief_history,
    chief_materials,
    key_context,
    key_prompts,
    knowledge_context,
    plan_capacity,
    unit_scope,
)
from novel_writer.generation.content import fingerprint, paragraphs, parse_object
from novel_writer.generation.novel import role_for
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.services.provider_profiles import ProviderProfile

POLICY = "chief-focus-v4"
CHIEF_GUIDANCE = """人物资料按决策需要展开：身份、目标、声音与行动限制用于选角和事件设计；
未展开的描写细节不表示人物没有这些特征，不据此安排与正式档案相反的反应。
current_state 保留原始完整记录，可能包含此前经过；按其中的时间、变化和最终结果理解当前条件。
最近摘要和历史片段帮助承接未完动作与约定；历史认知及旧状态不能覆盖正式开场状态。"""


def enabled(spec: GenerationSpec) -> bool:
    return spec.workflow == "novel-run-v1" and spec.context_policy == POLICY


def uses_keys(spec: GenerationSpec) -> bool:
    return enabled(spec) or key_context.enabled(spec)


def uses_roles(spec: GenerationSpec) -> bool:
    return enabled(spec) or key_context.uses_roles(spec)


def previous_spec(spec: GenerationSpec) -> GenerationSpec:
    return spec.model_copy(update={"context_policy": key_context.POLICY}) if enabled(spec) else spec


def contract_for(spec: GenerationSpec) -> str:
    base = key_context.contract_for(previous_spec(spec))
    if not enabled(spec):
        return base
    return fingerprint(
        {
            "base": base,
            "modules": [
                inspect.getsource(m)
                for m in (sys.modules[__name__], chief_materials, chief_history)
            ],
        }
    )


def amendment_contract(spec: GenerationSpec) -> str:
    return contract_for(spec) if enabled(spec) else key_context.amendment_contract(spec)


def material_target(spec: GenerationSpec) -> int:
    return chief_materials.TARGET if enabled(spec) else key_context.material_target(spec)


def fit_context(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    profile: ProviderProfile,
    latest_chapter_id: str | None,
) -> None:
    key_context.fit_context(previous_spec(spec), snapshot, profile, latest_chapter_id)


def retrieval_for(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    plan: dict[str, Any] | None,
    body: str | None,
    reports: dict[str, Any],
    author_note: str | None = None,
) -> dict[str, Any]:
    return key_context.retrieval_for(
        previous_spec(spec), snapshot, action, plan, body, reports, author_note
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
    reports = reports if reports is not None else {}
    if not enabled(spec) or role_for(action) != "chief" or action == "title":
        return key_context.render_for(
            previous_spec(spec), snapshot, action, plan, body, author_note, reports
        )
    receipt = retrieval_for(spec, snapshot, action, plan, body, reports, author_note)
    config = snapshot.get("counting", {}).get(action) or snapshot.get("counting", {}).get("chief")
    count = knowledge_context._counter(config or {"method": "utf8-byte-upper-bound"})
    material, audit = chief_materials.build(
        spec, snapshot, action, receipt, count, plan, body, reports, author_note
    )
    # Reuse the historical output schema without rendering/budgeting v3's input a
    # second time. Only the new Chief input layout and projection are versioned here.
    local = {**snapshot, "context": {**deepcopy(snapshot["context"]), **material["reference"]}}
    local["context"].update(formal_summaries=[], recent_chapters=[], relevant_history=[])
    _, old_raw = unit_scope.render_for(
        spec.model_copy(update={"context_policy": "focused-v1"}),
        local,
        action,
        plan,
        body,
        author_note,
        reports,
    )
    schema = parse_object(old_raw)["output_schema"]
    if spec.stage_mode != "longform-v1":
        plan_capacity.bound_schema(schema, 2, 4)
    task = {
        "author_direction": spec.direction,
        "author_boundaries": spec.author_boundaries,
        "author_decisions": author_note,
        "viewpoint": spec.viewpoint or "由 Chief 随事件确定，延续已有叙述方式",
        "story_foundation": snapshot["context"].get("story_foundation", {}),
    }
    if spec.relationship_scope != "genre-led":
        task.update(
            relationship_scope=spec.relationship_scope,
            relationship_character_ids=spec.relationship_character_ids,
        )
    cards = {c["id"]: c for c in snapshot.get("cards", [])}
    selection = snapshot.get("cast_selection") or {}
    payload = {
        "story_task": task,
        "narrative_design": {"selected_cards": [cards[i] for i in spec.narrative_card_ids]},
        "cast_scope": {
            "allowed_ids": [
                c["id"]
                for c in selection.get("characters", snapshot["context"].get("characters", []))
            ],
            "required_ids": selection.get("required_ids", spec.character_ids),
            "maximum_cast": 12,
        },
        "reference_boundary": {
            "source_version_id": str(spec.base_version_id),
            "opening": "本次被写作或审核正文开始前的正式起点",
            "omission_is_absence": False,
        },
        "formal_reference": material["reference"],
        "world_cards": {
            "primary": cards.get(spec.focus_card_id),
            "secondary": cards.get(spec.supporting_card_id or ""),
        },
        "knowledge_context": material["history"],
        "style_guidance": material["style_guidance"],
        "future_proposal_not_fact": snapshot.get("previous_proposal"),
        "unit_limit": spec.unit_limit,
        "output_schema": schema,
    }
    if spec.stage_mode != "longform-v1":
        payload.pop("unit_limit")
        payload["scene_count"] = "本次完成一个叙事单元，scenes 为其中二至四个连续场面"
    if action != "plan":
        payload.update(
            current_plan=plan,
            written_candidate=[{k: p[k] for k in ("id", "text")} for p in paragraphs(body or "")],
            completed_units=reports.get("completed_units", 0),
        )
    reports["key_context_selection"] = audit
    return key_prompts.CHIEF + "\n" + key_prompts.COMMON + "\n" + CHIEF_GUIDANCE, json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )
