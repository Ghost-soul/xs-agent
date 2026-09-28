"""Version dispatcher: legacy source and rendering remain byte-for-byte unchanged."""

from __future__ import annotations

import inspect
import json
import sys
from copy import deepcopy
from typing import Any, cast

from novel_writer.generation import (
    chief_context,
    craft_context,
    craft_models,
    craft_prompts,
    craft_templates,
    knowledge_context,
    prompt_templates,
    stage_scale,
)
from novel_writer.generation.content import fingerprint, parse_object
from novel_writer.generation.craft_models import CraftPlan, CraftSpec, enabled
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.generation.template_catalog import TemplateText


def contract_for(spec: GenerationSpec, snapshot: dict[str, Any] | None = None) -> str:
    if not enabled(spec):
        return prompt_templates.contract_for(spec, snapshot)
    bundle = (snapshot or {}).get("prompt_templates")
    if bundle:
        craft_templates.checked_bundle(bundle)
    return fingerprint(
        {
            "family": craft_models.POLICY,
            "base": prompt_templates.contract_for(spec),
            "modules": [
                inspect.getsource(m)
                for m in (
                    sys.modules[__name__],
                    craft_models,
                    craft_context,
                    craft_prompts,
                    craft_templates,
                    stage_scale,
                )
            ],
            "schema": CraftPlan.model_json_schema(),
            "templates": bundle,
        }
    )


def amendment_contract(spec: GenerationSpec, snapshot: dict[str, Any] | None = None) -> str:
    return (
        contract_for(spec, snapshot)
        if enabled(spec)
        else prompt_templates.amendment_contract(spec, snapshot)
    )


def raw_render(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    plan: dict[str, Any] | None = None,
    body: str | None = None,
    author_note: str | None = None,
    reports: dict[str, Any] | None = None,
) -> tuple[str, str]:
    if not enabled(spec):
        from novel_writer.generation.event_units import render_for as previous

        return previous(spec, snapshot, action, plan, body, author_note, reports)
    spec = cast(CraftSpec, spec)
    reports = reports if reports is not None else {}
    variant = prompt_templates.variant_for(action)
    protected = None
    local_reports = dict(reports)
    count = knowledge_context._counter(
        snapshot.get("counting", {}).get(action)
        or snapshot.get("counting", {}).get(variant)
        or {"method": "utf8-byte-upper-bound"}
    )
    if variant == "writer":
        protected = craft_context.continuity(snapshot, body, reports, action, plan)
        # Reserve necessary prose before allocating the optional history budget.
        target = int(reports.get("_key_material_target", 10000))
        local_reports["_key_material_target"] = max(0, target - count(protected))
    elif variant == "chief":
        local_reports["_key_material_target"] = max(
            0,
            int(reports.get("_key_material_target", 12000))
            - count(snapshot.get("craft_macro", {})),
        )
    _, raw = chief_context.render_for(
        spec, snapshot, action, plan, body, author_note, local_reports
    )
    payload = parse_object(raw)
    if "key_context_selection" in local_reports:
        reports["key_context_selection"] = local_reports["key_context_selection"]
    scale = spec.stage_scale.model_dump(mode="json")
    scale["authorized_unit_limit"] = spec.unit_limit
    scale["counting"] = "正文非空白 Unicode 字符，含标点，不含标题；不是 token"
    if variant == "chief":
        schema = CraftPlan.model_json_schema()
        low, high = stage_scale.acceptable_units(spec)
        schema["properties"]["scenes"].update(minItems=low, maxItems=high)
        if spec.stage_mode == "single-unit-v1":
            schema["properties"]["scenes"].update(minItems=2, maxItems=4)
        payload.update(
            output_schema=schema,
            stage_scale=scale,
            macro_reference=deepcopy(snapshot.get("craft_macro", {"phase_status": "unknown"})),
        )
        payload["stage_scale"]["acceptable_units"] = [low, high]
    elif variant == "writer":
        ordinal = int(action.split(":")[1]) if ":" in action else 1
        scenes = (plan or {}).get("scenes", [])
        scale.update(
            written_characters=stage_scale.characters(body or ""),
            remaining_units=max(0, len(scenes) - ordinal),
        )
        if scale["scale_mode"] == "stage-range" and scenes:
            scale["current_unit_reference"] = stage_scale.ranges(spec.stage_scale, scenes)[
                ordinal - 1
            ]
        payload.update(stage_scale=scale, continuity=protected)
        history = payload.get("knowledge_context", {})
        history.pop("previous_ending", None)
        payload["plot_execution"]["selected_narratives"] = [
            {"id": c["id"], "name": c["name"]}
            for c in snapshot.get("cards", [])
            if c["id"] in spec.narrative_card_ids
        ]
    if "key_context_selection" in reports:
        audit = reports["key_context_selection"]
        audit = {
            **audit,
            "craft_protected_count": count(protected) if protected else 0,
            "craft_payload_count": count(payload),
            "craft_payload_sha256": fingerprint(payload),
        }
        audit["sha256"] = fingerprint({k: v for k, v in audit.items() if k != "sha256"})
        reports["key_context_selection"] = audit
    return craft_prompts.SYSTEMS.get(variant, ""), json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
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
    if not enabled(spec):
        return prompt_templates.render_for(spec, snapshot, action, plan, body, author_note, reports)
    reports = reports if reports is not None else {}
    system, raw = raw_render(spec, snapshot, action, plan, body, author_note, reports)
    bundle = reports.get("prompt_templates", snapshot.get("prompt_templates"))
    if not bundle:
        return system, raw
    craft_templates.checked_bundle(bundle)
    variant = prompt_templates.variant_for(action)
    text = bundle["templates"].get(variant)
    if text is None:
        return system, raw
    payload = parse_object(raw)
    system, task, _ = craft_templates.apply_template(
        variant, TemplateText.model_validate(text), payload
    )
    reports["prompt_template_source"] = payload
    reports["prompt_template_revision"] = bundle["revision"]
    return system, task
